"""Vérifie que les appels log_audit() ajoutés aux modules chat et mailing ne
font pas planter les routes, via de vraies requêtes HTTP (admin_client)."""
import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system import AuditLog


async def _max_audit_id(db: AsyncSession) -> int:
    return (await db.execute(select(func.max(AuditLog.id)))).scalar() or 0


async def _last_action(db: AsyncSession, after_id: int) -> str:
    r = await db.execute(select(AuditLog.action).where(AuditLog.id > after_id).order_by(AuditLog.id.desc()).limit(1))
    return r.scalar_one()


@pytest.mark.asyncio
async def test_chat_channel_create_edit_delete_logged(admin_client: AsyncClient, db: AsyncSession):
    after_id = await _max_audit_id(db)
    resp = await admin_client.post(
        "/chat/channels/new",
        data={"name": "Canal test audit", "description": "", "channel_type": "GENERAL",
              "grade_filter": "", "function_filter": "", "is_readonly": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "CHAT_CHANNEL_CREATE"
    channel_id = (await db.execute(select(AuditLog.resource_id).where(AuditLog.id > after_id))).scalar_one()

    after_id = await _max_audit_id(db)
    resp = await admin_client.post(
        f"/chat/channels/{channel_id}/edit",
        data={"name": "Canal renommé", "description": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "CHAT_CHANNEL_EDIT"

    after_id = await _max_audit_id(db)
    resp = await admin_client.post(f"/chat/channels/{channel_id}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "CHAT_CHANNEL_DELETE"


@pytest.mark.asyncio
async def test_mailing_list_create_edit_delete_logged(admin_client: AsyncClient, db: AsyncSession):
    after_id = await _max_audit_id(db)
    resp = await admin_client.post(
        "/mailing/lists/new",
        data={"name": "Liste test audit", "description": "", "list_type": "STATIC"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "MAILING_LIST_CREATE"
    list_id = (await db.execute(select(AuditLog.resource_id).where(AuditLog.id > after_id))).scalar_one()

    after_id = await _max_audit_id(db)
    resp = await admin_client.post(
        f"/mailing/lists/{list_id}/edit",
        data={"name": "Liste renommée", "description": "", "list_type": "STATIC"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "MAILING_LIST_EDIT"

    after_id = await _max_audit_id(db)
    resp = await admin_client.post(f"/mailing/lists/{list_id}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "MAILING_LIST_DELETE"
