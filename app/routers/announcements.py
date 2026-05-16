"""Router Annonces internes"""
from datetime import date
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_auth, can_manage_members
from app.models.identity import Member
from app.models.communication import Announcement, AnnouncementRead

router = APIRouter(prefix="/announcements", tags=["announcements"])
from app.template_engine import templates


def _require_manager(user, member):
    if not (can_manage_members(member) or user.is_admin):
        raise HTTPException(status_code=403, detail="Réservé au VM et Secrétaire")


# ── Liste + gestion ───────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def announcements_index(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    _require_manager(user, member)

    result = await db.execute(
        select(Announcement)
        .options(selectinload(Announcement.author), selectinload(Announcement.reads))
        .order_by(Announcement.is_pinned.desc(), Announcement.created_at.desc())
    )
    announcements = result.scalars().all()

    return templates.TemplateResponse(request, "pages/announcements/index.html", {
        "current_member": member,
        "current_user": user,
        "announcements": announcements,
        "today": date.today(),
    })


# ── Création ─────────────────────────────────────────────────────────────────

@router.post("/new")
async def create_announcement(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    title: str = Form(...),
    content: str = Form(...),
    is_pinned: bool = Form(False),
    expires_at: str = Form(""),
):
    user, member = ctx
    _require_manager(user, member)

    exp = None
    if expires_at.strip():
        try:
            exp = date.fromisoformat(expires_at.strip())
        except ValueError:
            pass

    ann = Announcement(
        title=title.strip(),
        content=content.strip(),
        author_id=member.id,
        is_pinned=is_pinned,
        expires_at=exp,
    )
    db.add(ann)
    await db.commit()
    await db.refresh(ann)

    # Push notification à tous les membres actifs
    try:
        from app.models.identity import MemberStatus
        from app.services.push import send_push_broadcast
        r = await db.execute(
            select(Member.id).where(Member.status == MemberStatus.ACTIVE, Member.id != member.id)
        )
        ids = [row[0] for row in r.all()]
        await send_push_broadcast(
            db, ids,
            f"📰 {ann.title}",
            (ann.content or "")[:140],
            "/announcements/",
        )
    except Exception:
        pass

    return RedirectResponse(url="/announcements/?created=1", status_code=303)


# ── Épingler / désépingler ────────────────────────────────────────────────────

@router.post("/{ann_id}/pin")
async def toggle_pin(
    ann_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    _require_manager(user, member)

    r = await db.execute(select(Announcement).where(Announcement.id == ann_id))
    ann = r.scalar_one_or_none()
    if not ann:
        raise HTTPException(status_code=404)

    ann.is_pinned = not ann.is_pinned
    await db.commit()
    return RedirectResponse(url="/announcements/", status_code=303)


# ── Suppression ───────────────────────────────────────────────────────────────

@router.post("/{ann_id}/delete")
async def delete_announcement(
    ann_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    _require_manager(user, member)

    r = await db.execute(select(Announcement).where(Announcement.id == ann_id))
    ann = r.scalar_one_or_none()
    if not ann:
        raise HTTPException(status_code=404)

    await db.delete(ann)
    await db.commit()
    return RedirectResponse(url="/announcements/", status_code=303)


# ── Marquer comme lu ─────────────────────────────────────────────────────────

@router.post("/{ann_id}/read")
async def mark_read(
    ann_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    # Vérifier que l'annonce existe
    r = await db.execute(select(Announcement).where(Announcement.id == ann_id))
    if not r.scalar_one_or_none():
        raise HTTPException(status_code=404)

    # Ne pas créer de doublon
    existing = await db.execute(
        select(AnnouncementRead).where(
            AnnouncementRead.announcement_id == ann_id,
            AnnouncementRead.member_id == member.id,
        )
    )
    if not existing.scalar_one_or_none():
        db.add(AnnouncementRead(announcement_id=ann_id, member_id=member.id))
        await db.commit()

    # Retour vers la page d'origine (referer) ou dashboard
    referer = "/announcements/" if (can_manage_members(member) or user.is_admin) else "/"
    return RedirectResponse(url=referer, status_code=303)
