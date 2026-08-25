"""Router — Répertoire des loges voisines (annuaire externe)"""
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_auth, can_manage_members
from app.models.lodges_directory import NeighboringLodge

router = APIRouter(prefix="/loges-voisines", tags=["lodges_directory"])
from app.template_engine import templates

_JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
_ORDINAUX = {1: "1er", 2: "2e", 3: "3e", 4: "4e", 5: "5e"}


def _can_manage(user, member) -> bool:
    return bool(getattr(user, "is_admin", False) or can_manage_members(member))


def _schedule_label(schedule: Optional[list]) -> str:
    """Convertit [{"week":2,"day":"Samedi"}, ...] en '2e et 4e samedi du mois'."""
    if not schedule:
        return "—"
    by_day: dict[str, list[int]] = {}
    for entry in schedule:
        day = entry.get("day")
        week = entry.get("week")
        if day and week:
            by_day.setdefault(day, []).append(week)
    parts = []
    for day in _JOURS:
        weeks = sorted(by_day.get(day, []))
        if not weeks:
            continue
        weeks_label = " et ".join(_ORDINAUX.get(w, str(w)) for w in weeks)
        parts.append(f"{weeks_label} {day.lower()}")
    return " · ".join(parts) if parts else "—"


@router.get("/", response_class=HTMLResponse)
async def lodges_directory_list(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    search: str = "",
    region: str = "",
    rite: str = "",
):
    user, member = ctx

    query = select(NeighboringLodge).order_by(NeighboringLodge.region, NeighboringLodge.orient, NeighboringLodge.name)
    if search:
        term = f"%{search}%"
        query = query.where(
            or_(NeighboringLodge.name.ilike(term), NeighboringLodge.orient.ilike(term))
        )
    if region:
        query = query.where(NeighboringLodge.region == region)
    if rite:
        query = query.where(NeighboringLodge.rite == rite)

    r = await db.execute(query)
    lodges = r.scalars().all()

    all_r = await db.execute(select(NeighboringLodge.region, NeighboringLodge.rite))
    all_rows = all_r.all()
    regions = sorted({row[0] for row in all_rows if row[0]})
    rites = sorted({row[1] for row in all_rows if row[1]})

    schedule_labels = {lg.id: _schedule_label(lg.schedule) for lg in lodges}

    return templates.TemplateResponse(request, "pages/lodges_directory/list.html", {
        "current_user": user,
        "current_member": member,
        "lodges": lodges,
        "schedule_labels": schedule_labels,
        "regions": regions,
        "rites": rites,
        "search": search,
        "region_filter": region,
        "rite_filter": rite,
        "can_manage": _can_manage(user, member),
    })


@router.get("/new", response_class=HTMLResponse)
async def lodge_new_form(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not _can_manage(user, member):
        raise HTTPException(403)
    return templates.TemplateResponse(request, "pages/lodges_directory/form.html", {
        "current_user": user,
        "current_member": member,
        "lodge": None,
        "jours": _JOURS,
    })


def _parse_schedule(form) -> list:
    """Reconstruit le rythme théorique depuis les cases cochées week_D (D=jour, valeurs=semaines)."""
    schedule = []
    for day in _JOURS:
        weeks = form.getlist(f"week_{day}")
        for w in weeks:
            try:
                schedule.append({"week": int(w), "day": day})
            except ValueError:
                continue
    return schedule


@router.post("/new")
async def lodge_create(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    orient: str = Form(...),
    region: str = Form(""),
    name: str = Form(...),
    rite: str = Form(""),
    obedience: str = Form(""),
    meeting_time: str = Form(""),
    notes: str = Form(""),
):
    user, member = ctx
    if not _can_manage(user, member):
        raise HTTPException(403)
    form = await request.form()
    lodge = NeighboringLodge(
        orient=orient.strip(),
        region=region.strip() or None,
        name=name.strip(),
        rite=rite.strip() or None,
        obedience=obedience.strip() or None,
        meeting_time=meeting_time.strip() or None,
        schedule=_parse_schedule(form) or None,
        notes=notes.strip() or None,
        created_by_id=member.id,
    )
    db.add(lodge)
    await db.commit()
    return RedirectResponse(url="/loges-voisines/", status_code=303)


@router.get("/{lodge_id}/edit", response_class=HTMLResponse)
async def lodge_edit_form(
    lodge_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not _can_manage(user, member):
        raise HTTPException(403)
    lodge = await db.get(NeighboringLodge, lodge_id)
    if not lodge:
        raise HTTPException(404)
    checked = {(e["day"], e["week"]) for e in (lodge.schedule or [])}
    return templates.TemplateResponse(request, "pages/lodges_directory/form.html", {
        "current_user": user,
        "current_member": member,
        "lodge": lodge,
        "jours": _JOURS,
        "checked": checked,
    })


@router.post("/{lodge_id}/edit")
async def lodge_edit(
    lodge_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    orient: str = Form(...),
    region: str = Form(""),
    name: str = Form(...),
    rite: str = Form(""),
    obedience: str = Form(""),
    meeting_time: str = Form(""),
    notes: str = Form(""),
):
    user, member = ctx
    if not _can_manage(user, member):
        raise HTTPException(403)
    lodge = await db.get(NeighboringLodge, lodge_id)
    if not lodge:
        raise HTTPException(404)
    form = await request.form()
    lodge.orient = orient.strip()
    lodge.region = region.strip() or None
    lodge.name = name.strip()
    lodge.rite = rite.strip() or None
    lodge.obedience = obedience.strip() or None
    lodge.meeting_time = meeting_time.strip() or None
    lodge.schedule = _parse_schedule(form) or None
    lodge.notes = notes.strip() or None
    await db.commit()
    return RedirectResponse(url="/loges-voisines/", status_code=303)


@router.post("/{lodge_id}/delete")
async def lodge_delete(
    lodge_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not _can_manage(user, member):
        raise HTTPException(403)
    lodge = await db.get(NeighboringLodge, lodge_id)
    if not lodge:
        raise HTTPException(404)
    await db.delete(lodge)
    await db.commit()
    return RedirectResponse(url="/loges-voisines/", status_code=303)
