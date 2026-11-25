"""
Diagnostic en LECTURE SEULE — liste les membres sans date de naissance et/ou
sans date d'initiation renseignée (ces deux champs alimentent les stats
d'âge et d'ancienneté maçonnique de la page Statistiques de la loge, qui
les exclut silencieusement des moyennes quand ils manquent).

Ne modifie rien.

Usage :
    python scripts/diag_missing_dates.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import app.main  # noqa: F401
from app.database import AsyncSessionLocal
from app.models.identity import Member, MemberStatus
from sqlalchemy import select


async def main():
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(Member).order_by(Member.last_name, Member.first_name))
        members = r.scalars().all()

    active = [m for m in members if m.status == MemberStatus.ACTIVE]
    no_birth = [m for m in active if not m.birth_date]
    no_init = [m for m in active if not m.initiation_date]

    def fmt(m):
        civ = "S∴" if m.civility == "S" else "F∴"
        grade = m.masonic_grade.value if m.masonic_grade else "?"
        return f"  {civ} {m.last_name} {m.first_name} — {grade}"

    print(f"Membres actifs : {len(active)} (sur {len(members)} au total, tous statuts confondus)\n")

    print(f"Sans date de naissance ({len(no_birth)}) :")
    if no_birth:
        for m in no_birth:
            print(fmt(m))
    else:
        print("  (aucun)")

    print(f"\nSans date d'initiation ({len(no_init)}) :")
    if no_init:
        for m in no_init:
            print(fmt(m))
    else:
        print("  (aucun)")

    both = [m for m in active if not m.birth_date and not m.initiation_date]
    if both:
        print(f"\nSans AUCUNE des deux ({len(both)}) :")
        for m in both:
            print(fmt(m))


if __name__ == "__main__":
    asyncio.run(main())
