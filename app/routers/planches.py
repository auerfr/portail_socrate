"""Router Planches & travaux maçonniques"""
import logging
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, Form, Request, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_auth
from app.models.planches import Planche, PlancheComment, PlancheStatus, PlancheGrade
from app.models.identity import Member, MasonicGrade
from app.models.meetings import Meeting
from app.models.documents import (
    DocSpace, DocFolder, Document, DocStatus, MinGrade, DocAccessMode
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/planches", tags=["planches"])
from app.template_engine import templates
UPLOAD_DIR = Path("uploads/planches")

_GRADE_LEVEL = {
    MasonicGrade.APPRENTI: 1,
    MasonicGrade.COMPAGNON: 2,
    MasonicGrade.MAITRE: 3,
}
_PLANCHE_GRADE_LEVEL = {
    PlancheGrade.TOUS: 0,
    PlancheGrade.APPRENTI: 1,
    PlancheGrade.COMPAGNON: 2,
    PlancheGrade.MAITRE: 3,
}


def _can_read(member: Member, planche: Planche) -> bool:
    if planche.grade == PlancheGrade.TOUS:
        return True
    member_lvl = _GRADE_LEVEL.get(member.masonic_grade, 0)
    required   = _PLANCHE_GRADE_LEVEL.get(planche.grade, 0)
    return member_lvl >= required


def _can_write(user, member: Member) -> bool:
    from app.models.identity import LodgeFunction
    return user.is_admin or member.lodge_function in (
        LodgeFunction.SECRETAIRE, LodgeFunction.VM, LodgeFunction.ORATEUR,
    )


def _can_edit_planche(user, member: Member, planche: Planche) -> bool:
    return user.is_admin or planche.author_id == member.id or _can_write(user, member)


async def _push_publish_planche(planche: Planche, db, sender_member_id: int) -> None:
    """Push notification aux membres éligibles à la lecture d'une planche publiée."""
    try:
        from app.models.identity import MemberStatus
        from app.services.push import send_push_broadcast
        r = await db.execute(select(Member).where(Member.status == MemberStatus.ACTIVE))
        eligible_ids = [
            m.id for m in r.scalars().all()
            if m.id != sender_member_id and _can_read(m, planche)
        ]
        if not eligible_ids:
            return
        await send_push_broadcast(
            db, eligible_ids,
            f"📝 Nouvelle planche : {planche.title[:60]}",
            "Cliquez pour la consulter dans la bibliothèque.",
            f"/planches/{planche.id}",
        )
    except Exception:
        pass


# ── Archivage GED ────────────────────────────────────────────────────────────

_PLANCHE_GED_GRADE = {
    PlancheGrade.TOUS:      MinGrade.APPRENTI,  # visible par tous les membres avec compte
    PlancheGrade.APPRENTI:  MinGrade.APPRENTI,
    PlancheGrade.COMPAGNON: MinGrade.COMPAGNON,
    PlancheGrade.MAITRE:    MinGrade.MAITRE,
}


async def _get_or_create_planches_folder(db: AsyncSession, year_label: str, min_grade: MinGrade) -> DocFolder:
    """Trouve ou crée DocSpace 'Bibliothèque' > DocFolder 'Planches {année}'."""
    space_r = await db.execute(select(DocSpace).where(DocSpace.name == "Bibliothèque").limit(1))
    space = space_r.scalar_one_or_none()
    if not space:
        space = DocSpace(
            name="Bibliothèque",
            description="Travaux et planches de la loge",
            access_mode=DocAccessMode.GRADE,
            min_grade=MinGrade.APPRENTI,
            order_position=20,
        )
        db.add(space)
        await db.flush()

    folder_name = f"Planches {year_label}"
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
            description=f"Travaux présentés en loge — {year_label}",
            min_grade=min_grade,
            order_position=0,
        )
        db.add(folder)
        await db.flush()
    return folder


async def _archive_planche_to_ged(planche: Planche, db: AsyncSession) -> None:
    """Crée (ou met à jour) un Document GED pour la planche publiée."""
    year_label = str((planche.published_at or datetime.now()).year)
    min_grade = _PLANCHE_GED_GRADE.get(planche.grade, MinGrade.APPRENTI)
    folder = await _get_or_create_planches_folder(db, year_label, min_grade)

    doc_name = f"Planche — {planche.title}"

    # Préparer le fichier à archiver
    if planche.file_path and Path(planche.file_path).exists():
        # Recopie du fichier dans uploads/documents/
        os.makedirs("uploads/documents", exist_ok=True)
        ext = Path(planche.file_path).suffix
        new_fname = f"planche_{uuid.uuid4().hex}{ext}"
        storage_path = os.path.join("uploads/documents", new_fname)
        shutil.copyfile(planche.file_path, storage_path)
        original_filename = planche.original_filename or new_fname
        mime_type = planche.mime_type or "application/octet-stream"
        file_size = Path(storage_path).stat().st_size
    else:
        # HTML rédigé → wrapper HTML
        os.makedirs("uploads/documents", exist_ok=True)
        new_fname = f"planche_{uuid.uuid4().hex}.html"
        storage_path = os.path.join("uploads/documents", new_fname)
        html = f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8">
<title>{planche.title}</title>
<style>body{{font-family:Georgia,serif;max-width:800px;margin:2rem auto;padding:0 1rem;line-height:1.7;color:#111}}
h1{{color:#1a5252;font-size:1.4rem;border-bottom:1px solid #d1fae5;padding-bottom:0.5rem}}
h2{{color:#1a5252;font-size:1.1rem}}</style>
</head><body>
<h1>{planche.title}</h1>
{planche.content or ''}
</body></html>"""
        Path(storage_path).write_text(html, encoding="utf-8")
        original_filename = new_fname
        mime_type = "text/html"
        file_size = len(html.encode())

    # Si déjà archivé : mettre à jour le doc existant
    if planche.archived_doc_id:
        existing = await db.get(Document, planche.archived_doc_id)
        if existing:
            existing.name = doc_name
            existing.original_filename = original_filename
            existing.mime_type = mime_type
            existing.file_size = file_size
            # Supprimer l'ancien fichier physique
            if existing.storage_path and Path(existing.storage_path).exists():
                try: Path(existing.storage_path).unlink()
                except Exception: pass
            existing.storage_path = storage_path
            existing.folder_id = folder.id
            return

    # Sinon : créer un nouveau doc
    doc = Document(
        folder_id=folder.id,
        name=doc_name,
        original_filename=original_filename,
        mime_type=mime_type,
        file_size=file_size,
        storage_path=storage_path,
        status=DocStatus.PUBLISHED,
        author_id=planche.author_id,
    )
    db.add(doc)
    await db.flush()
    planche.archived_doc_id = doc.id


async def _unarchive_planche(planche: Planche, db: AsyncSession) -> None:
    """Supprime le Document GED associé (en cas de dépublication / suppression)."""
    if not planche.archived_doc_id:
        return
    doc = await db.get(Document, planche.archived_doc_id)
    if doc:
        if doc.storage_path and Path(doc.storage_path).exists():
            try: Path(doc.storage_path).unlink()
            except Exception: pass
        await db.delete(doc)
    planche.archived_doc_id = None


# ── Liste ────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def planches_list(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx

    result = await db.execute(
        select(Planche).order_by(Planche.created_at.desc())
    )
    all_planches = result.scalars().all()

    # Filtre par grade
    planches = [p for p in all_planches if _can_read(member, p)]
    published = [p for p in planches if p.status == PlancheStatus.PUBLIE]
    drafts    = [p for p in planches if p.status == PlancheStatus.BROUILLON
                 and (p.author_id == member.id or user.is_admin or _can_write(user, member))]

    return templates.TemplateResponse(request, "pages/planches/list.html", {
        "current_user": user,
        "current_member": member,
        "published": published,
        "drafts": drafts,
        "can_write": _can_write(user, member),
    })


# ── Nouvelle planche ─────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
async def planche_new(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx

    meetings_r = await db.execute(
        select(Meeting).order_by(Meeting.meeting_date.desc()).limit(30)
    )
    meetings = meetings_r.scalars().all()

    return templates.TemplateResponse(request, "pages/planches/edit.html", {
        "current_user": user,
        "current_member": member,
        "planche": None,
        "meetings": meetings,
        "can_write": _can_write(user, member),
    })


@router.post("/new")
async def planche_create(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    title: str = Form(""),
    content: str = Form(""),
    grade: str = Form("TOUS"),
    meeting_id: str = Form(""),
    action: str = Form("draft"),
    upload: Optional[UploadFile] = File(None),
):
    user, member = ctx

    file_path = original_filename = mime_type = None
    file_size = None
    if upload and upload.filename:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        ext = Path(upload.filename).suffix.lower()
        fname = f"planche_{uuid.uuid4().hex}{ext}"
        dest = UPLOAD_DIR / fname
        data = await upload.read()
        dest.write_bytes(data)
        file_path = str(dest)
        original_filename = upload.filename
        mime_type = upload.content_type
        file_size = len(data)

    p = Planche(
        title=title.strip() or "Sans titre",
        content=content if not file_path else None,
        grade=PlancheGrade(grade) if grade in PlancheGrade.__members__ else PlancheGrade.TOUS,
        author_id=member.id,
        meeting_id=int(meeting_id) if meeting_id.isdigit() else None,
        status=PlancheStatus.PUBLIE if action == "publish" else PlancheStatus.BROUILLON,
        published_at=datetime.now() if action == "publish" else None,
        file_path=file_path,
        original_filename=original_filename,
        mime_type=mime_type,
        file_size=file_size,
    )
    db.add(p)
    await db.flush()
    if p.status == PlancheStatus.PUBLIE:
        try:
            await _archive_planche_to_ged(p, db)
        except Exception as e:
            logger.warning("Archivage GED planche échoué : %s", e, exc_info=True)
    await db.commit()
    await db.refresh(p)
    if p.status == PlancheStatus.PUBLIE:
        await _push_publish_planche(p, db, member.id)
    return RedirectResponse(url=f"/planches/{p.id}", status_code=303)


# ── Détail ───────────────────────────────────────────────────────────────────

@router.get("/{planche_id}", response_class=HTMLResponse)
async def planche_detail(
    planche_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    planche = await db.get(Planche, planche_id)
    if not planche:
        raise HTTPException(404)
    if not _can_read(member, planche):
        raise HTTPException(403, "Grade insuffisant")
    if planche.status == PlancheStatus.BROUILLON and not _can_edit_planche(user, member, planche):
        raise HTTPException(403, "Brouillon non accessible")

    # Audit consultation (si activé via /admin/confidentiality)
    try:
        from app.services.confidentiality import maybe_audit_view
        await maybe_audit_view(
            db, actor_id=member.id,
            resource_type="planche", resource_id=planche.id,
            target_label=planche.title,
            request=request,
        )
    except Exception:
        pass

    return templates.TemplateResponse(request, "pages/planches/detail.html", {
        "current_user": user,
        "current_member": member,
        "planche": planche,
        "can_edit": _can_edit_planche(user, member, planche),
        "can_comment": _can_read(member, planche),
    })


# ── Édition ──────────────────────────────────────────────────────────────────

@router.get("/{planche_id}/edit", response_class=HTMLResponse)
async def planche_edit_form(
    planche_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    planche = await db.get(Planche, planche_id)
    if not planche:
        raise HTTPException(404)
    if not _can_edit_planche(user, member, planche):
        raise HTTPException(403)

    meetings_r = await db.execute(
        select(Meeting).order_by(Meeting.meeting_date.desc()).limit(30)
    )
    meetings = meetings_r.scalars().all()

    return templates.TemplateResponse(request, "pages/planches/edit.html", {
        "current_user": user,
        "current_member": member,
        "planche": planche,
        "meetings": meetings,
        "can_write": _can_write(user, member),
    })


@router.post("/{planche_id}/edit")
async def planche_edit_save(
    planche_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    title: str = Form(""),
    content: str = Form(""),
    grade: str = Form("TOUS"),
    meeting_id: str = Form(""),
    action: str = Form("draft"),
    upload: Optional[UploadFile] = File(None),
):
    user, member = ctx
    planche = await db.get(Planche, planche_id)
    if not planche:
        raise HTTPException(404)
    if not _can_edit_planche(user, member, planche):
        raise HTTPException(403)

    planche.title   = title.strip() or planche.title
    planche.grade   = PlancheGrade(grade) if grade in PlancheGrade.__members__ else planche.grade
    planche.meeting_id = int(meeting_id) if meeting_id.isdigit() else None
    planche.updated_at = datetime.now()

    if upload and upload.filename:
        # Remplace le fichier existant
        if planche.file_path:
            try:
                Path(planche.file_path).unlink(missing_ok=True)
            except Exception:
                pass
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        ext = Path(upload.filename).suffix.lower()
        fname = f"planche_{uuid.uuid4().hex}{ext}"
        dest = UPLOAD_DIR / fname
        data = await upload.read()
        dest.write_bytes(data)
        planche.file_path = str(dest)
        planche.original_filename = upload.filename
        planche.mime_type = upload.content_type
        planche.file_size = len(data)
        planche.content = None
    else:
        planche.content = content

    publish_now = False
    if action == "publish" and planche.status == PlancheStatus.BROUILLON:
        planche.status = PlancheStatus.PUBLIE
        planche.published_at = datetime.now()
        publish_now = True
    elif action == "unpublish":
        planche.status = PlancheStatus.BROUILLON
        try:
            await _unarchive_planche(planche, db)
        except Exception as e:
            logger.warning("Désarchivage GED planche échoué : %s", e, exc_info=True)

    # Re-archiver si publiée (création OU mise à jour du doc GED existant)
    if planche.status == PlancheStatus.PUBLIE:
        try:
            await _archive_planche_to_ged(planche, db)
        except Exception as e:
            logger.warning("Archivage GED planche échoué : %s", e, exc_info=True)

    await db.commit()
    if publish_now:
        await _push_publish_planche(planche, db, member.id)
    return RedirectResponse(url=f"/planches/{planche_id}?saved=1", status_code=303)


# ── Téléchargement fichier ────────────────────────────────────────────────────

@router.get("/{planche_id}/download")
async def planche_download(
    planche_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    planche = await db.get(Planche, planche_id)
    if not planche or not planche.file_path:
        raise HTTPException(404)
    if not _can_read(member, planche):
        raise HTTPException(403)
    if not Path(planche.file_path).exists():
        raise HTTPException(404, "Fichier introuvable")
    return FileResponse(
        planche.file_path,
        filename=planche.original_filename or "planche",
        media_type=planche.mime_type or "application/octet-stream",
    )


# ── Suppression ──────────────────────────────────────────────────────────────

@router.post("/{planche_id}/delete")
async def planche_delete(
    planche_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    planche = await db.get(Planche, planche_id)
    if not planche:
        raise HTTPException(404)
    if not _can_edit_planche(user, member, planche):
        raise HTTPException(403)
    try:
        await _unarchive_planche(planche, db)
    except Exception as e:
        logger.warning("Désarchivage GED échoué : %s", e, exc_info=True)
    # Supprimer le fichier source de la planche aussi
    if planche.file_path:
        try: Path(planche.file_path).unlink(missing_ok=True)
        except Exception: pass
    await db.delete(planche)
    await db.commit()
    return RedirectResponse(url="/planches/", status_code=303)


# ── Commentaires ─────────────────────────────────────────────────────────────

@router.post("/{planche_id}/comments")
async def planche_add_comment(
    planche_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    content: str = Form(""),
):
    user, member = ctx
    planche = await db.get(Planche, planche_id)
    if not planche or not _can_read(member, planche):
        raise HTTPException(403)
    if not content.strip():
        return RedirectResponse(url=f"/planches/{planche_id}", status_code=303)

    comment = PlancheComment(
        planche_id=planche_id,
        author_id=member.id,
        content=content.strip(),
    )
    db.add(comment)
    await db.commit()
    return RedirectResponse(url=f"/planches/{planche_id}#comments", status_code=303)


@router.post("/{planche_id}/comments/{comment_id}/delete")
async def planche_delete_comment(
    planche_id: int,
    comment_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    comment = await db.get(PlancheComment, comment_id)
    if not comment or comment.planche_id != planche_id:
        raise HTTPException(404)
    if not (user.is_admin or comment.author_id == member.id or _can_write(user, member)):
        raise HTTPException(403)
    await db.delete(comment)
    await db.commit()
    return RedirectResponse(url=f"/planches/{planche_id}#comments", status_code=303)
