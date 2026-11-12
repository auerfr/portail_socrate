"""Vérifie que les appels log_audit() ajoutés au module projects (projets +
modèles) ne font pas planter les routes, via de vraies requêtes HTTP
(admin_client). Les actions au niveau des tâches ont déjà leur propre
journal (ProjectActivity, scoped au projet) — non dupliquées ici."""
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
async def test_project_create_edit_delete_logged(admin_client: AsyncClient, db: AsyncSession):
    after_id = await _max_audit_id(db)
    resp = await admin_client.post(
        "/projects/new",
        data={"name": "Projet test audit", "description": "", "color": "#4a9d8f", "type": "PROJECT"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "PROJECT_CREATE"
    project_id = (await db.execute(select(AuditLog.resource_id).where(AuditLog.id > after_id))).scalar_one()

    after_id = await _max_audit_id(db)
    resp = await admin_client.post(
        f"/projects/{project_id}/edit",
        data={"name": "Projet renommé", "description": "", "color": "#4a9d8f", "status": "ACTIVE"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "PROJECT_EDIT"

    after_id = await _max_audit_id(db)
    resp = await admin_client.post(f"/projects/{project_id}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "PROJECT_DELETE"


@pytest.mark.asyncio
async def test_project_template_create_delete_logged(admin_client: AsyncClient, db: AsyncSession):
    after_id = await _max_audit_id(db)
    resp = await admin_client.post(
        "/projects/templates/new",
        data={"name": "Modèle test audit", "description": "", "color": "#4a9d8f", "type": "PROJECT"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "PROJECT_TEMPLATE_CREATE"
    tpl_id = (await db.execute(select(AuditLog.resource_id).where(AuditLog.id > after_id))).scalar_one()

    after_id = await _max_audit_id(db)
    resp = await admin_client.post(f"/projects/templates/{tpl_id}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "PROJECT_TEMPLATE_DELETE"
