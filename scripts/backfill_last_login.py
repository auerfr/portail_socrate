"""
Script one-shot : remplit last_login_at depuis toute activité connue.
Sources consultées (la plus récente gagne) :
  - audit_logs (n'importe quelle action)
  - chat_messages (messages envoyés)
  - page_views (pages visitées)

Lancer une seule fois depuis la console PythonAnywhere :
  cd /home/<user>/<app> && python scripts/backfill_last_login.py
"""
import asyncio
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, func, text
from app.database import AsyncSessionLocal
from app.models.identity import User


async def main():
    import app.models.documents, app.models.identity, app.models.groups
    import app.models.lodge, app.models.meetings, app.models.system

    from app.models.system import AuditLog

    async with AsyncSessionLocal() as db:
        r = await db.execute(select(User).where(User.last_login_at.is_(None)))
        users = r.scalars().all()
        print(f"{len(users)} compte(s) sans date de connexion enregistrée\n")

        updated = 0
        for user in users:
            if not user.member_id:
                continue

            candidates = []

            # 1. Audit log (toute action — preuve d'authentification)
            r1 = await db.execute(
                select(func.max(AuditLog.created_at))
                .where(AuditLog.actor_id == user.member_id)
            )
            d = r1.scalar_one_or_none()
            if d:
                candidates.append(d)

            # 2. Messages de chat envoyés
            try:
                r2 = await db.execute(
                    text("SELECT MAX(created_at) FROM chat_messages WHERE sender_id = :mid AND is_deleted = 0"),
                    {"mid": user.member_id},
                )
                d = r2.scalar()
                if d:
                    candidates.append(d if isinstance(d, datetime) else datetime.fromisoformat(str(d)))
            except Exception:
                pass

            # 3. Pages visitées
            try:
                r3 = await db.execute(
                    text("SELECT MAX(created_at) FROM page_views WHERE member_id = :mid"),
                    {"mid": user.member_id},
                )
                d = r3.scalar()
                if d:
                    candidates.append(d if isinstance(d, datetime) else datetime.fromisoformat(str(d)))
            except Exception:
                pass

            if candidates:
                best = max(candidates)
                user.last_login_at = best
                updated += 1
                print(f"  ✓ {user.login or user.id:30s} → {best.strftime('%d/%m/%Y %H:%M')}")
            else:
                print(f"  — {user.login or user.id:30s} → aucune activité trouvée")

        if updated:
            await db.commit()
            print(f"\n{updated} compte(s) mis à jour.")
        else:
            print("\nAucune activité trouvée pour les comptes sans date.")


asyncio.run(main())
