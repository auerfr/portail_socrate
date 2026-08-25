"""Router — Répertoire des loges voisines (annuaire externe)"""
import calendar as _calendar_mod
from datetime import date
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


_MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
            "août", "septembre", "octobre", "novembre", "décembre"]


def _nth_weekday_date(year: int, month: int, day_name: str, n: int) -> Optional[date]:
    """Retourne la date du n-ième 'day_name' (ex: 'Samedi') du mois donné, ou
    None si ce mois n'a pas de n-ième occurrence de ce jour (ex: pas de
    5e mardi certains mois)."""
    if day_name not in _JOURS or n < 1:
        return None
    weekday_index = _JOURS.index(day_name)  # Lundi=0 … Dimanche=6, comme date.weekday()
    _, days_in_month = _calendar_mod.monthrange(year, month)
    occurrence = 0
    for d in range(1, days_in_month + 1):
        if date(year, month, d).weekday() == weekday_index:
            occurrence += 1
            if occurrence == n:
                return date(year, month, d)
    return None


@router.get("/calendrier", response_class=HTMLResponse)
async def lodges_directory_calendar(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    year: int = 0,
    month: int = 0,
    region: str = "",
    rite: str = "",
):
    user, member = ctx
    today = date.today()
    y = year or today.year
    m = month or today.month
    if m < 1 or m > 12:
        m = today.month

    query = select(NeighboringLodge).where(NeighboringLodge.schedule.isnot(None))
    if region:
        query = query.where(NeighboringLodge.region == region)
    if rite:
        query = query.where(NeighboringLodge.rite == rite)
    r = await db.execute(query)
    lodges = r.scalars().all()

    days: dict[date, list[dict]] = {}
    for lg in lodges:
        for entry in (lg.schedule or []):
            d = _nth_weekday_date(y, m, entry.get("day"), entry.get("week", 0))
            if not d:
                continue
            days.setdefault(d, []).append({
                "lodge": lg,
                "day_label": entry.get("day"),
            })

    sorted_days = sorted(days.items())

    # Navigation mois précédent/suivant
    prev_month, prev_year = (12, y - 1) if m == 1 else (m - 1, y)
    next_month, next_year = (1, y + 1) if m == 12 else (m + 1, y)

    all_r = await db.execute(select(NeighboringLodge.region, NeighboringLodge.rite))
    all_rows = all_r.all()
    regions = sorted({row[0] for row in all_rows if row[0]})
    rites = sorted({row[1] for row in all_rows if row[1]})

    return templates.TemplateResponse(request, "pages/lodges_directory/calendar.html", {
        "current_user": user,
        "current_member": member,
        "sorted_days": sorted_days,
        "month_label": f"{_MOIS_FR[m - 1]} {y}",
        "year": y, "month": m,
        "prev_year": prev_year, "prev_month": prev_month,
        "next_year": next_year, "next_month": next_month,
        "today": today,
        "regions": regions,
        "rites": rites,
        "region_filter": region,
        "rite_filter": rite,
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
    address: str = Form(""),
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
        address=address.strip() or None,
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
    address: str = Form(""),
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
    lodge.address = address.strip() or None
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
