#!/usr/bin/env python3
"""
Import des membres depuis le CSV Agora.

Usage :
    python import_members.py [chemin_csv]

Par défaut lit : C:\\Users\\francois-regis.auer\\Downloads\\csv_agora.csv
Mot de passe attribué à chaque compte : Socrate2025!
"""
import asyncio
import csv
import html
import io
import sys
from pathlib import Path

# Forcer UTF-8 sur stdout Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))

import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.models.identity import Member, User, MasonicGrade, MemberStatus, LodgeFunction

DATABASE_PATH = Path(__file__).parent / "socrate_local.db"
DATABASE_URL  = f"sqlite+aiosqlite:///{DATABASE_PATH}"
DEFAULT_PASSWORD = "Socrate2025!"

CSV_PATH = Path(
    sys.argv[1] if len(sys.argv) > 1
    else r"C:\Users\francois-regis.auer\Downloads\csv_agora.csv"
)

# ── Mapping fonction Agora → LodgeFunction portail ────────────────────────────
FUNCTION_MAP: dict[str, LodgeFunction] = {
    "v∴ m∴":            LodgeFunction.VM,
    "v.m.":             LodgeFunction.VM,
    "vm":               LodgeFunction.VM,
    "vén∴ m∴":          LodgeFunction.VM,
    "sec:.":            LodgeFunction.SECRETAIRE,
    "sec":              LodgeFunction.SECRETAIRE,
    "secrétaire":       LodgeFunction.SECRETAIRE,
    "1er surv:.":       LodgeFunction.PREMIER_S,
    "1er surveillant":  LodgeFunction.PREMIER_S,
    "2nd surv:.":       LodgeFunction.SECOND_S,
    "2ème surv:.":      LodgeFunction.SECOND_S,
    "2e surv:.":        LodgeFunction.SECOND_S,
    "or:.":             LodgeFunction.ORATEUR,
    "orateur":          LodgeFunction.ORATEUR,
    "couv∴":            LodgeFunction.TUILEUR,
    "tuileur":          LodgeFunction.TUILEUR,
    "m de la toile":    LodgeFunction.ARCHITECTE,
    "arch∴":            LodgeFunction.ARCHITECTE,
    "m:. des cer:.":    LodgeFunction.MAITRE_CEREMONIES,
    "md c suppleante":  LodgeFunction.MAITRE_CEREMONIES,
    "m∴ des cér∴":      LodgeFunction.MAITRE_CEREMONIES,
    "exp∴":             LodgeFunction.EXPERT,
    "expert":           LodgeFunction.EXPERT,
    "hosp∴":            LodgeFunction.HOSPITALIER,
    "hospitalier":      LodgeFunction.HOSPITALIER,
    "tréso∴":           LodgeFunction.TRESORIER,
    "trésorier":        LodgeFunction.TRESORIER,
}

# Comptes système à ignorer
SKIP_EMAILS = {"vm@amisdesocrate.fr", ""}


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _civility(raw: str) -> str:
    r = raw.strip().lower()
    return "S" if r.startswith("s") else "F"


def _grade(groups: list[str]) -> MasonicGrade:
    """Déduit le grade maçonnique depuis les groupes Agora (priorité : MM > Comp > App)."""
    gl = [g.lower().replace(" ", "") for g in groups]
    if any("mm∴" in g or "mm:." in g for g in gl):
        return MasonicGrade.MAITRE
    if any("comp∴" in g or "comp:." in g for g in gl):
        return MasonicGrade.COMPAGNON
    if any("app∴" in g or "app:." in g for g in gl):
        return MasonicGrade.APPRENTI
    return MasonicGrade.MAITRE  # défaut


def _function(raw: str) -> LodgeFunction:
    key = raw.strip().lower()
    return FUNCTION_MAP.get(key, LodgeFunction.FRERE)


def _phone(tel: str, mobile: str) -> str | None:
    p = (mobile or tel).strip().replace(".", "").replace(" ", "")
    return p or None


async def run():
    if not CSV_PATH.exists():
        print(f"❌ Fichier introuvable : {CSV_PATH}")
        sys.exit(1)

    print(f"Lecture de {CSV_PATH}")
    raw = html.unescape(CSV_PATH.read_text(encoding="utf-8"))
    rows = list(csv.reader(raw.splitlines(), delimiter=";", quotechar='"'))
    data_rows = rows[1:]  # skip header

    engine = create_async_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    created = skipped = errors = 0

    async with Session() as db:
        for row in data_rows:
            # Padding pour éviter les IndexError sur lignes courtes
            while len(row) < 15:
                row.append("")

            civility_raw = row[0].strip()
            last_name    = row[1].strip()
            first_name   = row[2].strip()
            function_raw = row[4].strip()
            tel          = row[9].strip()
            mobile       = row[10].strip()
            email        = row[11].strip()
            login        = row[13].strip() or email
            groups       = [row[j].strip() for j in range(14, len(row)) if row[j].strip()]

            # ── Validation basique ────────────────────────────────────────────
            if not email or email in SKIP_EMAILS:
                skipped += 1
                continue
            if not last_name or not first_name:
                print(f"  [skip] ligne incomplète (pas de nom/prénom) : {email}")
                skipped += 1
                continue

            # ── Doublon email ─────────────────────────────────────────────────
            existing_r = await db.execute(select(Member).where(Member.email == email))
            if existing_r.scalar_one_or_none():
                print(f"  [existe déjà] {email}")
                skipped += 1
                continue

            # ── Créer le membre ───────────────────────────────────────────────
            member = Member(
                civility     = _civility(civility_raw) if civility_raw else "F",
                last_name    = last_name,
                first_name   = first_name,
                email        = email,
                phone        = _phone(tel, mobile),
                masonic_grade= _grade(groups),
                status       = MemberStatus.ACTIVE,
                lodge_function = _function(function_raw),
            )
            db.add(member)
            await db.flush()  # obtenir member.id

            # ── Créer le compte utilisateur ───────────────────────────────────
            # Vérifier que le login n'est pas déjà pris
            login_r = await db.execute(select(User).where(User.login == login))
            if login_r.scalar_one_or_none():
                login = email  # fallback email comme login

            user = User(
                member_id     = member.id,
                login         = login,
                password_hash = _hash(DEFAULT_PASSWORD),
                is_active     = True,
                is_admin      = False,
            )
            db.add(user)
            created += 1
            grade_label = {"MAITRE": "M∴", "COMPAGNON": "Comp∴", "APPRENTI": "App∴"}[member.masonic_grade.value]
            print(f"  + {member.civility}. {first_name} {last_name} <{email}> [{grade_label}] {member.lodge_function.value}")

        await db.commit()

    await engine.dispose()

    print()
    print("═" * 60)
    print(f"  OK : {created} membres crees")
    print(f"  Ignores : {skipped} (doublons ou donnees manquantes)")
    print(f"  Mot de passe par défaut : {DEFAULT_PASSWORD}")
    print("  Chaque membre peut le modifier dans son profil.")
    print("═" * 60)


if __name__ == "__main__":
    asyncio.run(run())
