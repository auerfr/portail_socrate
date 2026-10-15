"""Router — Espace visiteur (accès portail restreint). Phase 1 : coquille +
auth. Phase 2 : contenu consultable, chacun gated par un opt-in explicite
posé par un officier — jamais de visibilité par défaut (voir
Meeting.visible_to_guests, Document.visible_to_guests). Toute page de ce
module dépend de require_guest_auth, jamais de require_auth (voir
app/dependencies.py)."""
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import require_guest_auth, verify_password, hash_password
from app.models.guest import GuestAccount
from app.models.identity import MasonicGrade
from app.models.meetings import Meeting, MeetingStatus
from app.routers.meetings import _type_label
from app.models.documents import Document, DocFolder, DocStatus
from app.models.lodge import LodgeSettings

router = APIRouter(prefix="/visiteur", tags=["visiteur"])
from app.template_engine import templates

# Le grade réel du visiteur plafonne tout contenu grade-gated qui lui est
# ouvert, en plus de l'opt-in visible_to_guests — un rang absent (compte pas
# encore renseigné) vaut 0 : ne voit que ce qui est ouvert à "ALL" (le plus
# restrictif), jamais "tout voir par défaut".
_GUEST_GRADE_RANK = {MasonicGrade.APPRENTI: 1, MasonicGrade.COMPAGNON: 2, MasonicGrade.MAITRE: 3}
_MEETING_GRADE_RANK = {"APPRENTI": 1, "COMPAGNON": 2, "MAITRE": 3, "ALL": 0}
_MIN_GRADE_RANK = {"ALL": 0, "APPRENTI": 1, "COMPAGNON": 2, "MAITRE": 3}


def _guest_rank(guest: GuestAccount) -> int:
    return _GUEST_GRADE_RANK.get(guest.masonic_grade, 0)


@router.get("/", response_class=HTMLResponse)
async def visiteur_dashboard(
    request: Request,
    guest: Annotated[GuestAccount, Depends(require_guest_auth)],
):
    return templates.TemplateResponse(request, "pages/visiteur/dashboard.html", {
        "guest": guest,
    })


# ── Programme (tenues explicitement ouvertes aux visiteurs) ────────────────

@router.get("/programme", response_class=HTMLResponse)
async def visiteur_programme(
    request: Request,
    guest: Annotated[GuestAccount, Depends(require_guest_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    r = await db.execute(
        select(Meeting)
        .where(
            Meeting.visible_to_guests.is_(True),
            Meeting.status == MeetingStatus.PUBLISHED,
            Meeting.meeting_date >= date.today(),
        )
        .order_by(Meeting.meeting_date, Meeting.meeting_time)
    )
    guest_rank = _guest_rank(guest)
    meetings = [
        m for m in r.scalars().all()
        if guest_rank >= _MEETING_GRADE_RANK.get(m.grade.value, 0)
    ]
    return templates.TemplateResponse(request, "pages/visiteur/programme.html", {
        "guest": guest,
        "meetings": meetings,
        "type_label": _type_label,
    })


# ── Annuaire restreint (VM / Secrétaire uniquement) ─────────────────────────

@router.get("/contacts", response_class=HTMLResponse)
async def visiteur_contacts(
    request: Request,
    guest: Annotated[GuestAccount, Depends(require_guest_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    r = await db.execute(select(LodgeSettings).limit(1))
    lodge = r.scalar_one_or_none()
    return templates.TemplateResponse(request, "pages/visiteur/contacts.html", {
        "guest": guest,
        "lodge": lodge,
    })


# ── Documents : propre opt-in, ou hérité du dossier/espace entier ──────────

def _doc_guest_visible(doc: Document, guest_rank: int) -> bool:
    """Un document est visible à ce visiteur si lui-même, son dossier ou son
    espace a été explicitement ouvert aux visiteurs, ET que le grade du
    visiteur couvre à la fois le grade minimum du dossier et de l'espace
    (même logique à deux niveaux que pour un membre — voir
    _get_authorized_doc dans app/routers/documents.py)."""
    folder = doc.folder
    if not folder or folder.personal_owner_id:
        return False  # jamais un dossier personnel, flag ou pas
    space = folder.space
    opened = bool(doc.visible_to_guests or folder.visible_to_guests or (space and space.visible_to_guests))
    if not opened:
        return False
    if guest_rank < _MIN_GRADE_RANK.get(folder.min_grade.value, 0):
        return False
    if space and guest_rank < _MIN_GRADE_RANK.get(space.min_grade.value, 0):
        return False
    return True


@router.get("/documents", response_class=HTMLResponse)
async def visiteur_documents(
    request: Request,
    guest: Annotated[GuestAccount, Depends(require_guest_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    r = await db.execute(
        select(Document)
        .options(selectinload(Document.folder).selectinload(DocFolder.space))
        .where(
            Document.status == DocStatus.PUBLISHED,
            Document.deleted_at.is_(None),
        )
        .order_by(Document.name)
    )
    guest_rank = _guest_rank(guest)
    documents = [d for d in r.scalars().all() if _doc_guest_visible(d, guest_rank)]
    return templates.TemplateResponse(request, "pages/visiteur/documents.html", {
        "guest": guest,
        "documents": documents,
    })


@router.get("/documents/{doc_id}/download")
async def visiteur_document_download(
    doc_id: int,
    guest: Annotated[GuestAccount, Depends(require_guest_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    r = await db.execute(
        select(Document)
        .options(selectinload(Document.folder).selectinload(DocFolder.space))
        .where(Document.id == doc_id)
    )
    doc = r.scalar_one_or_none()
    if (
        not doc
        or doc.status != DocStatus.PUBLISHED
        or doc.deleted_at is not None
        or not _doc_guest_visible(doc, _guest_rank(guest))
    ):
        raise HTTPException(status_code=404)

    if doc.link_url:
        return RedirectResponse(url=doc.link_url, status_code=302)

    if not doc.storage_path:
        raise HTTPException(status_code=404, detail="Fichier introuvable")
    path = Path(doc.storage_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Fichier introuvable sur le serveur")

    doc.download_count = (doc.download_count or 0) + 1
    await db.commit()
    return FileResponse(
        path=path,
        filename=doc.original_filename,
        media_type=doc.mime_type or "application/octet-stream",
    )


@router.get("/mot-de-passe", response_class=HTMLResponse)
async def visiteur_password_page(
    request: Request,
    guest: Annotated[GuestAccount, Depends(require_guest_auth)],
):
    return templates.TemplateResponse(request, "pages/visiteur/password.html", {
        "guest": guest,
    })


@router.post("/mot-de-passe", response_class=HTMLResponse)
async def visiteur_password_submit(
    request: Request,
    guest: Annotated[GuestAccount, Depends(require_guest_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    def error(msg: str):
        return templates.TemplateResponse(request, "pages/visiteur/password.html", {
            "guest": guest, "error": msg,
        }, status_code=400)

    if not guest.password_hash or not verify_password(current_password, guest.password_hash):
        return error("Mot de passe actuel incorrect.")
    if len(new_password) < 8:
        return error("Le nouveau mot de passe doit contenir au moins 8 caractères.")
    if new_password != confirm_password:
        return error("Les deux nouveaux mots de passe ne correspondent pas.")

    guest.password_hash = hash_password(new_password)
    await db.commit()
    return templates.TemplateResponse(request, "pages/visiteur/password.html", {
        "guest": guest, "success": True,
    })
