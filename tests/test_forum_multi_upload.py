"""Même correctif que test_documents_multi_upload.py, pour le forum
(forum_create_subject avait le même anti-patron Union[list[UploadFile],
UploadFile] qui ne garde que le dernier fichier posté)."""
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.forum import ForumAttachment, ForumMessage, ForumSubject, ForumTheme


@pytest.mark.asyncio
async def test_forum_new_subject_multiple_files_keeps_all_of_them(admin_client: AsyncClient, db: AsyncSession):
    theme = ForumTheme(name="Thème test multi-upload")
    db.add(theme)
    await db.commit()

    files = [
        ("files", ("un.pdf", b"contenu un", "application/pdf")),
        ("files", ("deux.pdf", b"contenu deux", "application/pdf")),
    ]

    created_paths: list[str] = []
    try:
        resp = await admin_client.post(
            "/forum/new",
            files=files,
            data={"theme_id": str(theme.id), "title": "Sujet test", "content": "contenu"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303), resp.text

        subject = (await db.execute(
            select(ForumSubject).where(ForumSubject.theme_id == theme.id)
        )).scalar_one()
        message = (await db.execute(
            select(ForumMessage).where(ForumMessage.subject_id == subject.id)
        )).scalar_one()

        attachments = (await db.execute(
            select(ForumAttachment).where(ForumAttachment.message_id == message.id)
        )).scalars().all()
        created_paths = [a.storage_path for a in attachments if a.storage_path]

        assert sorted(a.original_filename for a in attachments) == ["deux.pdf", "un.pdf"]
    finally:
        for p in created_paths:
            Path(p).unlink(missing_ok=True)
