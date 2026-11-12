"""Vérifie que les appels log_audit() ajoutés au module documents (GED) ne
font pas planter les routes, via de vraies requêtes HTTP (admin_client)."""
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.documents import DocFolder, DocSpace, Document
from app.models.system import AuditLog


async def _max_audit_id(db: AsyncSession) -> int:
    return (await db.execute(select(func.max(AuditLog.id)))).scalar() or 0


async def _last_action(db: AsyncSession, after_id: int) -> str:
    r = await db.execute(select(AuditLog.action).where(AuditLog.id > after_id).order_by(AuditLog.id.desc()).limit(1))
    return r.scalar_one()


@pytest.mark.asyncio
async def test_space_folder_lifecycle_logged(admin_client: AsyncClient, db: AsyncSession):
    after_id = await _max_audit_id(db)
    resp = await admin_client.post(
        "/documents/admin/space",
        data={"name": "Espace test audit", "description": "", "min_grade": "ALL",
              "group_id": "", "order_position": "0", "icon": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "DOC_SPACE_CREATE"
    space_id = (await db.execute(select(AuditLog.resource_id).where(AuditLog.id > after_id))).scalar_one()

    after_id = await _max_audit_id(db)
    resp = await admin_client.post(
        f"/documents/admin/space/{space_id}/edit",
        data={"name": "Espace renommé", "description": "", "min_grade": "ALL",
              "group_id": "", "order_position": "0", "icon": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "DOC_SPACE_EDIT"

    after_id = await _max_audit_id(db)
    resp = await admin_client.post(
        "/documents/admin/folder",
        data={"name": "Dossier test audit", "space_id": str(space_id), "min_grade": "ALL",
              "group_id": "", "order_position": "0"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "DOC_FOLDER_CREATE"
    folder_id = (await db.execute(select(AuditLog.resource_id).where(AuditLog.id > after_id))).scalar_one()

    after_id = await _max_audit_id(db)
    resp = await admin_client.post(
        f"/documents/folder/{folder_id}/add-link",
        data={"name": "Lien test", "url": "https://example.fr", "description": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "DOCUMENT_ADD_LINK"
    doc_id = (await db.execute(select(AuditLog.resource_id).where(AuditLog.id > after_id))).scalar_one()

    after_id = await _max_audit_id(db)
    resp = await admin_client.post(f"/documents/file/{doc_id}/rename", data={"name": "Lien renommé"}, follow_redirects=False)
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "DOCUMENT_RENAME"

    after_id = await _max_audit_id(db)
    resp = await admin_client.post(f"/documents/file/{doc_id}/trash", follow_redirects=False)
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "DOCUMENT_TRASH"

    after_id = await _max_audit_id(db)
    resp = await admin_client.post(f"/documents/file/{doc_id}/restore", follow_redirects=False)
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "DOCUMENT_RESTORE"

    after_id = await _max_audit_id(db)
    resp = await admin_client.post(f"/documents/admin/file/{doc_id}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "DOCUMENT_DELETE"

    after_id = await _max_audit_id(db)
    resp = await admin_client.post(f"/documents/admin/folder/{folder_id}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "DOC_FOLDER_DELETE"

    after_id = await _max_audit_id(db)
    resp = await admin_client.post(f"/documents/admin/space/{space_id}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "DOC_SPACE_DELETE"


@pytest.mark.asyncio
async def test_document_upload_version_and_bulk_logged(admin_client: AsyncClient, db: AsyncSession):
    space = DocSpace(name="Bibliothèque test audit batch8")
    db.add(space)
    await db.flush()
    folder = DocFolder(space_id=space.id, name="Dossier test batch8")
    db.add(folder)
    await db.commit()

    created_paths: list[str] = []
    try:
        after_id = await _max_audit_id(db)
        resp = await admin_client.post(
            f"/documents/folder/{folder.id}/upload",
            files=[("files", ("doc.pdf", b"contenu initial", "application/pdf"))],
            data={"doc_name": "", "notify_members": "", "notify_target": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert await _last_action(db, after_id) == "DOCUMENT_UPLOAD"

        doc = (await db.execute(select(Document).where(Document.folder_id == folder.id))).scalar_one()
        created_paths.append(doc.storage_path)

        after_id = await _max_audit_id(db)
        resp = await admin_client.post(
            f"/documents/file/{doc.id}/new-version",
            files={"file": ("doc_v2.pdf", b"contenu v2", "application/pdf")},
            data={"change_notes": "mise à jour"},
            follow_redirects=False,
        )
        assert resp.status_code == 303, resp.text
        assert await _last_action(db, after_id) == "DOCUMENT_NEW_VERSION"
        await db.refresh(doc)
        created_paths.append(doc.storage_path)

        after_id = await _max_audit_id(db)
        resp = await admin_client.post(
            "/documents/bulk",
            data={"action": "trash", "doc_ids": [str(doc.id)], "back_folder_id": str(folder.id)},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert await _last_action(db, after_id) == "DOCUMENT_BULK_TRASH"
    finally:
        for p in created_paths:
            if p:
                Path(p).unlink(missing_ok=True)
