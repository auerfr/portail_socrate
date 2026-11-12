"""Vérifie que les appels log_audit() ajoutés au module attendance (visiteurs
du carnet, hors tenue) ne font pas planter les routes, via de vraies requêtes
HTTP (admin_client)."""
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
async def test_visitor_add_edit_delete_logged(admin_client: AsyncClient, db: AsyncSession):
    after_id = await _max_audit_id(db)
    resp = await admin_client.post(
        "/attendance/visitor/add",
        data={
            "civility": "F", "last_name": "Dupont", "first_name": "Jean",
            "lodge_name": "", "orient_city": "", "obedience": "",
            "masonic_grade": "", "is_vm": "", "email": "", "phone": "", "program_optin": "",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "VISITOR_CREATE"
    visitor_id = (await db.execute(select(AuditLog.resource_id).where(AuditLog.id > after_id))).scalar_one()

    after_id = await _max_audit_id(db)
    resp = await admin_client.post(
        f"/attendance/visitor/{visitor_id}/edit",
        data={
            "civility": "F", "last_name": "Dupont", "first_name": "Jean-Modifié",
            "lodge_name": "", "orient_city": "", "obedience": "",
            "masonic_grade": "", "is_vm": "", "email": "", "phone": "", "program_optin": "",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "VISITOR_EDIT"

    after_id = await _max_audit_id(db)
    resp = await admin_client.post(f"/attendance/visitor/{visitor_id}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "VISITOR_DELETE"
