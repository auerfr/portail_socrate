"""
Rattrapage — fusionne les doublons créés par create_member_folders.py

Le script initial ne repérait un dossier déjà existant que par
DocFolder.subject_member_id (vide au premier lancement) et a donc créé un
nouveau dossier vide pour chaque membre actif, même quand un dossier du
même nom existait déjà (créé manuellement, avec du contenu et souvent une
restriction de groupe, ex: "Secrétariat").

Pour chaque paire de dossiers de même nom sous le dossier racine des
membres, ce script :
  - garde le dossier PRÉ-EXISTANT (celui sans subject_member_id, ou le plus
    ancien) comme dossier définitif du membre,
  - y déplace les documents, sous-dossiers et délégués du doublon vide,
  - le rattache au membre (subject_member_id),
  - supprime le doublon devenu vide.

Idempotent — sans doublon à fusionner, ne fait rien.

Usage :
    python scripts/fix_member_folder_duplicates.py            # aperçu (dry-run)
    python scripts/fix_member_folder_duplicates.py --apply    # applique
"""
import asyncio
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import app.main  # noqa: F401 — enregistre tous les modèles sur Base.metadata
from app.database import AsyncSessionLocal
from app.models.documents import DocFolder, DocFolderDelegate, Document
from app.models.lodge import LodgeSettings
from sqlalchemy import select, update


async def main():
    apply = "--apply" in sys.argv

    async with AsyncSessionLocal() as db:
        ls_r = await db.execute(select(LodgeSettings).limit(1))
        lodge = ls_r.scalar_one_or_none()
        if not lodge or not lodge.member_folders_parent_id:
            print("Aucun dossier racine des membres n'est configuré.")
            return

        children_r = await db.execute(
            select(DocFolder).where(DocFolder.parent_id == lodge.member_folders_parent_id)
        )
        children = children_r.scalars().all()

        groups = defaultdict(list)
        for f in children:
            groups[f.name.strip().lower()].append(f)

        dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
        if not dup_groups:
            print("Aucun doublon détecté.")
            return

        print(f"{len(dup_groups)} nom(s) en doublon détecté(s).\n")

        for key, folders in dup_groups.items():
            folders_sorted = sorted(folders, key=lambda f: (f.subject_member_id is not None, f.id))
            keeper = folders_sorted[0]
            dups = folders_sorted[1:]
            subject_id = next((f.subject_member_id for f in folders if f.subject_member_id), None)

            print(f"« {keeper.name} » : conserve id={keeper.id} (subject_member_id actuel={keeper.subject_member_id}), "
                  f"fusionne {[d.id for d in dups]} → membre_id cible={subject_id}")

            if not apply:
                continue

            for dup in dups:
                await db.execute(
                    update(Document).where(Document.folder_id == dup.id).values(folder_id=keeper.id)
                )
                await db.execute(
                    update(DocFolder).where(DocFolder.parent_id == dup.id).values(parent_id=keeper.id)
                )

                dup_delegates_r = await db.execute(
                    select(DocFolderDelegate).where(DocFolderDelegate.folder_id == dup.id)
                )
                keeper_member_ids_r = await db.execute(
                    select(DocFolderDelegate.member_id).where(DocFolderDelegate.folder_id == keeper.id)
                )
                keeper_member_ids = {m for m in keeper_member_ids_r.scalars().all()}
                for deleg in dup_delegates_r.scalars().all():
                    if deleg.member_id in keeper_member_ids:
                        await db.delete(deleg)
                    else:
                        deleg.folder_id = keeper.id
                        keeper_member_ids.add(deleg.member_id)

                await db.delete(dup)

            if subject_id and not keeper.subject_member_id:
                keeper.subject_member_id = subject_id

        if apply:
            await db.commit()
            print("\nFusion appliquée.")
        else:
            print("\nAperçu seulement — relancez avec --apply pour appliquer.")


if __name__ == "__main__":
    asyncio.run(main())
