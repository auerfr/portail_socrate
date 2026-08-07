"""
Script one-shot : remplit last_login_at depuis les logs d'audit (action=LOGIN).
Lancer une seule fois depuis la console PythonAnywhere :
  cd /home/<user>/<app> && python scripts/backfill_last_login.py
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, func
from app.database import AsyncSessionLocal
from app.models.identity import User
from app.models.system import AuditLog


async def main():
    import app.models.documents, app.models.identity, app.models.groups
    import app.models.lodge, app.models.meetings, app.models.system

    async with AsyncSessionLocal() as db:
        # Récupérer tous les users sans last_login_at
        r = await db.execute(select(User).where(User.last_login_at.is_(None)))
        users = r.scalars().all()
        print(f"{len(users)} compte(s) sans date de connexion")

        updated = 0
        for user in users:
            if not user.member_id:
                continue
            # Chercher le login le plus récent dans les logs d'audit
            r_log = await db.execute(
                select(func.max(AuditLog.created_at))
                .where(AuditLog.actor_id == user.member_id, AuditLog.action == "LOGIN")
            )
            last_login = r_log.scalar_one_or_none()
            if last_login:
                user.last_login_at = last_login
                updated += 1
                print(f"  ✓ user_id={user.id} → last_login_at={last_login}")

        if updated:
            await db.commit()
            print(f"\n{updated} compte(s) mis à jour.")
        else:
            print("\nAucune entrée LOGIN trouvée dans les logs d'audit.")


asyncio.run(main())
