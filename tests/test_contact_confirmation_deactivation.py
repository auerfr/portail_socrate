"""Régression : la désactivation automatique des correspondants externes
après 60 jours sans réponse à la campagne de confirmation annuelle.

Avant ce correctif, deactivate_unconfirmed() désactivait tout contact actif
dont last_confirmed_at était NULL — y compris un contact jamais sollicité
(aucune campagne envoyée), puisqu'aucun champ ne permettait de savoir quand
le délai de 60 jours devait réellement démarrer pour lui. La fonction
n'était d'ailleurs jamais appelée automatiquement (seul un bouton manuel
dans Paramètres), donc ce bug n'avait jamais eu l'occasion de se manifester
en pratique — mais rendre l'appel automatique (daily_contact_confirmation_loop)
sans corriger la logique aurait désactivé d'un coup tous les contacts
jamais encore campagnés."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lodge import ExternalContact
from app.services.contact_confirmation import deactivate_unconfirmed


def _contact(db: AsyncSession, **kwargs) -> ExternalContact:
    defaults = dict(name="Test Contact", contact_type="EXTERNAL", is_active=True)
    defaults.update(kwargs)
    c = ExternalContact(**defaults)
    db.add(c)
    return c


@pytest.mark.asyncio
async def test_never_campaigned_contact_is_not_deactivated(db: AsyncSession):
    c = _contact(db, email="jamais-sollicite@example.fr")
    await db.commit()

    await deactivate_unconfirmed(db, older_than_days=60)

    await db.refresh(c)
    assert c.is_active is True


@pytest.mark.asyncio
async def test_campaigned_over_60_days_ago_never_confirmed_is_deactivated(db: AsyncSession):
    old = datetime.utcnow() - timedelta(days=65)
    c = _contact(db, email="sans-reponse@example.fr", confirmation_sent_at=old)
    await db.commit()

    await deactivate_unconfirmed(db, older_than_days=60)

    await db.refresh(c)
    assert c.is_active is False


@pytest.mark.asyncio
async def test_campaigned_over_60_days_ago_but_confirmed_after_stays_active(db: AsyncSession):
    sent = datetime.utcnow() - timedelta(days=65)
    confirmed = datetime.utcnow() - timedelta(days=10)
    c = _contact(db, email="a-confirme@example.fr", confirmation_sent_at=sent, last_confirmed_at=confirmed)
    await db.commit()

    await deactivate_unconfirmed(db, older_than_days=60)

    await db.refresh(c)
    assert c.is_active is True


@pytest.mark.asyncio
async def test_campaigned_recently_not_yet_deactivated(db: AsyncSession):
    recent = datetime.utcnow() - timedelta(days=10)
    c = _contact(db, email="campagne-recente@example.fr", confirmation_sent_at=recent)
    await db.commit()

    await deactivate_unconfirmed(db, older_than_days=60)

    await db.refresh(c)
    assert c.is_active is True


@pytest.mark.asyncio
async def test_removal_requested_contact_is_untouched(db: AsyncSession):
    old = datetime.utcnow() - timedelta(days=65)
    c = _contact(
        db, email="desinscrit@example.fr", confirmation_sent_at=old,
        is_active=False, removal_requested_at=datetime.utcnow() - timedelta(days=5),
    )
    await db.commit()

    count = await deactivate_unconfirmed(db, older_than_days=60)

    await db.refresh(c)
    assert c.is_active is False
