"""Router — Pierre d'Angle (charte de la loge + contenu Compagnon/Maître)"""
from types import SimpleNamespace
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin, require_auth
from app.models.content import AboutSection
from app.models.documents import MinGrade
from app.models.identity import MasonicGrade

router = APIRouter(tags=["about"])
from app.template_engine import templates

_GRADE_ORDER = {
    MasonicGrade.APPRENTI:  1,
    MasonicGrade.COMPAGNON: 2,
    MasonicGrade.MAITRE:    3,
}
_MIN_GRADE_ORDER = {
    MinGrade.ALL:       0,
    MinGrade.APPRENTI:  1,
    MinGrade.COMPAGNON: 2,
    MinGrade.MAITRE:    3,
}


@router.get("/a-propos", response_class=HTMLResponse)
async def about_index(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    sections_r = await db.execute(
        select(AboutSection).order_by(AboutSection.order_position)
    )
    all_sections = sections_r.scalars().all()

    # Visibilité par grade appliquée ici, côté serveur : une section
    # réservée pour laquelle le membre n'a pas le grade requis est
    # entièrement absente de la réponse — ni titre ni encart "verrouillé"
    # (pas de divulgation, même partielle, du contenu Compagnon/Maître à
    # un Apprenti — cf. échange du 03/10/2026 sur la Pierre Taillée).
    member_lvl = 99 if user.is_admin else _GRADE_ORDER.get(member.masonic_grade, 0)
    sections = []
    for s in all_sections:
        required = _MIN_GRADE_ORDER.get(s.min_grade, 0)
        if member_lvl < required:
            continue
        sections.append(SimpleNamespace(
            id=s.id,
            title=s.title,
            content_html=s.content_html,
            min_grade=s.min_grade,
        ))

    return templates.TemplateResponse(request, "pages/about/index.html", {
        "current_user": user,
        "current_member": member,
        "sections": sections,
        "is_admin": user.is_admin,
    })


@router.get("/admin/a-propos", response_class=HTMLResponse)
async def about_admin_edit(
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    saved: int = 0,
):
    user, member = ctx
    sections_r = await db.execute(
        select(AboutSection).order_by(AboutSection.order_position)
    )
    sections = sections_r.scalars().all()
    return templates.TemplateResponse(request, "pages/about/admin_edit.html", {
        "current_user": user,
        "current_member": member,
        "sections": sections,
        "min_grades": list(MinGrade),
        "saved": saved,
    })


@router.post("/admin/a-propos/{section_id}/save")
async def about_admin_save(
    section_id: int,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    title: str = Form(...),
    content: str = Form(""),
    min_grade: str = Form("ALL"),
):
    user, member = ctx
    section = await db.get(AboutSection, section_id)
    if not section:
        raise HTTPException(status_code=404)
    section.title = title.strip()
    section.content_html = content
    section.min_grade = MinGrade(min_grade)
    section.updated_by_id = member.id
    await db.commit()
    return RedirectResponse(url="/admin/a-propos?saved=1", status_code=303)
