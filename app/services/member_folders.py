"""Service — dossier "à propos de" chaque membre dans la Bibliothèque.

Un sous-dossier vide est créé automatiquement pour chaque membre sous le
dossier "racine" configuré (LodgeSettings.member_folders_parent_id, ex:
"Dossiers membres" en Secrétariat), et déplacé automatiquement vers le
dossier "Anciens membres" (LodgeSettings.former_members_folder_id) dès que
le membre passe en démission / radiation / décès.

Les deux dossiers racine sont désignés depuis la Bibliothèque elle-même
(boutons admin sur la page d'un dossier) plutôt que configurés ici — tant
qu'ils ne le sont pas, ces fonctions sont des no-op silencieux.
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.documents import DocFolder
from app.models.lodge import LodgeSettings


def member_folder_name(member) -> str:
    return f"{(member.last_name or '').upper()} {member.first_name or ''}".strip()


async def _get_lodge_settings(db: AsyncSession) -> Optional[LodgeSettings]:
    r = await db.execute(select(LodgeSettings).limit(1))
    return r.scalar_one_or_none()


async def create_member_folder(db: AsyncSession, member) -> Optional[DocFolder]:
    """Crée le dossier du membre s'il n'en a pas déjà un et que le dossier
    racine est configuré. Ne commit pas — à la charge de l'appelant."""
    lodge = await _get_lodge_settings(db)
    if not lodge or not lodge.member_folders_parent_id:
        return None
    parent = await db.get(DocFolder, lodge.member_folders_parent_id)
    if not parent:
        return None

    existing_r = await db.execute(
        select(DocFolder.id).where(DocFolder.subject_member_id == member.id).limit(1)
    )
    if existing_r.scalar_one_or_none():
        return None

    folder = DocFolder(
        space_id=parent.space_id,
        parent_id=parent.id,
        name=member_folder_name(member),
        min_grade=parent.min_grade,
        group_id=parent.group_id,
        subject_member_id=member.id,
    )
    db.add(folder)
    return folder


async def move_member_folder_to_former(db: AsyncSession, member) -> bool:
    """Déplace le dossier existant du membre vers "Anciens membres" si
    configuré. Ne commit pas — à la charge de l'appelant."""
    lodge = await _get_lodge_settings(db)
    if not lodge or not lodge.former_members_folder_id:
        return False
    dest = await db.get(DocFolder, lodge.former_members_folder_id)
    if not dest:
        return False

    folder_r = await db.execute(
        select(DocFolder).where(DocFolder.subject_member_id == member.id).limit(1)
    )
    folder = folder_r.scalar_one_or_none()
    if not folder or folder.parent_id == dest.id:
        return False

    folder.parent_id = dest.id
    folder.space_id = dest.space_id
    return True
