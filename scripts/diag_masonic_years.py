"""
Diagnostic en LECTURE SEULE — pourquoi la stat "Loge cette année" du
dashboard (assiduité) mélange des tenues de plusieurs années maçonniques.

Affiche chaque année maçonnique (dates, is_current), le nombre de tenues
qui lui sont rattachées, et — pour l'année marquée "en cours" — le détail
utilisé par le dashboard (tenues passées, présences/absences comptées).
Ne modifie rien.

Usage :
    python scripts/diag_masonic_years.py
"""
import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import app.main  # noqa: F401
from app.database import AsyncSessionLocal
from app.models.lodge import MasonicYear
from app.models.meetings import Meeting, Attendance, AttendanceStatus
from sqlalchemy import select, func


async def main():
    today = date.today()
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(MasonicYear).order_by(MasonicYear.start_date))
        years = r.scalars().all()

        if not years:
            print("Aucune année maçonnique en base.")
            return

        current_flagged = [y for y in years if y.is_current]
        multi_current_warning = "⚠️ PLUS D'UNE — c'est probablement le bug" if len(current_flagged) > 1 else ""
        print(f"Années maçonniques : {len(years)}")
        print(f"Marquées is_current=True : {len(current_flagged)}  {multi_current_warning}")
        print()

        for y in years:
            mr = await db.execute(select(func.count()).where(Meeting.masonic_year_id == y.id))
            n_meetings = mr.scalar() or 0
            covers_today = "oui" if (y.start_date <= today <= y.end_date) else "non"
            print(f"#{y.id} {y.label!r}  {y.start_date} → {y.end_date}  "
                  f"is_current={y.is_current}  couvre_aujourdhui={covers_today}  tenues={n_meetings}")

        # Meetings not tied to any MasonicYear at all (would be silently
        # excluded from every year's stats — a different but related bug)
        mr_orphan = await db.execute(select(func.count()).where(Meeting.masonic_year_id.is_(None)))
        n_orphan = mr_orphan.scalar() or 0
        if n_orphan:
            print(f"\n⚠️ {n_orphan} tenue(s) sans masonic_year_id du tout (orpheline, invisible dans toute stat par année).")

        print()
        current = next((y for y in years if y.is_current), None)
        if not current:
            print("Aucune année n'est actuellement marquée 'en cours' (is_current=True).")
            return

        print(f"— Détail pour l'année en cours utilisée par le dashboard : #{current.id} {current.label!r} —")
        past_r = await db.execute(
            select(Meeting.id, Meeting.meeting_date, Meeting.masonic_year_id)
            .where(Meeting.masonic_year_id == current.id, Meeting.meeting_date < today)
            .order_by(Meeting.meeting_date)
        )
        past = past_r.all()
        print(f"Tenues passées de cette année (celles comptées dans le %) : {len(past)}")
        for m_id, m_date, myid in past:
            print(f"   tenue #{m_id}  {m_date}  masonic_year_id={myid}")

        if past:
            att_r = await db.execute(
                select(Attendance.status, func.count()).where(
                    Attendance.meeting_id.in_([p[0] for p in past])
                ).group_by(Attendance.status)
            )
            print("Répartition des présences comptées :")
            for status, n in att_r.all():
                print(f"   {status}: {n}")


if __name__ == "__main__":
    asyncio.run(main())
