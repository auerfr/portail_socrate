"""Vérifie que les appels log_audit() ajoutés au module forum ne font pas
planter les routes, via de vraies requêtes HTTP (admin_client) maintenant
que get_db() est correctement redirigé vers l'engine de test (cf.
conftest.py)."""
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
async def test_forum_theme_subject_decision_lifecycle_logged(admin_client: AsyncClient, db: AsyncSession):
    # Thème
    after_id = await _max_audit_id(db)
    resp = await admin_client.post(
        "/forum/themes/new",
        data={"name": "Thème test audit", "color": "", "description": "", "order_position": "0", "target": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "FORUM_THEME_CREATE"
    theme_id = (await db.execute(select(AuditLog.resource_id).where(AuditLog.id > after_id))).scalar_one()

    # Sujet (avec fichier, pour vérifier au passage le correctif multi-upload)
    after_id = await _max_audit_id(db)
    resp = await admin_client.post(
        "/forum/new",
        data={"theme_id": str(theme_id), "title": "Sujet test audit", "content": "Contenu initial"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "FORUM_SUBJECT_CREATE"
    subject_id = (await db.execute(select(AuditLog.resource_id).where(AuditLog.id > after_id))).scalar_one()

    # Pin / Lock
    after_id = await _max_audit_id(db)
    await admin_client.post(f"/forum/t/{subject_id}/pin", follow_redirects=False)
    assert await _last_action(db, after_id) == "FORUM_SUBJECT_PIN"

    after_id = await _max_audit_id(db)
    await admin_client.post(f"/forum/t/{subject_id}/lock", follow_redirects=False)
    assert await _last_action(db, after_id) == "FORUM_SUBJECT_LOCK"
    await admin_client.post(f"/forum/t/{subject_id}/lock", follow_redirects=False)  # redéverrouille pour la suite

    # Décision
    after_id = await _max_audit_id(db)
    resp = await admin_client.post(
        f"/forum/t/{subject_id}/decision",
        data={"title": "Décision test", "description": "", "closes_at": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "FORUM_DECISION_CREATE"
    decision_id = (await db.execute(select(AuditLog.resource_id).where(AuditLog.id > after_id))).scalar_one()

    after_id = await _max_audit_id(db)
    await admin_client.post(f"/forum/decisions/{decision_id}/close", data={"outcome": ""}, follow_redirects=False)
    assert await _last_action(db, after_id) == "FORUM_DECISION_CLOSE"

    # Suppression du sujet puis du thème
    after_id = await _max_audit_id(db)
    resp = await admin_client.post(f"/forum/t/{subject_id}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "FORUM_SUBJECT_DELETE"

    after_id = await _max_audit_id(db)
    resp = await admin_client.post(f"/forum/themes/{theme_id}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "FORUM_THEME_DELETE"
