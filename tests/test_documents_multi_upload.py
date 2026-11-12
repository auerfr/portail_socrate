"""Régression : téléverser plusieurs fichiers à la fois dans un dossier GED
ne devait enregistrer QUE le dernier (Union[List[UploadFile], UploadFile]
fait perdre tous les fichiers sauf un à FastAPI — même bug déjà rencontré et
corrigé dans messages.py::send_message et mailing.py::compose_save, jamais
propagé à documents.py avant ce correctif).

Doit passer par une vraie requête HTTP multipart/form-data (httpx +
ASGITransport) : un appel direct de la fonction du routeur contourne
entièrement la couche de résolution des paramètres de FastAPI où le bug se
produit réellement — un tel test passerait même avec le code cassé (vérifié)."""
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.documents import DocSpace, DocFolder, Document


@pytest.mark.asyncio
async def test_upload_multiple_files_keeps_all_of_them(admin_client: AsyncClient, db: AsyncSession):
    space = DocSpace(name="Bibliothèque test multi-upload")
    db.add(space)
    await db.flush()
    folder = DocFolder(space_id=space.id, name="Dossier test multi-upload")
    db.add(folder)
    await db.commit()

    files = [
        ("files", ("un.pdf", b"contenu un", "application/pdf")),
        ("files", ("deux.pdf", b"contenu deux", "application/pdf")),
        ("files", ("trois.pdf", b"contenu trois", "application/pdf")),
    ]

    created_paths: list[str] = []
    try:
        resp = await admin_client.post(
            f"/documents/folder/{folder.id}/upload",
            files=files,
            data={"doc_name": "", "notify_members": "", "notify_target": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=" not in resp.headers["location"]

        docs = (await db.execute(
            select(Document).where(Document.folder_id == folder.id)
        )).scalars().all()
        created_paths = [d.storage_path for d in docs]

        assert sorted(d.original_filename for d in docs) == ["deux.pdf", "trois.pdf", "un.pdf"]
        assert len(docs) == 3
    finally:
        for p in created_paths:
            Path(p).unlink(missing_ok=True)
