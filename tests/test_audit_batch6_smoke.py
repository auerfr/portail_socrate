"""Vérifie que les appels log_audit() ajoutés au module meetings (tenues)
ne font pas planter les routes, via de vraies requêtes HTTP (admin_client)."""
from datetime import date, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lodge import MasonicYear
from app.models.system import AuditLog


async def _max_audit_id(db: AsyncSession) -> int:
    return (await db.execute(select(func.max(AuditLog.id)))).scalar() or 0


async def _last_action(db: AsyncSession, after_id: int) -> str:
    r = await db.execute(select(AuditLog.action).where(AuditLog.id > after_id).order_by(AuditLog.id.desc()).limit(1))
    return r.scalar_one()


@pytest.mark.asyncio
async def test_meeting_create_edit_lock_delete_logged(admin_client: AsyncClient, db: AsyncSession):
    year = MasonicYear(
        label="test-year-batch6", start_date=date.today() - timedelta(days=30),
        end_date=date.today() + timedelta(days=300), is_current=True,
    )
    db.add(year)
    await db.commit()

    meeting_date = date.today() + timedelta(days=7)
    after_id = await _max_audit_id(db)
    resp = await admin_client.post(
        "/meetings/new/form",
        data={
            "meeting_date": meeting_date.isoformat(), "meeting_time": "20:30",
            "meeting_type": "SOLENNELLE", "meeting_grade": "APPRENTI",
            "meeting_status": "PUBLISHED",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text
    assert await _last_action(db, after_id) == "MEETING_CREATE"
    meeting_id = (await db.execute(select(AuditLog.resource_id).where(AuditLog.id > after_id))).scalar_one()

    after_id = await _max_audit_id(db)
    resp = await admin_client.post(
        f"/meetings/{meeting_id}/edit",
        data={
            "meeting_date": meeting_date.isoformat(), "meeting_time": "20:30",
            "meeting_type": "SOLENNELLE", "meeting_grade": "APPRENTI",
            "meeting_status": "PUBLISHED",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text
    assert await _last_action(db, after_id) == "MEETING_EDIT"

    after_id = await _max_audit_id(db)
    resp = await admin_client.post(f"/meetings/{meeting_id}/lock", follow_redirects=False)
    assert resp.status_code == 302
    assert await _last_action(db, after_id) == "MEETING_LOCK"

    after_id = await _max_audit_id(db)
    resp = await admin_client.post(f"/meetings/{meeting_id}/lock", follow_redirects=False)
    assert resp.status_code == 302
    assert await _last_action(db, after_id) == "MEETING_UNLOCK"

    after_id = await _max_audit_id(db)
    resp = await admin_client.post(f"/meetings/{meeting_id}/delete", follow_redirects=False)
    assert resp.status_code == 302
    assert await _last_action(db, after_id) == "MEETING_DELETE"
