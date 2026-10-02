"""Router Planches & travaux maçonniques"""
import logging
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, Form, Request, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_auth, has_fine_permission
from app.models.planches import Planche, PlancheComment, PlancheStatus, PlancheGrade
from app.models.identity import Member, MasonicGrade, MemberStatus
from app.models.meetings import Meeting
from app.services.pdf_render import render_html_to_pdf
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
    ) or has_fine_permission(member, "can_manage_planches")


def _can_edit_planche(user, member: Member, planche: Planche) -> bool:
    return (user.is_admin or planche.author_id == member.id or planche.created_by_id == member.id
            or _can_write(user, member))


async def _form_context(db: AsyncSession) -> dict:
    """Listes du formulaire : toutes les tenues (plus récentes d'abord) et les
    membres pouvant être auteurs (actifs d'abord, puis anciens membres)."""
    meetings = (await db.execute(
        select(Meeting).order_by(Meeting.meeting_date.desc(), Meeting.id.desc())
    )).scalars().all()
    # Seul le compte technique placeholder créé par seed.py est exclu — pas
    # "tout membre admin" (un vrai F∴/S∴ qui a aussi des droits admin reste
    # un membre réel et peut parfaitement être auteur d'une planche ; bug
    # signalé le 03/10/2026 : AUER, admin réel, absent de cette liste).
    members = [m for m in (await db.execute(select(Member))).scalars().all() if m.email != "admin@loge.local"]
    return {
        "meetings": meetings,
        "author_members": sorted(members, key=lambda m: (m.status != MemberStatus.ACTIVE, m.last_name.upper(), m.first_name)),
    }


def _apply_author(planche: Planche, user, member: Member, author_mode: str,
                  author_member_id: str, author_name: str, author_lodge: str) -> None:
    """Auteur de la planche : un membre de la loge, ou un auteur extérieur et sa
    loge. Seuls les gestionnaires des planches peuvent désigner un autre auteur
    qu'eux-mêmes ; sinon l'auteur est la personne qui dépose."""
    if not _can_write(user, member):
        if planche.author_id is None and not planche.author_name:
            planche.author_id = member.id
        return
    if author_mode == "external" and author_name.strip():
        planche.author_id = None
        planche.author_name = " ".join(author_name.split())
        planche.author_lodge = " ".join(author_lodge.split()) or None
    else:
        planche.author_id = int(author_member_id) if author_member_id.isdigit() else member.id
        planche.author_name = planche.author_lodge = None


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
    """Trouve ou crée DocSpace 'Travaux Socrate' > DocFolder 'Planches {année}'.

    Ciblait auparavant un espace nommé en dur 'Bibliothèque', distinct de
    l'espace 'Travaux Socrate' où la loge organise réellement ses planches
    — les planches soumises via l'app atterrissaient donc dans un espace
    à part, invisible de l'organisation habituelle (constaté le 02/10/2026,
    3 planches retrouvées dans ce mauvais espace)."""
    space_r = await db.execute(select(DocSpace).where(DocSpace.name == "Travaux Socrate").limit(1))
    space = space_r.scalar_one_or_none()
    if not space:
        space = DocSpace(
            name="Travaux Socrate",
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
    # Les plus récentes d'abord : date de la tenue où la planche a été présentée,
    # à défaut sa date de publication (les planches de la bibliothèque sont
    # rattachées après coup, leur date de création ne veut rien dire)
    published = sorted(
        (p for p in planches if p.status == PlancheStatus.PUBLIE),
        key=lambda p: p.meeting.meeting_date if p.meeting else (p.published_at or p.created_at).date(),
        reverse=True,
    )
    drafts    = [p for p in planches if p.status == PlancheStatus.BROUILLON
                 and (p.author_id == member.id or p.created_by_id == member.id
                      or user.is_admin or _can_write(user, member))]

    return templates.TemplateResponse(request, "pages/planches/list.html", {
        "current_user": user,
        "current_member": member,
        "published": published,
        "published_years": sorted({
            (p.meeting.meeting_date if p.meeting else (p.published_at or p.created_at)).year for p in published
        }, reverse=True),
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

    form_ctx = await _form_context(db)

    return templates.TemplateResponse(request, "pages/planches/edit.html", {
        "current_user": user,
        "current_member": member,
        "planche": None,
        **form_ctx,
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
    author_mode: str = Form("member"),
    author_member_id: str = Form(""),
    author_name: str = Form(""),
    author_lodge: str = Form(""),
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
        created_by_id=member.id,
        meeting_id=int(meeting_id) if meeting_id.isdigit() else None,
        status=PlancheStatus.PUBLIE if action == "publish" else PlancheStatus.BROUILLON,
        published_at=datetime.now() if action == "publish" else None,
        file_path=file_path,
        original_filename=original_filename,
        mime_type=mime_type,
        file_size=file_size,
    )
    _apply_author(p, user, member, author_mode, author_member_id, author_name, author_lodge)
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

    # Type du document de la bibliothèque (aperçu PDF possible ou non)
    library_mime = None
    if planche.library_doc_id and not planche.file_path:
        library_doc = await db.get(Document, planche.library_doc_id)
        library_mime = library_doc.mime_type if library_doc else None

    return templates.TemplateResponse(request, "pages/planches/detail.html", {
        "current_user": user,
        "current_member": member,
        "planche": planche,
        "library_mime": library_mime,
        "can_edit": _can_edit_planche(user, member, planche),
        "can_comment": _can_read(member, planche),
    })


@router.get("/{planche_id}/pdf")
async def planche_export_pdf(
    planche_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Télécharge en PDF une planche rédigée en ligne (pas pour celles
    déposées comme fichier, déjà dans le format d'origine de l'auteur)."""
    user, member = ctx
    planche = await db.get(Planche, planche_id)
    if not planche:
        raise HTTPException(404)
    if not _can_read(member, planche):
        raise HTTPException(403, "Grade insuffisant")
    if planche.status == PlancheStatus.BROUILLON and not _can_edit_planche(user, member, planche):
        raise HTTPException(403, "Brouillon non accessible")
    if planche.file_path:
        raise HTTPException(400, "Cette planche a été déposée comme fichier, pas rédigée en ligne")
    if not planche.content:
        raise HTTPException(400, "Cette planche n'a pas encore de contenu")

    if planche.author:
        author_label = f"{planche.author.first_name} {planche.author.last_name}"
    elif planche.author_name:
        author_label = f"{planche.author_name} ({planche.author_lodge or 'autre loge'})"
    else:
        author_label = ""

    html = f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8">
<title>{planche.title}</title>
<style>
body{{font-family:Georgia,serif;max-width:800px;margin:2rem auto;padding:0 1rem;line-height:1.7;color:#111}}
h1{{color:#1a5252;font-size:1.4rem;border-bottom:1px solid #d1fae5;padding-bottom:0.5rem}}
h2{{color:#1a5252;font-size:1.1rem}}
.meta{{color:#666;font-size:0.9rem;margin-bottom:1.5rem}}
</style>
</head><body>
<h1>{planche.title}</h1>
{f'<p class="meta">Par {author_label}</p>' if author_label else ''}
{planche.content}
</body></html>"""

    try:
        pdf_bytes = render_html_to_pdf(html)
    except Exception as e:
        logger.warning("Export PDF de la planche #%s échoué : %s", planche_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="Échec de la génération du PDF")

    filename = f"planche_{planche_id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


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

    form_ctx = await _form_context(db)

    return templates.TemplateResponse(request, "pages/planches/edit.html", {
        "current_user": user,
        "current_member": member,
        "planche": planche,
        **form_ctx,
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
    author_mode: str = Form("member"),
    author_member_id: str = Form(""),
    author_name: str = Form(""),
    author_lodge: str = Form(""),
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
    _apply_author(planche, user, member, author_mode, author_member_id, author_name, author_lodge)
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
    inline: bool = False,
):
    """Fichier de la planche — téléchargé, ou affiché dans la page (inline=1,
    pour l'aperçu PDF / image)."""
    user, member = ctx
    planche = await db.get(Planche, planche_id)
    if not planche:
        raise HTTPException(404)
    if not _can_read(member, planche):
        raise HTTPException(403)
    if not planche.file_path and planche.library_doc_id:
        # Planche de la bibliothèque : consultation via la GED (et ses droits d'accès)
        target = "preview" if inline else "view"
        return RedirectResponse(url=f"/documents/file/{planche.library_doc_id}/{target}", status_code=303)
    if not planche.file_path:
        raise HTTPException(404)
    if not Path(planche.file_path).exists():
        raise HTTPException(404, "Fichier introuvable")
    return FileResponse(
        planche.file_path,
        filename=planche.original_filename or "planche",
        media_type=planche.mime_type or "application/octet-stream",
        content_disposition_type="inline" if inline else "attachment",
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
