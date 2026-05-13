"""Router Bookmarks — Liens à partager collectifs (Pocket-like)."""
from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, or_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_auth
from app.models.bookmarks import Bookmark
from app.models.identity import Member

router = APIRouter(prefix="/bookmarks", tags=["bookmarks"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def bookmarks_index(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    q: str = "",
    tag: str = "",
):
    user, member = ctx
    stmt = select(Bookmark).order_by(desc(Bookmark.created_at))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(
            Bookmark.title.ilike(like),
            Bookmark.description.ilike(like),
            Bookmark.url.ilike(like),
        ))
    if tag:
        stmt = stmt.where(Bookmark.tags.ilike(f"%{tag.strip()}%"))

    bks = (await db.execute(stmt)).scalars().all()

    # Cache auteurs
    author_ids = {b.added_by_id for b in bks if b.added_by_id}
    authors: dict[int, Member] = {}
    if author_ids:
        for m in (await db.execute(select(Member).where(Member.id.in_(author_ids)))).scalars().all():
            authors[m.id] = m

    # Tous les tags (pour nuage)
    all_tags: dict[str, int] = {}
    all_bks = (await db.execute(select(Bookmark))).scalars().all()
    for b in all_bks:
        for t in (b.tags or "").split(","):
            t = t.strip()
            if t:
                all_tags[t] = all_tags.get(t, 0) + 1

    return templates.TemplateResponse(request, "pages/bookmarks/index.html", {
        "current_user": user, "current_member": member,
        "bookmarks": bks, "authors": authors,
        "q": q, "tag": tag,
        "all_tags": sorted(all_tags.items(), key=lambda x: -x[1]),
    })


@router.post("/new")
async def bookmark_create(
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    url: str = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    tags: str = Form(""),
):
    user, member = ctx
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    bk = Bookmark(
        url=url,
        title=title.strip() or url,
        description=description.strip() or None,
        tags=",".join(t.strip() for t in tags.split(",") if t.strip()) or None,
        added_by_id=member.id,
    )
    db.add(bk)
    await db.commit()
    return RedirectResponse(url="/bookmarks/", status_code=303)


@router.post("/{bk_id}/delete")
async def bookmark_delete(
    bk_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    bk = await db.get(Bookmark, bk_id)
    if not bk:
        raise HTTPException(404)
    if bk.added_by_id != member.id and not user.is_admin:
        raise HTTPException(403)
    await db.delete(bk)
    await db.commit()
    return RedirectResponse(url="/bookmarks/", status_code=303)
