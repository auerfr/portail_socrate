"""Synchronisation de l'opt-in "recevoir le programme" d'un maçon passant
(Visitor.program_optin) vers le carnet de contacts externes et la liste de
diffusion système "Réseau visiteurs".

Jusqu'ici ce champ était saisi (émargement, inscription publique) mais
jamais exploité : aucun ExternalContact n'était créé, personne n'était
réellement ajouté à la liste de diffusion.
"""
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lodge import ExternalContact
from app.models.mailing import MailingList, MailingListExternal
from app.models.meetings import Visitor

RESEAU_VISITEURS_LIST_NAME = "Réseau visiteurs"


async def sync_visitor_program_optin(db: AsyncSession, visitor: Visitor) -> Optional[ExternalContact]:
    """Si le visiteur a coché "recevoir le programme" et fourni un email,
    crée/retrouve le contact externe correspondant et l'inscrit à la liste
    système "Réseau visiteurs".

    Ne désinscrit jamais automatiquement en cas de décochage — le
    désabonnement reste un geste explicite (lien de confirmation annuelle ou
    action admin), pour ne pas désinscrire silencieusement quelqu'un déjà
    inscrit par un autre biais.
    """
    if not visitor.program_optin or not visitor.email or not visitor.email.strip():
        return None

    email = visitor.email.strip().lower()

    r = await db.execute(
        select(ExternalContact).where(func.lower(ExternalContact.email) == email)
    )
    contact = r.scalar_one_or_none()

    if not contact:
        contact = ExternalContact(
            name=f"{visitor.first_name} {visitor.last_name}".strip(),
            first_name=visitor.first_name,
            last_name=visitor.last_name,
            email=email,
            lodge_name=visitor.lodge_name,
            orient=visitor.orient_city,
            obedience=visitor.obedience,
            contact_type="VISITOR",
            is_active=True,
        )
        db.add(contact)
        await db.flush()
    else:
        # Ré-opt-in explicite : on lève une éventuelle désactivation/désinscription
        # antérieure — cette case cochée aujourd'hui est un consentement à jour.
        if not contact.is_active:
            contact.is_active = True
        if contact.removal_requested_at is not None:
            contact.removal_requested_at = None
        # Complète les champs manquants sans écraser une correction manuelle existante
        if not contact.lodge_name and visitor.lodge_name:
            contact.lodge_name = visitor.lodge_name
        if not contact.orient and visitor.orient_city:
            contact.orient = visitor.orient_city
        if not contact.obedience and visitor.obedience:
            contact.obedience = visitor.obedience

    r_list = await db.execute(
        select(MailingList).where(MailingList.name == RESEAU_VISITEURS_LIST_NAME)
    )
    mailing_list = r_list.scalar_one_or_none()
    if not mailing_list:
        return contact

    r_sub = await db.execute(
        select(MailingListExternal).where(
            MailingListExternal.list_id == mailing_list.id,
            MailingListExternal.external_id == contact.id,
        )
    )
    sub = r_sub.scalar_one_or_none()
    if sub:
        if sub.unsubscribed_at is not None:
            sub.unsubscribed_at = None
    else:
        db.add(MailingListExternal(list_id=mailing_list.id, external_id=contact.id))

    return contact
