"""Router — Partage de documents GED (liens externes, chat, réunions, événements)"""
import hashlib
import secrets
from datetime import datetime, timedelta, date
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import require_admin, require_auth
from app.models.documents import DocShare, Document, DocStatus, DocFolder, DocSpace
from app.models.chat import ChatChannel, ChatMessage, MessageContentType
from app.models.meetings import Meeting
from app.models.lodge_calendar import LodgeEvent

# ── Deux routers : un avec préfixe /documents (auth), un sans (public) ────────
router = APIRouter(prefix="/documents", tags=["sharing"])
public_router = APIRouter(tags=["sharing-public"])

templates = Jinja2Templates(directory="app/templates")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _hash_pwd(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def _check_pwd(password: str, hashed: str) -> bool:
    return hashlib.sha256(password.encode()).hexdigest() == hashed


def _share_to_dict(share: DocShare, request: Request) -> dict:
    base_url = str(request.base_url).rstrip("/")
    return {
        "id": share.id,
        "token": share.token,
        "label": share.label,
        "expires_at": share.expires_at.isoformat() if share.expires_at else None,
        "max_uses": share.max_uses,
        "use_count": share.use_count,
        "is_active": share.is_active,
        "created_at": share.created_at.isoformat() if share.created_at else None,
        "url": f"{base_url}/share/{share.token}",
    }


async def _get_doc_for_sharing(doc_id: int, db: AsyncSession) -> Document:
    doc = await db.get(Document, doc_id, options=[selectinload(Document.folder)])
    if not doc or doc.status != DocStatus.PUBLISHED or doc.deleted_at:
        raise HTTPException(status_code=404, detail="Document introuvable")
    return doc


# ─────────────────────────────────────────────────────────────────────────────
# 1. Créer un lien de partage externe
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/file/{doc_id}/share/external")
async def create_external_share(
    request: Request,
    doc_id: int,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    label: str = Form(""),
    expires_days: int = Form(0),
    max_uses: int = Form(0),
    password: str = Form(""),
):
    user, member = ctx
    await _get_doc_for_sharing(doc_id, db)  # vérif existence

    token = secrets.token_urlsafe(32)
    expires_at = (
        datetime.utcnow() + timedelta(days=expires_days)
        if expires_days > 0
        else None
    )
    password_hash = _hash_pwd(password) if password.strip() else None

    share = DocShare(
        document_id=doc_id,
        token=token,
        label=label.strip() or None,
        expires_at=expires_at,
        max_uses=max_uses if max_uses > 0 else None,
        password_hash=password_hash,
        is_active=True,
        created_by_id=member.id,
    )
    db.add(share)
    await db.commit()

    base_url = str(request.base_url).rstrip("/")
    return {"token": token, "url": f"{base_url}/share/{token}"}


# ─────────────────────────────────────────────────────────────────────────────
# 2. Lister les partages d'un document
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/file/{doc_id}/shares")
async def list_shares(
    request: Request,
    doc_id: int,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await _get_doc_for_sharing(doc_id, db)
    result = await db.execute(
        select(DocShare)
        .where(DocShare.document_id == doc_id)
        .order_by(DocShare.created_at.desc())
    )
    shares = result.scalars().all()
    return [_share_to_dict(s, request) for s in shares]


# ─────────────────────────────────────────────────────────────────────────────
# 3. Révoquer un partage
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/share/{share_id}/revoke")
async def revoke_share(
    share_id: int,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    share = await db.get(DocShare, share_id)
    if not share:
        raise HTTPException(status_code=404, detail="Partage introuvable")
    share.is_active = False
    await db.commit()
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# 4. Partager dans un canal de chat
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/file/{doc_id}/share/chat")
async def share_to_chat(
    request: Request,
    doc_id: int,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    channel_id: int = Form(...),
    message: str = Form(""),
):
    user, member = ctx
    doc = await _get_doc_for_sharing(doc_id, db)

    channel = await db.get(ChatChannel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Canal introuvable")

    base_url = str(request.base_url).rstrip("/")
    download_url = f"{base_url}/documents/file/{doc_id}/download"
    user_msg = message.strip()
    content = (
        f"{user_msg}\n\n" if user_msg else ""
    ) + f"📄 **{doc.name}**\n{download_url}"

    chat_msg = ChatMessage(
        channel_id=channel_id,
        sender_id=member.id,
        content=content,
        content_type=MessageContentType.TEXT,
    )
    db.add(chat_msg)
    await db.commit()
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# 5. Attacher à une réunion
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/file/{doc_id}/share/meeting")
async def share_to_meeting(
    request: Request,
    doc_id: int,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    meeting_id: int = Form(...),
):
    user, member = ctx
    doc = await _get_doc_for_sharing(doc_id, db)
    meeting = await db.get(Meeting, meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Tenue introuvable")

    base_url = str(request.base_url).rstrip("/")
    download_url = f"{base_url}/documents/file/{doc_id}/download"
    link_html = (
        f'<p><a href="{download_url}" target="_blank" rel="noopener">'
        f'Document : {doc.name}</a></p>'
    )
    current = meeting.agenda_html or ""
    meeting.agenda_html = current + "\n" + link_html
    await db.commit()
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# 6. Attacher à un événement
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/file/{doc_id}/share/event")
async def share_to_event(
    request: Request,
    doc_id: int,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    event_id: int = Form(...),
):
    user, member = ctx
    doc = await _get_doc_for_sharing(doc_id, db)
    event = await db.get(LodgeEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Événement introuvable")

    base_url = str(request.base_url).rstrip("/")
    download_url = f"{base_url}/documents/file/{doc_id}/download"
    link_html = (
        f'<p><a href="{download_url}" target="_blank" rel="noopener">'
        f'Document : {doc.name}</a></p>'
    )
    current = event.description or ""
    event.description = current + "\n" + link_html
    await db.commit()
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# 7. Cibles de partage (réunions, événements, canaux)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/share-targets")
async def share_targets(
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    today = date.today()
    now_dt = datetime.combine(today, datetime.min.time())

    # Prochaines réunions (10)
    mtg_r = await db.execute(
        select(Meeting)
        .where(Meeting.meeting_date >= today)
        .order_by(Meeting.meeting_date)
        .limit(10)
    )
    meetings = [
        {
            "id": m.id,
            "meeting_date": m.meeting_date.isoformat(),
            "grade": m.grade,
            "type": m.type,
            "label": f"{m.meeting_date.strftime('%d/%m/%Y')} — {m.type} ({m.grade})",
        }
        for m in mtg_r.scalars().all()
    ]

    # Prochains événements (10)
    ev_r = await db.execute(
        select(LodgeEvent)
        .where(LodgeEvent.start_datetime >= now_dt)
        .order_by(LodgeEvent.start_datetime)
        .limit(10)
    )
    events = [
        {
            "id": e.id,
            "title": e.title,
            "date_start": e.start_datetime.isoformat(),
            "label": f"{e.start_datetime.strftime('%d/%m/%Y')} — {e.title}",
        }
        for e in ev_r.scalars().all()
    ]

    # Canaux disponibles
    ch_r = await db.execute(
        select(ChatChannel).order_by(ChatChannel.name)
    )
    channels = [
        {"id": c.id, "name": c.name, "type": c.type}
        for c in ch_r.scalars().all()
    ]

    return {"meetings": meetings, "events": events, "chat_channels": channels}


# ─────────────────────────────────────────────────────────────────────────────
# 8. Routes publiques — accès sans authentification
# ─────────────────────────────────────────────────────────────────────────────

async def _resolve_share(token: str, db: AsyncSession) -> DocShare | None:
    """Vérifie token, is_active, expiration, use_count. Retourne le share ou None."""
    result = await db.execute(
        select(DocShare)
        .options(selectinload(DocShare.document))
        .where(DocShare.token == token)
    )
    share = result.scalar_one_or_none()
    if not share:
        return None
    if not share.is_active:
        return None
    if share.expires_at and share.expires_at < datetime.utcnow():
        return None
    if share.max_uses is not None and share.use_count >= share.max_uses:
        return None
    return share


@public_router.get("/share/{token}", response_class=HTMLResponse)
async def public_share_view(
    request: Request,
    token: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    pwd: Optional[str] = None,
):
    share = await _resolve_share(token, db)
    if not share:
        return templates.TemplateResponse(
            request, "pages/sharing/expired.html", {"token": token}
        )

    # Lien protégé par mot de passe
    if share.password_hash:
        if not pwd:
            return templates.TemplateResponse(
                request, "pages/sharing/password.html", {"token": token}
            )
        if not _check_pwd(pwd, share.password_hash):
            return templates.TemplateResponse(
                request,
                "pages/sharing/password.html",
                {"token": token, "error": "Mot de passe incorrect"},
            )

    # Incrémente le compteur
    share.use_count += 1
    await db.commit()

    # Calcul alerte expiration proche (< 24h)
    expiring_soon = (
        share.expires_at
        and share.expires_at - datetime.utcnow() < timedelta(hours=24)
    )

    doc = share.document
    file_size_str = _format_size(doc.file_size) if doc.file_size else None

    return templates.TemplateResponse(
        request,
        "pages/sharing/view.html",
        {
            "share": share,
            "doc": doc,
            "file_size_str": file_size_str,
            "expiring_soon": expiring_soon,
            "token": token,
        },
    )


@public_router.get("/share/{token}/download")
async def public_share_download(
    token: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    pwd: Optional[str] = None,
):
    share = await _resolve_share(token, db)
    if not share:
        raise HTTPException(status_code=410, detail="Lien expiré ou invalide")

    if share.password_hash and (not pwd or not _check_pwd(pwd, share.password_hash)):
        raise HTTPException(status_code=403, detail="Mot de passe requis")

    doc = share.document
    share.use_count += 1
    await db.commit()

    if doc.link_url:
        return RedirectResponse(url=doc.link_url, status_code=302)

    if not doc.storage_path:
        raise HTTPException(status_code=404, detail="Fichier introuvable")

    path = Path(doc.storage_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Fichier introuvable sur le serveur")

    return FileResponse(
        path=path,
        filename=doc.original_filename or doc.name,
        media_type=doc.mime_type or "application/octet-stream",
    )


def _format_size(size: int) -> str:
    if size > 1_048_576:
        return f"{size / 1_048_576:.1f} Mo"
    if size > 1024:
        return f"{size // 1024} Ko"
    return f"{size} o"
