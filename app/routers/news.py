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
from app.models.groups import LodgeGroup, GroupMembership, GroupType
from app.models.identity import Member, MasonicGrade, LodgeFunction

router = APIRouter(prefix="/news", tags=["news"])
templates = Jinja2Templates(directory="app/templates")

_GRADE_ORDER = {"APPRENTI": 1, "COMPAGNON": 2, "MAITRE": 3}

_OFFICER_FUNCTIONS = {
    LodgeFunction.VM, LodgeFunction.PREMIER_S, LodgeFunction.SECOND_S,
    LodgeFunction.ORATEUR, LodgeFunction.SECRETAIRE, LodgeFunction.TRESORIER,
    LodgeFunction.EXPERT, LodgeFunction.MAITRE_CEREMONIES, LodgeFunction.HARMONISTE,
    LodgeFunction.HOSPITALIER, LodgeFunction.TUILEUR, LodgeFunction.ARCHITECTE,
    LodgeFunction.MAITRE_BANQUETS,
}


def _parse_target(target: str) -> tuple[Optional[str], Optional[int]]:
    """Retourne (min_grade, target_group_id) depuis la valeur du select."""
    if not target:
        return None, None
    if target.startswith("group:"):
        try:
            return None, int(target[6:])
        except ValueError:
            return None, None
    return target, None


def _target_value(article: NewsArticle) -> str:
    if article.target_group_id:
        return f"group:{article.target_group_id}"
    return article.min_grade or ""


async def _check_group_access(member: Member, group_id: int, db: AsyncSession) -> bool:
    group = await db.get(LodgeGroup, group_id)
    if not group:
        return False
    if group.group_type == GroupType.GRADE:
        if group.grade_filter is None:
            return True
        return bool(member.masonic_grade and member.masonic_grade.value == group.grade_filter)
    elif group.group_type == GroupType.COUNCIL:
        return member.lodge_function in _OFFICER_FUNCTIONS
    elif group.group_type == GroupType.PAIR:
        import json
        functions = set(json.loads(group.function_filter or "[]"))
        return bool(member.lodge_function and member.lodge_function.value in functions)
    else:
        r = await db.execute(
            select(GroupMembership).where(
                GroupMembership.group_id == group_id,
                GroupMembership.member_id == member.id,
            )
        )
        return r.scalar_one_or_none() is not None


async def _can_read(article: NewsArticle, member: Member, is_admin: bool, db: AsyncSession) -> bool:
    if is_admin:
        return True
    if article.target_group_id:
        return await _check_group_access(member, article.target_group_id, db)
    if article.min_grade:
        return _GRADE_ORDER.get(member.masonic_grade.value, 0) >= _GRADE_ORDER.get(article.min_grade, 0)
    return True


async def _load_groups(db: AsyncSession) -> list[LodgeGroup]:
    r = await db.execute(select(LodgeGroup).order_by(LodgeGroup.name))
    return r.scalars().all()


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
    articles = []
    for a in all_articles:
        if await _can_read(a, member, user.is_admin, db):
            articles.append(a)

    author_ids = {a.created_by_id for a in articles if a.created_by_id}
    authors_map: dict[int, Member] = {}
    if author_ids:
        ar = await db.execute(select(Member).where(Member.id.in_(author_ids)))
        authors_map = {m.id: m for m in ar.scalars().all()}

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
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not user.is_admin:
        raise HTTPException(status_code=403)
    return templates.TemplateResponse(request, "pages/news/form.html", {
        "current_member": member,
        "current_user": user,
        "article": None,
        "groups": await _load_groups(db),
        "target_value": "",
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
    target: str = Form(""),
    publish_until: str = Form(""),
    notify_members: str = Form(""),
):
    user, member = ctx
    if not user.is_admin:
        raise HTTPException(status_code=403)

    content_html = content.replace("\r\n", "\n").replace("\r", "\n")
    if "<" not in content_html:
        content_html = "<br>".join(content_html.split("\n"))

    pu = None
    if publish_until.strip():
        try:
            pu = datetime.fromisoformat(publish_until)
        except ValueError:
            pass

    min_grade, target_group_id = _parse_target(target)

    article = NewsArticle(
        title=title.strip(),
        content_html=content_html,
        is_featured=bool(is_featured),
        is_online=bool(is_online),
        min_grade=min_grade,
        target_group_id=target_group_id,
        publish_until=pu,
        created_by_id=member.id,
    )
    db.add(article)
    await db.commit()
    await db.refresh(article)

    if notify_members:
        from app.utils.notifications import send_notification
        view_url = str(request.base_url).rstrip("/") + f"/news/{article.id}"
        await send_notification(
            db, member.id,
            f"📰 Nouvelle actualité : {article.title}",
            f"Une nouvelle actualité a été publiée :\n\n{article.title}\n\n{view_url}",
            min_grade=article.min_grade,
            target_group_id=article.target_group_id,
        )
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
    if not await _can_read(article, member, user.is_admin, db):
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
        "groups": await _load_groups(db),
        "target_value": _target_value(article),
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
    target: str = Form(""),
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

    min_grade, target_group_id = _parse_target(target)
    article.title = title.strip()
    article.content_html = content_html
    article.is_featured = bool(is_featured)
    article.is_online = bool(is_online)
    article.min_grade = min_grade
    article.target_group_id = target_group_id
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
