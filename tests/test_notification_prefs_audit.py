"""Régression : un membre peut désactiver ses notifications email sans que
ça laisse de trace — un VM qui ne reçoit plus ses notifications et dont on
soupçonne qu'il a lui-même désactivé la case ne peut alors être confirmé ou
infirmé après coup. /settings/notifications est la SEULE route qui touche
Member.email_notifications (require_auth, sur son propre compte) : journalise
désormais le changement, uniquement quand la valeur change réellement."""
import pytest
from httpx import AsyncClient
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system import AuditLog


async def _last_action(db: AsyncSession, after_id: int) -> str:
    r = await db.execute(select(AuditLog.action).where(AuditLog.id > after_id).order_by(AuditLog.id.desc()).limit(1))
    return r.scalar_one()


async def _max_audit_id(db: AsyncSession) -> int:
    return (await db.execute(select(func.max(AuditLog.id)))).scalar() or 0


@pytest.mark.asyncio
async def test_disabling_email_notifications_is_logged(admin_client: AsyncClient, db: AsyncSession):
    after_id = await _max_audit_id(db)
    resp = await admin_client.post("/settings/notifications", data={})  # aucune case cochée → tout off
    assert resp.status_code == 303, resp.text
    assert await _last_action(db, after_id) == "NOTIF_EMAIL_TOGGLE"


@pytest.mark.asyncio
async def test_resaving_same_value_does_not_log_again(admin_client: AsyncClient, db: AsyncSession):
    await admin_client.post("/settings/notifications", data={})  # off
    after_id = await _max_audit_id(db)
    resp = await admin_client.post("/settings/notifications", data={})  # toujours off, rien ne change
    assert resp.status_code == 303, resp.text
    r = await db.execute(select(AuditLog).where(AuditLog.id > after_id, AuditLog.action == "NOTIF_EMAIL_TOGGLE"))
    assert r.scalars().first() is None


@pytest.mark.asyncio
async def test_reenabling_email_notifications_is_logged(admin_client: AsyncClient, db: AsyncSession):
    await admin_client.post("/settings/notifications", data={})  # off
    after_id = await _max_audit_id(db)
    resp = await admin_client.post("/settings/notifications", data={"email_notifications": "on"})
    assert resp.status_code == 303, resp.text
    assert await _last_action(db, after_id) == "NOTIF_EMAIL_TOGGLE"
