"""Router — Actualités (module News)"""
from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_auth
from app.models.content import NewsArticle
from app.models.identity import Member, MasonicGrade

router = APIRouter(prefix="/news", tags=["news"])
templates = Jinja2Templates(directory="app/templates")

_GRADE_ORDER = {"APPRENTI": 1, "COMPAGNON": 2, "MAITRE": 3}


def _can_read(article: NewsArticle, member: Member, is_admin: bool) -> bool:
    if is_admin:
        return True
    if not article.min_grade:
        return True
    member_level = _GRADE_ORDER.get(member.masonic_grade.value, 0)
    required = _GRADE_ORDER.get(article.min_grade, 0)
    return member_level >= required


@router.get("/", response_class=HTMLResponse)
async def news_list(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    now = datetime.now()
    r = await db.execute(
        select(NewsArticle)
        .where(
            NewsArticle.is_online == True,
            or_(NewsArticle.publish_from == None, NewsArticle.publish_from <= now),
            or_(NewsArticle.publish_until == None, NewsArticle.publish_until >= now),
        )
        .order_by(NewsArticle.is_featured.desc(), NewsArticle.created_at.desc())
    )
    all_articles = r.scalars().all()
    articles = [a for a in all_articles if _can_read(a, member, user.is_admin)]

    # Charger les auteurs
    author_ids = {a.created_by_id for a in articles if a.created_by_id}
    authors_map: dict[int, Member] = {}
    if author_ids:
        ar = await db.execute(select(Member).where(Member.id.in_(author_ids)))
        authors_map = {m.id: m for m in ar.scalars().all()}

    # Admin : tous les articles (y compris hors ligne) pour gestion
    admin_all = []
    if user.is_admin:
        ra = await db.execute(
            select(NewsArticle).order_by(NewsArticle.created_at.desc())
        )
        admin_all = ra.scalars().all()

    return templates.TemplateResponse(request, "pages/news/list.html", {
        "current_member": member,
        "current_user": user,
        "articles": articles,
        "authors_map": authors_map,
        "admin_all": admin_all,
    })


@router.get("/new", response_class=HTMLResponse)
async def news_new_form(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
):
    user, member = ctx
    if not user.is_admin:
        raise HTTPException(status_code=403)
    return templates.TemplateResponse(request, "pages/news/form.html", {
        "current_member": member,
        "current_user": user,
        "article": None,
    })


@router.post("/new")
async def news_create(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    title: str = Form(""),
    content: str = Form(""),
    is_featured: str = Form(""),
    is_online: str = Form(""),
    min_grade: str = Form(""),
    publish_until: str = Form(""),
):
    user, member = ctx
    if not user.is_admin:
        raise HTTPException(status_code=403)
    # Convertir les sauts de ligne en <br> si pas déjà de balises HTML
    content_html = content.replace("\r\n", "\n").replace("\r", "\n")
    if "<" not in content_html:
        content_html = "<br>".join(content_html.split("\n"))

    pu = None
    if publish_until.strip():
        try:
            pu = datetime.fromisoformat(publish_until)
        except ValueError:
            pass

    article = NewsArticle(
        title=title.strip(),
        content_html=content_html,
        is_featured=bool(is_featured),
        is_online=bool(is_online),
        min_grade=min_grade or None,
        publish_until=pu,
        created_by_id=member.id,
    )
    db.add(article)
    await db.commit()
    return RedirectResponse(url="/news/", status_code=303)


@router.get("/{article_id}", response_class=HTMLResponse)
async def news_detail(
    article_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    article = await db.get(NewsArticle, article_id)
    if not article:
        raise HTTPException(status_code=404)
    if not _can_read(article, member, user.is_admin):
        raise HTTPException(status_code=403)
    author = await db.get(Member, article.created_by_id) if article.created_by_id else None
    return templates.TemplateResponse(request, "pages/news/detail.html", {
        "current_member": member,
        "current_user": user,
        "article": article,
        "author": author,
    })


@router.get("/{article_id}/edit", response_class=HTMLResponse)
async def news_edit_form(
    article_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not user.is_admin:
        raise HTTPException(status_code=403)
    article = await db.get(NewsArticle, article_id)
    if not article:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "pages/news/form.html", {
        "current_member": member,
        "current_user": user,
        "article": article,
    })


@router.post("/{article_id}/edit")
async def news_update(
    article_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    title: str = Form(""),
    content: str = Form(""),
    is_featured: str = Form(""),
    is_online: str = Form(""),
    min_grade: str = Form(""),
    publish_until: str = Form(""),
):
    user, member = ctx
    if not user.is_admin:
        raise HTTPException(status_code=403)
    article = await db.get(NewsArticle, article_id)
    if not article:
        raise HTTPException(status_code=404)

    content_html = content.replace("\r\n", "\n").replace("\r", "\n")
    if "<" not in content_html:
        content_html = "<br>".join(content_html.split("\n"))

    pu = None
    if publish_until.strip():
        try:
            pu = datetime.fromisoformat(publish_until)
        except ValueError:
            pass

    article.title = title.strip()
    article.content_html = content_html
    article.is_featured = bool(is_featured)
    article.is_online = bool(is_online)
    article.min_grade = min_grade or None
    article.publish_until = pu
    await db.commit()
    return RedirectResponse(url="/news/", status_code=303)


@router.post("/{article_id}/delete")
async def news_delete(
    article_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not user.is_admin:
        raise HTTPException(status_code=403)
    article = await db.get(NewsArticle, article_id)
    if not article:
        raise HTTPException(status_code=404)
    await db.delete(article)
    await db.commit()
    return RedirectResponse(url="/news/", status_code=303)
