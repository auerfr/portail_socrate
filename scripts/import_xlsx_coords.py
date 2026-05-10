"""Met à jour téléphones et dates d'initiation des membres fondateurs
à partir du fichier "SOCRATE Liste FF et SS coordonnées.xlsx".

- Ne modifie QUE les membres déjà présents en base (les autres ont démissionné).
- Téléphone : normalisé (espaces, points retirés ; +33 → 0).
- N'écrase pas un téléphone existant si la cellule xlsx est vide.
- Date d'initiation : mise à jour si présente dans le xlsx.
"""
from __future__ import annotations
import re
import sys
import unicodedata
from datetime import datetime, date
from pathlib import Path

import openpyxl
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

# Bootstrap : imports app
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.models.identity import Member  # noqa: E402

XLSX = Path(r"C:/Users/francois-regis.auer/Downloads/SOCRATE Liste FF et SS coordonnées.xlsx")
DB_URL = "sqlite:///" + str((ROOT / "socrate_local.db").as_posix())


def _norm(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[\s\-_.]+", "", s).upper()
    return s


def _norm_phone(s: str | None) -> str | None:
    if not s:
        return None
    s = str(s).strip()
    if not s:
        return None
    s = re.sub(r"[\s.\-]", "", s)
    if s.startswith("+33"):
        s = "0" + s[3:]
    elif s.startswith("0033"):
        s = "0" + s[4:]
    return s


def _to_date(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, int):
        # Année seule (ex: 2001) → 1er janvier
        if 1900 <= v <= 2100:
            return date(v, 1, 1)
    return None


def main(dry_run: bool = False) -> None:
    wb = openpyxl.load_workbook(XLSX, data_only=True)
    ws = wb.active

    rows = list(ws.iter_rows(min_row=2, values_only=True))
    print(f"Fichier xlsx : {len(rows)} lignes utiles")

    engine = create_engine(DB_URL, future=True)
    with Session(engine) as db:
        members = db.execute(select(Member)).scalars().all()
        # Index par nom normalisé + (nom + prénom) normalisé
        by_lname: dict[str, list[Member]] = {}
        for m in members:
            by_lname.setdefault(_norm(m.last_name), []).append(m)
        print(f"Base : {len(members)} membres")

        updated = 0
        skipped_no_match = []
        for row in rows:
            r = list(row) + [None] * 12
            xnom, xprenom, xemail, xlogin, xtel, xloge, xinit, xbirth = r[:8]
            key_l = _norm(xnom)
            if not key_l:
                continue
            candidates = by_lname.get(key_l, [])
            if not candidates:
                skipped_no_match.append(f"{xnom} {xprenom}")
                continue
            # Si plusieurs (homonymes), filtrer par prénom
            target = None
            if len(candidates) == 1:
                target = candidates[0]
            else:
                kp = _norm(xprenom)
                for m in candidates:
                    if _norm(m.first_name).startswith(kp[:3]) or kp.startswith(_norm(m.first_name)[:3]):
                        target = m
                        break
                if not target:
                    target = candidates[0]

            changes = []
            new_phone = _norm_phone(xtel)
            if new_phone and new_phone != (target.phone or ""):
                changes.append(f"phone: {target.phone!r} → {new_phone!r}")
                target.phone = new_phone

            new_init = _to_date(xinit)
            if new_init and new_init != target.initiation_date:
                changes.append(f"init: {target.initiation_date} → {new_init}")
                target.initiation_date = new_init

            new_birth = _to_date(xbirth)
            if new_birth and new_birth != target.birth_date:
                changes.append(f"birth: {target.birth_date} → {new_birth}")
                target.birth_date = new_birth

            if changes:
                updated += 1
                print(f"  ✓ {target.last_name} {target.first_name} — " + " ; ".join(changes))

        if dry_run:
            print(f"\n[DRY-RUN] {updated} membres seraient mis à jour")
            db.rollback()
        else:
            db.commit()
            print(f"\n✅ {updated} membres mis à jour")

        if skipped_no_match:
            print(f"\n— Lignes xlsx sans correspondance en base ({len(skipped_no_match)}) :")
            for n in skipped_no_match:
                print(f"   · {n}")


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
