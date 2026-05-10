"""Router PV — Procès-verbaux de tenues"""
import os
import uuid
import logging
from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_auth
from app.models.meetings import Meeting, MeetingGrade
from app.models.reports import MeetingReport, ReportStatus
from app.models.identity import Member, MasonicGrade, LodgeFunction
from app.models.documents import DocSpace, DocFolder, Document, DocStatus, MinGrade, DocAccessMode

router = APIRouter(prefix="/reports", tags=["reports"])
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)

UPLOAD_DIR = "uploads/documents"

# ── Helpers de permission ───────────────────────────────────────────────────

def _can_write(user, member: Member) -> bool:
    """Secrétaire ou admin."""
    return user.is_admin or member.lodge_function in (
        LodgeFunction.SECRETAIRE,
        LodgeFunction.VM,
    )

def _can_approve(user, member: Member) -> bool:
    """VM ou admin."""
    return user.is_admin or member.lodge_function == LodgeFunction.VM

def _grade_level(grade) -> int:
    return {MasonicGrade.APPRENTI: 1, MasonicGrade.COMPAGNON: 2, MasonicGrade.MAITRE: 3}.get(grade, 0)

def _can_read(member: Member, meeting: Meeting) -> bool:
    """Membres dont le grade >= grade de la tenue."""
    if meeting.grade == MeetingGrade.ALL:
        return True
    grade_map = {
        MeetingGrade.APPRENTI: MasonicGrade.APPRENTI,
        MeetingGrade.COMPAGNON: MasonicGrade.COMPAGNON,
        MeetingGrade.MAITRE: MasonicGrade.MAITRE,
    }
    required = grade_map.get(meeting.grade)
    return required is None or _grade_level(member.masonic_grade) >= _grade_level(required)


# ── Récupérer ou créer le dossier GED pour les PV ──────────────────────────

async def _get_or_create_pv_folder(db: AsyncSession, year_label: str) -> DocFolder:
    """Trouve ou crée DocSpace 'Secrétariat' > DocFolder 'PV {année}'."""
    space_r = await db.execute(select(DocSpace).where(DocSpace.name == "Secrétariat").limit(1))
    space = space_r.scalar_one_or_none()
    if not space:
        space = DocSpace(
            name="Secrétariat",
            description="Documents officiels du Secrétariat",
            access_mode=DocAccessMode.GRADE,
            min_grade=MinGrade.APPRENTI,
            order_position=10,
        )
        db.add(space)
        await db.flush()

    folder_name = f"PV {year_label}"
    folder_r = await db.execute(
        select(DocFolder).where(
            DocFolder.space_id == space.id,
            DocFolder.parent_id.is_(None),
            DocFolder.name == folder_name,
        ).limit(1)
    )
    folder = folder_r.scalar_one_or_none()
    if not folder:
        folder = DocFolder(
            space_id=space.id,
            name=folder_name,
            description=f"Procès-verbaux de l'année {year_label}",
            min_grade=MinGrade.APPRENTI,
            order_position=0,
        )
        db.add(folder)
        await db.flush()

    return folder


# ── Routes ──────────────────────────────────────────────────────────────────

@router.get("/meeting/{meeting_id}", response_class=HTMLResponse)
async def report_view(
    meeting_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    meeting_r = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = meeting_r.scalar_one_or_none()
    if not meeting:
        raise HTTPException(404)
    if not _can_read(member, meeting):
        raise HTTPException(403, "Grade insuffisant pour lire ce PV")

    report_r = await db.execute(
        select(MeetingReport).where(MeetingReport.meeting_id == meeting_id)
    )
    report = report_r.scalar_one_or_none()

    return templates.TemplateResponse(request, "pages/reports/view.html", {
        "current_user": user,
        "current_member": member,
        "meeting": meeting,
        "report": report,
        "can_write": _can_write(user, member),
        "can_approve": _can_approve(user, member),
    })


@router.get("/meeting/{meeting_id}/edit", response_class=HTMLResponse)
async def report_edit_form(
    meeting_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not _can_write(user, member):
        raise HTTPException(403)

    meeting_r = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = meeting_r.scalar_one_or_none()
    if not meeting:
        raise HTTPException(404)

    report_r = await db.execute(
        select(MeetingReport).where(MeetingReport.meeting_id == meeting_id)
    )
    report = report_r.scalar_one_or_none()

    return templates.TemplateResponse(request, "pages/reports/edit.html", {
        "current_user": user,
        "current_member": member,
        "meeting": meeting,
        "report": report,
    })


@router.post("/meeting/{meeting_id}/save")
async def report_save(
    meeting_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    content: str = Form(""),
):
    user, member = ctx
    if not _can_write(user, member):
        raise HTTPException(403)

    meeting_r = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = meeting_r.scalar_one_or_none()
    if not meeting:
        raise HTTPException(404)

    report_r = await db.execute(
        select(MeetingReport).where(MeetingReport.meeting_id == meeting_id)
    )
    report = report_r.scalar_one_or_none()

    if not report:
        report = MeetingReport(
            meeting_id=meeting_id,
            author_id=member.id,
            status=ReportStatus.BROUILLON,
        )
        db.add(report)

    report.content = content
    report.updated_at = datetime.now()
    await db.commit()

    return RedirectResponse(
        url=f"/reports/meeting/{meeting_id}/edit?saved=1", status_code=303
    )


@router.post("/meeting/{meeting_id}/submit")
async def report_submit(
    meeting_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not _can_write(user, member):
        raise HTTPException(403)

    report_r = await db.execute(
        select(MeetingReport).where(MeetingReport.meeting_id == meeting_id)
    )
    report = report_r.scalar_one_or_none()
    if not report or not report.content:
        raise HTTPException(400, "PV vide, impossible de soumettre")
    if report.status != ReportStatus.BROUILLON:
        raise HTTPException(400, "Ce PV a déjà été soumis ou approuvé")

    report.status = ReportStatus.SOUMIS
    report.submitted_at = datetime.now()
    await db.commit()

    return RedirectResponse(
        url=f"/reports/meeting/{meeting_id}?submitted=1", status_code=303
    )


@router.post("/meeting/{meeting_id}/approve")
async def report_approve(
    meeting_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not _can_approve(user, member):
        raise HTTPException(403)

    report_r = await db.execute(
        select(MeetingReport).where(MeetingReport.meeting_id == meeting_id)
    )
    report = report_r.scalar_one_or_none()
    if not report:
        raise HTTPException(404)
    if report.status == ReportStatus.APPROUVE:
        raise HTTPException(400, "Déjà approuvé")

    meeting = report.meeting

    report.status = ReportStatus.APPROUVE
    report.approved_by_id = member.id
    report.approved_at = datetime.now()

    # ── Archivage GED ────────────────────────────────────────────────────────
    try:
        year_label = str(meeting.meeting_date.year)
        folder = await _get_or_create_pv_folder(db, year_label)

        from app.routers.meetings import MEETING_TYPE_LABELS
        type_label = MEETING_TYPE_LABELS.get(meeting.type.value, meeting.type.value)
        doc_name = f"PV — {type_label} — {meeting.meeting_date.strftime('%d/%m/%Y')}"

        os.makedirs(UPLOAD_DIR, exist_ok=True)
        filename = f"pv_{uuid.uuid4().hex}.html"
        storage_path = os.path.join(UPLOAD_DIR, filename)

        html_content = f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8">
<title>{doc_name}</title>
<style>body{{font-family:Georgia,serif;max-width:800px;margin:2rem auto;padding:0 1rem;line-height:1.7;color:#111}}</style>
</head><body>
<h1 style="font-size:1.2rem;color:#1a5252;">{doc_name}</h1>
<hr style="border-color:#d1fae5;margin:1rem 0;">
{report.content or ''}
<hr style="border-color:#e5e7eb;margin:2rem 0;">
<p style="font-size:0.8rem;color:#6b7280;">
Approuvé par {member.first_name} {member.last_name} le {report.approved_at.strftime('%d/%m/%Y')}</p>
</body></html>"""

        with open(storage_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        doc = Document(
            folder_id=folder.id,
            name=doc_name,
            original_filename=filename,
            mime_type="text/html",
            file_size=len(html_content.encode()),
            storage_path=storage_path,
            status=DocStatus.PUBLISHED,
            author_id=report.author_id,
            validated_by_id=member.id,
            validated_at=report.approved_at,
        )
        db.add(doc)
        await db.flush()
        report.archived_doc_id = doc.id

    except Exception as e:
        logger.warning("Archivage GED PV échoué : %s", e, exc_info=True)

    await db.commit()
    return RedirectResponse(
        url=f"/reports/meeting/{meeting_id}?approved=1", status_code=303
    )


@router.post("/meeting/{meeting_id}/reject")
async def report_reject(
    meeting_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not _can_approve(user, member):
        raise HTTPException(403)

    report_r = await db.execute(
        select(MeetingReport).where(MeetingReport.meeting_id == meeting_id)
    )
    report = report_r.scalar_one_or_none()
    if not report or report.status != ReportStatus.SOUMIS:
        raise HTTPException(400)

    report.status = ReportStatus.BROUILLON
    report.submitted_at = None
    await db.commit()

    return RedirectResponse(
        url=f"/reports/meeting/{meeting_id}?rejected=1", status_code=303
    )
