"""Router — À propos de la loge (charte Pierre d'Angle, diffusion générale)"""
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin, require_auth
from app.models.content import AboutSection

router = APIRouter(tags=["about"])
from app.template_engine import templates


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
    sections = sections_r.scalars().all()
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
        "saved": saved,
    })


@router.post("/admin/a-propos/{section_id}/save")
async def about_admin_save(
    section_id: int,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    title: str = Form(...),
    content: str = Form(""),
):
    user, member = ctx
    section = await db.get(AboutSection, section_id)
    if not section:
        raise HTTPException(status_code=404)
    section.title = title.strip()
    section.content_html = content
    section.updated_by_id = member.id
    await db.commit()
    return RedirectResponse(url="/admin/a-propos?saved=1", status_code=303)
