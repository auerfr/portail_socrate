"""Router — Formation (guides de révision Apprenti/Compagnon, marque et nom
compagnonnique). Menu séparé de Pierre d'Angle (cf. échange du 03/10/2026)."""
from typing import Annotated
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin, require_auth
from app.models.content import FormationResource
from app.models.identity import MasonicGrade

router = APIRouter(tags=["formation"])
from app.template_engine import templates

_GRADE_ORDER = {MasonicGrade.APPRENTI: 1, MasonicGrade.COMPAGNON: 2, MasonicGrade.MAITRE: 3}

# Grade minimum requis pour chaque volet — le contenu rituel de la Pierre
# Dégrossie (Apprenti) et de la Pierre Taillée (Compagnon) ne doit jamais
# être servi à un compte qui n'a pas encore ce grade (cf. échange sur
# Pierre d'Angle/Pierre Taillée : traitement invisible, pas un simple flou).
_MODULE_MIN_GRADE = {
    "apprenti": 1,   # APPRENTI
    "compagnon": 2,  # COMPAGNON
    "marque": 2,     # COMPAGNON (marque + nom compagnonnique)
}

_MODULES = {
    "apprenti": {
        "title": "Pierre Dégrossie — Apprenti",
        "subtitle": "Guide de révision pour l'examen de passage Apprenti → Apprenti Accompli",
        "ready": True,
    },
    "compagnon": {
        "title": "Pierre Taillée — Compagnon",
        "subtitle": "Guide de révision pour l'examen de Compagnon-Accompli",
        "ready": False,
    },
    "marque": {
        "title": "Marque & nom compagnonnique",
        "subtitle": "Fabrication de la marque et recherche du nom compagnonnique",
        "ready": False,
    },
}


def _member_level(user, member) -> int:
    if user.is_admin:
        return 99
    return _GRADE_ORDER.get(member.masonic_grade, 0)


@router.get("/formation", response_class=HTMLResponse)
async def formation_index(request: Request, ctx: Annotated[tuple, Depends(require_auth)], db: Annotated[AsyncSession, Depends(get_db)]):
    user, member = ctx
    lvl = _member_level(user, member)
    modules = []
    for key, info in _MODULES.items():
        modules.append({
            "key": key,
            "title": info["title"],
            "subtitle": info["subtitle"],
            "ready": info["ready"],
            "accessible": lvl >= _MODULE_MIN_GRADE[key],
        })
    return templates.TemplateResponse(request, "pages/formation/index.html", {
        "modules": modules,
        "is_admin": user.is_admin,
    })


@router.get("/formation/apprenti", response_class=HTMLResponse)
async def formation_apprenti(request: Request, ctx: Annotated[tuple, Depends(require_auth)], db: Annotated[AsyncSession, Depends(get_db)]):
    user, member = ctx
    lvl = _member_level(user, member)
    if lvl < _MODULE_MIN_GRADE["apprenti"]:
        return templates.TemplateResponse(request, "pages/formation/unavailable.html", {}, status_code=403)

    res = (await db.execute(
        select(FormationResource).where(FormationResource.module == "apprenti")
    )).scalar_one_or_none()
    pdf_document_id = res.pdf_document_id if res else None

    return templates.TemplateResponse(request, "pages/formation/apprenti.html", {
        "is_admin": user.is_admin,
        "pdf_document_id": pdf_document_id,
    })


@router.get("/admin/formation", response_class=HTMLResponse)
async def formation_admin(request: Request, ctx: Annotated[tuple, Depends(require_admin)], db: Annotated[AsyncSession, Depends(get_db)], saved: int = 0):
    rows = (await db.execute(select(FormationResource))).scalars().all()
    by_module = {r.module: r for r in rows}
    modules = []
    for key, info in _MODULES.items():
        modules.append({
            "key": key,
            "title": info["title"],
            "pdf_document_id": by_module[key].pdf_document_id if key in by_module else None,
        })
    return templates.TemplateResponse(request, "pages/formation/admin.html", {
        "modules": modules,
        "saved": saved,
    })


@router.post("/admin/formation/{module}/save")
async def formation_admin_save(
    request: Request,
    module: str,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    pdf_document_id: str = Form(""),
):
    user, member = ctx
    if module not in _MODULES:
        return RedirectResponse("/admin/formation", status_code=303)

    res = (await db.execute(
        select(FormationResource).where(FormationResource.module == module)
    )).scalar_one_or_none()
    if res is None:
        res = FormationResource(module=module)
        db.add(res)

    res.pdf_document_id = int(pdf_document_id) if pdf_document_id.strip().isdigit() else None
    res.updated_by_id = member.id if member else None
    await db.commit()
    return RedirectResponse("/admin/formation?saved=1", status_code=303)
