"""Régénère en PDF les tracés déjà archivés en HTML dans la GED — cas des
tracés approuvés avant que la génération PDF (WeasyPrint) ne fonctionne
correctement en production (pydyf non épinglé).

Remplace le fichier HTML par un PDF équivalent (même gabarit, même
contenu narratif et listes de présence figés au moment de l'approbation)
et met à jour le Document en place (même id, même nom, même dossier) —
rien d'autre n'est modifié.

Usage :
    python scripts/regenerate_trace_pdf.py            # tous les tracés HTML archivés
    python scripts/regenerate_trace_pdf.py 23          # un seul, par meeting_id
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import app.main  # noqa: F401
from app.database import AsyncSessionLocal
from app.models.meetings import Meeting, Attendance, MeetingVisitor
from app.models.reports import MeetingReport, ReportStatus
from app.models.documents import Document
from app.models.lodge import LodgeSettings
from app.routers.meetings import (
    _trace_snapshot_context, _render_trace_pdf, _grade_label,
    TRACE_ARCHIVE_UPLOAD_DIR,
)
from app.template_engine import templates
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from types import SimpleNamespace
import uuid


async def main(meeting_id: int | None):
    async with AsyncSessionLocal() as db:
        stmt = (
            select(MeetingReport)
            .options(selectinload(MeetingReport.approved_by))
            .join(Document, MeetingReport.archived_doc_id == Document.id)
            .where(
                MeetingReport.status == ReportStatus.APPROUVE,
                MeetingReport.archived_doc_id.isnot(None),
                Document.mime_type == "text/html",
            )
        )
        if meeting_id is not None:
            stmt = stmt.where(MeetingReport.meeting_id == meeting_id)
        reports = (await db.execute(stmt)).scalars().all()

        if not reports:
            print("Aucun tracé archivé en HTML à régénérer.")
            return

        lodge = (await db.execute(select(LodgeSettings).limit(1))).scalar_one_or_none()
        fake_request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

        for report in reports:
            meeting_r = await db.execute(
                select(Meeting)
                .options(
                    selectinload(Meeting.attendances).selectinload(Attendance.member),
                    selectinload(Meeting.meeting_visitors).selectinload(MeetingVisitor.visitor),
                )
                .where(Meeting.id == report.meeting_id)
            )
            meeting = meeting_r.scalar_one()
            doc = (await db.execute(select(Document).where(Document.id == report.archived_doc_id))).scalar_one()

            print(f"Tracé tenue {meeting.meeting_number} du {meeting.meeting_date} — doc #{doc.id} « {doc.name} »...", end=" ")

            ctx_vars = await _trace_snapshot_context(db, meeting)
            archive_html = templates.get_template("pages/meetings/trace_archive.html").render({
                "request": fake_request, "meeting": meeting, "lodge": lodge,
                "grade_label": _grade_label,
                "approved_by": report.approved_by, "approved_at": report.approved_at,
                **ctx_vars,
            })

            try:
                pdf_bytes = _render_trace_pdf(archive_html)
            except Exception as e:
                print(f"ÉCHEC génération PDF : {e}")
                continue

            old_path = doc.storage_path
            new_filename = f"pv_{uuid.uuid4().hex}.pdf"
            new_path = os.path.join(TRACE_ARCHIVE_UPLOAD_DIR, new_filename)
            with open(new_path, "wb") as f:
                f.write(pdf_bytes)

            doc.original_filename = new_filename
            doc.storage_path = new_path
            doc.mime_type = "application/pdf"
            doc.file_size = len(pdf_bytes)

            if old_path and os.path.exists(old_path):
                os.remove(old_path)

            print("OK")

        await db.commit()
        print(f"\n{len(reports)} document(s) régénéré(s) en PDF.")


if __name__ == "__main__":
    mid = int(sys.argv[1]) if len(sys.argv) > 1 else None
    asyncio.run(main(mid))
