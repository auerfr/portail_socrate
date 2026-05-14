"""Permissions fines par module.

Utilisation :
  await has_permission(db, user, "can_manage_finance")
  await user_permissions(db, user_id)  → set de strings

Ces permissions s'ajoutent aux droits natifs (is_admin, lodge_function).
"""
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system import ModulePermission

# Toutes les permissions possibles avec leur libellé
ALL_PERMISSIONS = {
    "can_manage_finance":   "Finance — budget, cotisations, bilan",
    "can_manage_meetings":  "Tenues — créer, modifier, supprimer",
    "can_manage_members":   "Membres — ajouter, modifier les grades",
    "can_send_mailing":     "Diffusion — envoyer des campagnes email",
    "can_manage_documents": "GED — upload, gestion des dossiers",
    "can_manage_programs":  "Programmes — créer et envoyer",
    "can_manage_projects":  "Projets — créer et gérer des projets",
}

_CACHE: dict[int, set] = {}


async def user_permissions(db: AsyncSession, user_id: int) -> set:
    """Retourne le set des permissions fines de cet utilisateur."""
    if user_id in _CACHE:
        return _CACHE[user_id]
    r = await db.execute(
        select(ModulePermission.permission).where(ModulePermission.user_id == user_id)
    )
    perms = {row[0] for row in r.all()}
    _CACHE[user_id] = perms
    return perms


async def has_permission(db: AsyncSession, user, perm: str) -> bool:
    """L'user est-il admin OU possède-t-il cette permission fine ?"""
    if getattr(user, "is_admin", False):
        return True
    perms = await user_permissions(db, user.id)
    return perm in perms


async def grant_permission(db: AsyncSession, user_id: int, perm: str,
                           granted_by_id: Optional[int] = None) -> None:
    """Accorde une permission (idempotent)."""
    r = await db.execute(
        select(ModulePermission).where(
            ModulePermission.user_id == user_id,
            ModulePermission.permission == perm,
        )
    )
    if not r.scalar_one_or_none():
        db.add(ModulePermission(user_id=user_id, permission=perm,
                                granted_by_id=granted_by_id))
        await db.commit()
    _CACHE.pop(user_id, None)


async def revoke_permission(db: AsyncSession, user_id: int, perm: str) -> None:
    """Révoque une permission."""
    from sqlalchemy import delete
    await db.execute(
        delete(ModulePermission).where(
            ModulePermission.user_id == user_id,
            ModulePermission.permission == perm,
        )
    )
    await db.commit()
    _CACHE.pop(user_id, None)
