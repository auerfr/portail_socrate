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
    "can_manage_finance":   "Finance — budget, cotisations, bilan (délégation du rôle de Trésorier)",
    "can_manage_meetings":  "Tenues — créer, modifier, supprimer",
    "can_manage_members":   "Membres — ajouter, modifier les grades",
    "can_send_mailing":     "Diffusion — envoyer des campagnes email",
    "can_manage_documents": "GED — upload, gestion des dossiers",
    "can_manage_programs":  "Programmes — créer et envoyer",
    "can_manage_projects":  "Projets — créer et gérer des projets",
    "can_manage_news":              "Actualités — créer, modifier, supprimer",
    "can_manage_polls":             "Sondages — voir tous les sondages, gérer/clôturer ceux des autres",
    "can_manage_forum":             "Forum — modérer, gérer les catégories",
    "can_manage_calendar":          "Agenda — créer des événements partagés",
    "can_manage_announcements":     "Annonces — créer, modifier, supprimer",
    "can_manage_planches":          "Planches — rédiger/gérer au nom d'un officier",
    "can_manage_groups":            "Groupes — créer, modifier la composition",
    "can_manage_partner_lodges":    "Loges voisines — ajouter, modifier l'annuaire",
}

async def user_permissions(db: AsyncSession, user_id: int) -> set:
    """Retourne le set des permissions fines de cet utilisateur.

    Pas de cache : avec plusieurs processus serveur (uWSGI), un cache en
    mémoire par processus n'est jamais invalidé dans les autres processus
    quand une permission est accordée/révoquée — un utilisateur peut alors
    voir l'ancien état pendant des heures selon le processus qui traite sa
    requête (observé en production le 03/10/2026 : droits GED accordés à
    un membre, invisibles pour lui). La requête est triviale (indexée sur
    user_id), aucun besoin réel de caching ici."""
    r = await db.execute(
        select(ModulePermission.permission).where(ModulePermission.user_id == user_id)
    )
    return {row[0] for row in r.all()}


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
