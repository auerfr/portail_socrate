#!/usr/bin/env python3
"""
Import des cotisations depuis le CSV Agora.

Usage :
    python import_cotisations.py [chemin_csv]

Par défaut : C:\\Users\\francois-regis.auer\\Downloads\\membres_cotisations.csv
"""
import asyncio
import csv
import io
import sys
import unicodedata
from decimal import Decimal
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.models.identity import Member, MembershipType
from app.models.lodge import MasonicYear
from app.models.finance import (
    ContributionConfig, ContributionTier, MemberContribution, ContributionStatus
)

DATABASE_PATH = Path(__file__).parent / "socrate_local.db"
DATABASE_URL  = f"sqlite+aiosqlite:///{DATABASE_PATH}"

CSV_PATH = Path(
    sys.argv[1] if len(sys.argv) > 1
    else r"C:\Users\francois-regis.auer\Downloads\membres_cotisations.csv"
)

# Coefficients officiels des tranches
TIER_COEFFICIENTS = {1: 0.4, 2: 0.7, 3: 1.0, 4: 1.3, 5: 1.6}
TIER_LABELS = {
    1: "Tres amenagee",
    2: "Amenagee",
    3: "Reference",
    4: "Confortable",
    5: "Tres confortable",
}


# Correspondances manuelles (prénom norm., nom norm.) → email en base
# Nécessaire quand la base contient des noms abrégés ("C...") ou orthographe différente
MANUAL_EMAIL_MAP: dict[tuple[str, str], str] = {
    ("alain", "cadona"):          "alain.cadona@pm.me",
    ("michel", "dillenschneider"): "michel.dillen54@gmail.com",
    ("nicolas", "dupont"):         "ndu5966@proton.me",
    ("alain", "faivre"):           "alain.f@amisdesocrate.fr",
    ("nicolas", "kaizer"):         "nicolas.k@amisdesocrate.fr",
    ("ti chi", "lang"):            "tichi@orange.fr",
    ("ti chi ?", "lang"):          "tichi@orange.fr",
    ("gerard", "mangenot"):        "gerardmangenot@icloud.com",
    ("krishnik", "memetaj"):       "kreshnik.m@amisdesocrate.fr",
    ("kreshnik", "memetaj"):       "kreshnik.m@amisdesocrate.fr",
    ("michel", "pollo"):           "michel.p@amisdesocrate.fr",
}


def _normalize(s: str) -> str:
    """Normalise un nom pour la comparaison : minuscules, sans accents, sans tirets."""
    s = s.strip().lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace("-", " ").replace("_", " ")
    return " ".join(s.split())


def _match_member(members: list[Member], first: str, last: str,
                   email_index: dict[str, Member]) -> Member | None:
    """Cherche un membre par nom+prénom normalisé, avec fallback sur email_index."""
    fn = _normalize(first)
    ln = _normalize(last)

    # 0. Map manuelle → email
    key = (fn, ln)
    if key in MANUAL_EMAIL_MAP:
        return email_index.get(MANUAL_EMAIL_MAP[key])

    # 1er essai : correspondance exacte prénom+nom
    exact = [m for m in members if _normalize(m.first_name) == fn and _normalize(m.last_name) == ln]
    if len(exact) == 1:
        return exact[0]

    # 2e essai : nom seul
    by_last = [m for m in members if _normalize(m.last_name) == ln]
    if len(by_last) == 1:
        return by_last[0]

    # 3e essai : prénom seul
    by_first = [m for m in members if _normalize(m.first_name) == fn]
    if len(by_first) == 1:
        return by_first[0]

    # 4e essai : premier token du prénom + première lettre du nom
    fn0 = fn.split()[0] if fn else fn
    ln0 = ln[0] if ln else ""
    partial = [m for m in members
               if _normalize(m.first_name).startswith(fn0)
               and _normalize(m.last_name).startswith(ln0)]
    if len(partial) == 1:
        return partial[0]

    return None


async def run():
    if not CSV_PATH.exists():
        print(f"Fichier introuvable : {CSV_PATH}")
        sys.exit(1)

    print(f"Lecture de {CSV_PATH}")
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    rows = [r for r in rows if any(v.strip() for v in r.values())]  # ignorer lignes vides

    engine = create_async_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        # ── Charger l'année maçonnique courante ───────────────────────────────
        r_year = await db.execute(select(MasonicYear).order_by(MasonicYear.id.desc()).limit(1))
        year = r_year.scalar_one_or_none()
        if not year:
            print("Aucune annee maconnique en base. Creez-en une depuis /finance d'abord.")
            sys.exit(1)
        print(f"Annee maconnique : {year.label} (id={year.id})")

        # ── Charger tous les membres ──────────────────────────────────────────
        r_members = await db.execute(select(Member))
        all_members = r_members.scalars().all()
        email_index = {m.email.lower(): m for m in all_members}
        print(f"Membres en base : {len(all_members)}")

        # ── Détecter les montants depuis le CSV ───────────────────────────────
        # On lit capitation nationale/régionale depuis les lignes appartenance
        appartenance_rows = [r for r in rows if r["type_membre"].strip().lower() == "appartenance"]
        nat_cap = float(appartenance_rows[0]["capitation_nationale"]) if appartenance_rows else 183.5
        reg_cap = float(appartenance_rows[0]["capitation_regionale"]) if appartenance_rows else 4.0

        # Montants par tranche depuis le CSV
        tier_amounts_from_csv: dict[str, float] = {}
        for row in rows:
            code = row["tranche_code"].strip().upper()  # T1..T5
            pa = float(row["part_associative"])
            if code not in tier_amounts_from_csv:
                tier_amounts_from_csv[code] = pa

        # T3 référence
        ref_t3 = tier_amounts_from_csv.get("T3", 167.67)

        print(f"Capitation nationale : {nat_cap}")
        print(f"Capitation regionale : {reg_cap}")
        print(f"Reference T3         : {ref_t3}")

        # ── Mettre à jour ContributionConfig ─────────────────────────────────
        r_cfg = await db.execute(
            select(ContributionConfig).where(ContributionConfig.masonic_year_id == year.id)
        )
        cfg = r_cfg.scalar_one_or_none()
        if not cfg:
            cfg = ContributionConfig(masonic_year_id=year.id)
            db.add(cfg)
        cfg.national_capitation_rate = nat_cap
        cfg.regional_capitation_rate = reg_cap
        cfg.reference_amount = ref_t3
        await db.flush()

        # ── Mettre à jour / créer les 5 tranches ─────────────────────────────
        r_tiers = await db.execute(
            select(ContributionTier).where(ContributionTier.config_id == cfg.id)
        )
        existing_tiers = {t.tier_number: t for t in r_tiers.scalars().all()}

        tier_by_number: dict[int, ContributionTier] = {}
        for num, coeff in TIER_COEFFICIENTS.items():
            # Montant : depuis CSV si disponible, sinon ref_t3 * coeff
            code = f"T{num}"
            amount = tier_amounts_from_csv.get(code, round(ref_t3 * coeff, 2))

            if num in existing_tiers:
                t = existing_tiers[num]
                t.amount = amount
                t.label = TIER_LABELS[num]
                t.coefficient = coeff
            else:
                t = ContributionTier(
                    config_id=cfg.id,
                    tier_number=num,
                    label=TIER_LABELS[num],
                    coefficient=coeff,
                    amount=amount,
                )
                db.add(t)
            tier_by_number[num] = t

        await db.flush()

        # ── Traiter chaque ligne du CSV ───────────────────────────────────────
        created = updated = skipped = unmatched = 0

        for row in rows:
            first = row["first_name"].strip()
            last  = row["last_name"].strip()
            type_m = row["type_membre"].strip().lower()
            tranche_code = row["tranche_code"].strip().upper()  # T1..T5
            pa   = float(row["part_associative"])
            cap_n = float(row["capitation_nationale"])
            cap_r = float(row["capitation_regionale"])
            total = float(row["total_cotisation"])

            # Ignorer lignes sans tranche valide
            if not tranche_code or not tranche_code.startswith("T"):
                print(f"  [skip] tranche invalide pour {first} {last}: {tranche_code}")
                skipped += 1
                continue

            tier_num = int(tranche_code[1])

            # Trouver le membre
            member = _match_member(list(all_members), first, last, email_index)
            if not member:
                print(f"  [?] Membre non trouve : {first} {last}")
                unmatched += 1
                continue

            # 1. Mettre à jour membership_type
            new_type = MembershipType.AFFILIATION if type_m == "affilie" else MembershipType.APPARTENANCE
            if member.membership_type != new_type:
                member.membership_type = new_type

            # 2. Cotisation
            tier = tier_by_number.get(tier_num)
            if not tier:
                print(f"  [skip] tranche T{tier_num} introuvable pour {first} {last}")
                skipped += 1
                continue

            capitation = cap_n + cap_r  # 0 pour affiliés

            r_contrib = await db.execute(
                select(MemberContribution).where(
                    MemberContribution.member_id == member.id,
                    MemberContribution.masonic_year_id == year.id,
                )
            )
            contrib = r_contrib.scalar_one_or_none()
            if contrib:
                contrib.tier_id = tier.id
                contrib.base_amount = pa
                contrib.capitation_amount = capitation
                contrib.total_amount = total
                # Ne pas écraser le statut si déjà payé/exempté
                if contrib.status == ContributionStatus.PENDING:
                    pass  # on garde PENDING
                updated += 1
                verb = "maj"
            else:
                contrib = MemberContribution(
                    member_id=member.id,
                    masonic_year_id=year.id,
                    tier_id=tier.id,
                    base_amount=pa,
                    capitation_amount=capitation,
                    total_amount=total,
                    status=ContributionStatus.PENDING,
                )
                db.add(contrib)
                created += 1
                verb = "cree"

            type_label = "Affilie" if new_type == MembershipType.AFFILIATION else "Appart."
            print(f"  {verb} | {type_label} | T{tier_num} | {pa:.2f}+{capitation:.2f}={total:.2f} | {member.first_name} {member.last_name}")

        await db.commit()

    await engine.dispose()

    print()
    print("=" * 60)
    print(f"  Cotisations creees   : {created}")
    print(f"  Cotisations mises a jour : {updated}")
    print(f"  Membres non trouves  : {unmatched}")
    print(f"  Lignes ignorees      : {skipped}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run())
