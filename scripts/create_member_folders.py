"""
Rattrapage : crée le dossier individuel (vide) de chaque membre actif qui
n'en a pas encore, dans le dossier racine désigné via la Bibliothèque
(bouton "Utiliser comme dossier racine des membres").

Idempotent — ne recrée rien pour un membre qui a déjà un dossier
(DocFolder.subject_member_id). Doit être relancé après l'ajout de nouveaux
membres si jamais l'auto-création (à la création du membre) a été
contournée par un import direct en base.

Usage :
    python scripts/create_member_folders.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import app.main  # noqa: F401 — enregistre tous les modèles sur Base.metadata
from app.database import AsyncSessionLocal
from app.models.identity import Member, MemberStatus
from app.models.lodge import LodgeSettings
from app.services.member_folders import create_member_folder, member_folder_name
from sqlalchemy import select


async def main():
    async with AsyncSessionLocal() as db:
        ls_r = await db.execute(select(LodgeSettings).limit(1))
        lodge = ls_r.scalar_one_or_none()
        if not lodge or not lodge.member_folders_parent_id:
            print(
                "Aucun dossier racine des membres n'est configuré.\n"
                "Ouvrez le dossier voulu dans la Bibliothèque (ex: « Dossiers membres ») "
                "et cliquez sur « Utiliser comme dossier racine des membres », "
                "puis relancez ce script."
            )
            return

        members_r = await db.execute(
            select(Member).where(Member.status == MemberStatus.ACTIVE)
            .order_by(Member.last_name, Member.first_name)
        )
        members = members_r.scalars().all()

        created, skipped = 0, 0
        for m in members:
            folder = await create_member_folder(db, m)
            if folder:
                created += 1
                print(f"  + créé : {member_folder_name(m)}")
            else:
                skipped += 1

        await db.commit()
        print(f"\n{created} dossier(s) créé(s), {skipped} déjà existant(s) (inchangé).")


if __name__ == "__main__":
    asyncio.run(main())
