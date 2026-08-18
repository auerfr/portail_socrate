"""Router — Secrétariat : gestion des années maçonniques (mandats)"""
from datetime import date as _date
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_secretariat_manager
from app.models.associative import OfficerAssignment
from app.models.identity import Member
from app.models.lodge import LodgeOffice, MasonicYear
from app.services.masonic_year import create_new_masonic_year
from app.template_engine import templates

router = APIRouter(prefix="/secretariat", tags=["secretariat"])


def _default_dates(today: _date) -> tuple[_date, _date]:
    """Propose 1er septembre → 30 juin, sur la saison en cours."""
    year = today.year if today.month >= 7 else today.year - 1
    return _date(year, 9, 1), _date(year + 1, 6, 30)


@router.get("/annees", response_class=HTMLResponse)
async def annees_list(
    request: Request,
    ctx: Annotated[tuple, Depends(require_secretariat_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx

    r = await db.execute(select(MasonicYear).order_by(MasonicYear.start_date.desc()))
    years = r.scalars().all()

    # Tableau de loge courant — ce qui sera figé si on crée une nouvelle année maintenant.
    r_off = await db.execute(select(LodgeOffice).order_by(LodgeOffice.sort_order))
    offices = r_off.scalars().all()

    # Historique déjà archivé, groupé par année.
    r_hist = await db.execute(select(OfficerAssignment))
    history = r_hist.scalars().all()
    hist_by_year: dict[int, list] = {}
    for h in history:
        hist_by_year.setdefault(h.masonic_year_id, []).append(h)

    member_ids = {o.member_id for o in offices if o.member_id} | {h.member_id for h in history}
    members_map: dict[int, Member] = {}
    if member_ids:
        r_m = await db.execute(select(Member).where(Member.id.in_(member_ids)))
        members_map = {m.id: m for m in r_m.scalars().all()}

    default_start, default_end = _default_dates(_date.today())

    return templates.TemplateResponse(request, "pages/secretariat/annees.html", {
        "current_user": user,
        "current_member": member,
        "years": years,
        "offices": offices,
        "hist_by_year": hist_by_year,
        "members_map": members_map,
        "default_start": default_start.isoformat(),
        "default_end": default_end.isoformat(),
    })


@router.post("/annees/new")
async def annee_create(
    ctx: Annotated[tuple, Depends(require_secretariat_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
    label: Annotated[str, Form()],
    start_date: Annotated[str, Form()],
    end_date: Annotated[str, Form()],
):
    try:
        await create_new_masonic_year(
            db, label, _date.fromisoformat(start_date), _date.fromisoformat(end_date),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    await db.commit()
    return RedirectResponse(url="/secretariat/annees", status_code=303)
