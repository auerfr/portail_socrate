"""Router — Agenda / Calendrier global (Domaine 7)"""
import calendar as cal
from datetime import date, datetime, timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_auth
from app.models.lodge_calendar import EventType, EventVisibility, LodgeEvent
from app.models.identity import LodgeFunction, Member
from app.models.meetings import Meeting

router = APIRouter(prefix="/calendar", tags=["calendar"])
templates = Jinja2Templates(directory="app/templates")


# ── Helpers ────────────────────────────────────────────────────────────────

def _event_visible_to(event_visibility: EventVisibility, member: Member) -> bool:
    """Renvoie True si l'événement est visible pour ce membre."""
    if event_visibility == EventVisibility.ALL:
        return True
    if event_visibility == EventVisibility.OFFICERS:
        return member.lodge_function != LodgeFunction.FRERE
    # EventVisibility.ADMIN → géré via require_admin, jamais affiché ici
    return False


def _meeting_to_event(m: Meeting) -> dict:
    """Convertit une tenue en dict pseudo-event pour le calendrier."""
    return {
        "id": f"meeting_{m.id}",
        "title": m.title or "Tenue",
        "date": m.meeting_date,
        "type": "RITUAL",
        "location": m.location,
        "url": f"/meetings/{m.id}",
        "is_meeting": True,
        "all_day": True,
        "start_datetime": None,
    }


def _lodge_event_to_dict(e: LodgeEvent) -> dict:
    """Convertit un LodgeEvent en dict unifié."""
    return {
        "id": e.id,
        "title": e.title,
        "date": e.start_datetime.date() if e.start_datetime else None,
        "type": e.event_type.value,
        "location": e.location,
        "url": None,
        "is_meeting": False,
        "all_day": e.all_day,
        "start_datetime": e.start_datetime,
        "end_datetime": e.end_datetime,
        "description": e.description,
        "visibility": e.event_type,
        "_obj": e,
    }


def _can_create_event(user, member: Member) -> bool:
    """VM, Secrétaire ou admin peuvent créer des événements."""
    if user.is_admin:
        return True
    return member.lodge_function in (
        LodgeFunction.VM,
        LodgeFunction.SECRETAIRE,
        LodgeFunction.PREMIER_S,
        LodgeFunction.SECOND_S,
    )


def _ics_escape(text: str) -> str:
    """Échappe les caractères spéciaux pour le format ICS."""
    if not text:
        return ""
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _format_ics_datetime(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _format_ics_date(d: date) -> str:
    return d.strftime("%Y%m%d")


# ── Vue principale : grille mensuelle ─────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def calendar_index(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    year: Optional[int] = None,
    month: Optional[int] = None,
):
    user, member = ctx
    today = date.today()

    year = year or today.year
    month = month or today.month

    # Navigation mois précédent / suivant
    first_of_month = date(year, month, 1)
    if month == 1:
        prev_month = {"year": year - 1, "month": 12}
    else:
        prev_month = {"year": year, "month": month - 1}
    if month == 12:
        next_month = {"year": year + 1, "month": 1}
    else:
        next_month = {"year": year, "month": month + 1}

    # Calcul des semaines (lundi premier)
    cal_obj = cal.Calendar(firstweekday=0)
    weeks = cal_obj.monthdatescalendar(year, month)

    # Plage de dates à charger (toutes les dates visibles dans la grille)
    date_from = weeks[0][0]
    date_to = weeks[-1][-1]

    # Récupérer les tenues du mois
    meetings_r = await db.execute(
        select(Meeting).where(
            Meeting.meeting_date >= date_from,
            Meeting.meeting_date <= date_to,
        )
    )
    meetings_list = meetings_r.scalars().all()

    # Récupérer les LodgeEvents du mois
    events_r = await db.execute(
        select(LodgeEvent).where(
            LodgeEvent.start_datetime >= datetime.combine(date_from, datetime.min.time()),
            LodgeEvent.start_datetime <= datetime.combine(date_to, datetime.max.time()),
        )
    )
    lodge_events = events_r.scalars().all()

    # Construire events_by_day : dict[date -> liste de dicts]
    events_by_day: dict[date, list] = {}

    for m in meetings_list:
        ev = _meeting_to_event(m)
        d = m.meeting_date
        events_by_day.setdefault(d, []).append(ev)

    for e in lodge_events:
        if not _event_visible_to(e.visibility, member):
            continue
        ev = _lodge_event_to_dict(e)
        d = e.start_datetime.date()
        events_by_day.setdefault(d, []).append(ev)

    month_names = [
        "", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
        "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"
    ]

    return templates.TemplateResponse(request, "pages/calendar/index.html", {
        "current_member": member,
        "current_user": user,
        "today": today,
        "year": year,
        "month": month,
        "month_name": month_names[month],
        "weeks": weeks,
        "events_by_day": events_by_day,
        "prev_month": prev_month,
        "next_month": next_month,
        "can_create": _can_create_event(user, member),
    })


# ── Vue liste ─────────────────────────────────────────────────────────────

@router.get("/list", response_class=HTMLResponse)
async def calendar_list(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    today = date.today()
    end_date = today + timedelta(days=92)  # ~3 mois

    # Tenues à venir
    meetings_r = await db.execute(
        select(Meeting).where(
            Meeting.meeting_date >= today,
            Meeting.meeting_date <= end_date,
        ).order_by(Meeting.meeting_date)
    )
    meetings_list = meetings_r.scalars().all()

    # LodgeEvents à venir
    events_r = await db.execute(
        select(LodgeEvent).where(
            LodgeEvent.start_datetime >= datetime.combine(today, datetime.min.time()),
            LodgeEvent.start_datetime <= datetime.combine(end_date, datetime.max.time()),
        ).order_by(LodgeEvent.start_datetime)
    )
    lodge_events = events_r.scalars().all()

    # Fusionner et trier
    all_events = []
    for m in meetings_list:
        all_events.append(_meeting_to_event(m))
    for e in lodge_events:
        if not _event_visible_to(e.visibility, member):
            continue
        all_events.append(_lodge_event_to_dict(e))

    all_events.sort(key=lambda x: x["date"] or date.min)

    # Grouper par mois
    events_by_month: dict[tuple, list] = {}
    for ev in all_events:
        d = ev["date"]
        if d:
            key = (d.year, d.month)
            events_by_month.setdefault(key, []).append(ev)

    month_names = [
        "", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
        "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"
    ]

    # Transformer en liste ordonnée [(label, events)]
    months_ordered = [
        (f"{month_names[k[1]]} {k[0]}", v)
        for k, v in sorted(events_by_month.items())
    ]

    return templates.TemplateResponse(request, "pages/calendar/list.html", {
        "current_member": member,
        "current_user": user,
        "today": today,
        "months_ordered": months_ordered,
        "can_create": _can_create_event(user, member),
    })


# ── Formulaire création ────────────────────────────────────────────────────

@router.get("/compose", response_class=HTMLResponse)
async def calendar_compose(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not _can_create_event(user, member):
        raise HTTPException(status_code=403, detail="Accès refusé")

    return templates.TemplateResponse(request, "pages/calendar/compose.html", {
        "current_member": member,
        "current_user": user,
        "event_types": EventType,
        "event_visibilities": EventVisibility,
        "today_str": date.today().isoformat(),
    })


# ── Création d'un événement ────────────────────────────────────────────────

@router.post("/events")
async def calendar_create_event(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    title: str = Form(...),
    description: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    start_date: str = Form(...),
    start_time: Optional[str] = Form(None),
    end_date: Optional[str] = Form(None),
    end_time: Optional[str] = Form(None),
    all_day: Optional[str] = Form(None),
    event_type: str = Form(...),
    visibility: str = Form(...),
):
    user, member = ctx
    if not _can_create_event(user, member):
        raise HTTPException(status_code=403, detail="Accès refusé")

    is_all_day = all_day == "on" or all_day == "1" or all_day is True

    # Construire start_datetime
    if is_all_day or not start_time:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    else:
        start_dt = datetime.strptime(f"{start_date} {start_time}", "%Y-%m-%d %H:%M")

    # Construire end_datetime
    end_dt = None
    if end_date:
        if is_all_day or not end_time:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        else:
            end_dt = datetime.strptime(f"{end_date} {end_time}", "%Y-%m-%d %H:%M")

    event = LodgeEvent(
        title=title,
        description=description or None,
        location=location or None,
        start_datetime=start_dt,
        end_datetime=end_dt,
        all_day=is_all_day,
        event_type=EventType(event_type),
        visibility=EventVisibility(visibility),
        created_by_id=member.id,
    )
    db.add(event)
    await db.flush()

    return RedirectResponse(url="/calendar/", status_code=302)


# ── Suppression d'un événement ─────────────────────────────────────────────

@router.post("/events/{event_id}/delete")
async def calendar_delete_event(
    event_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx

    result = await db.execute(select(LodgeEvent).where(LodgeEvent.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Événement introuvable")

    # Seul le créateur ou un admin peut supprimer
    if not user.is_admin and event.created_by_id != member.id:
        raise HTTPException(status_code=403, detail="Accès refusé")

    await db.delete(event)
    return RedirectResponse(url="/calendar/", status_code=302)


# ── Export ICS ─────────────────────────────────────────────────────────────

@router.get("/export.ics")
async def calendar_export_ics(
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    today = date.today()

    # Tenues à venir
    meetings_r = await db.execute(
        select(Meeting).where(Meeting.meeting_date >= today).order_by(Meeting.meeting_date)
    )
    meetings_list = meetings_r.scalars().all()

    # LodgeEvents à venir
    events_r = await db.execute(
        select(LodgeEvent).where(
            LodgeEvent.start_datetime >= datetime.combine(today, datetime.min.time())
        ).order_by(LodgeEvent.start_datetime)
    )
    lodge_events = events_r.scalars().all()

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Portail Socrate//FR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Agenda Loge",
        "X-WR-CALDESC:Agenda de la loge — tenues et événements",
        "X-WR-TIMEZONE:Europe/Paris",
    ]

    # Événements depuis les tenues
    for m in meetings_list:
        uid = f"meeting-{m.id}@portail-socrate"
        summary = _ics_escape(m.title or "Tenue")
        dtstart = f"DTSTART;VALUE=DATE:{_format_ics_date(m.meeting_date)}"
        dtend_date = m.meeting_date + timedelta(days=1)
        dtend = f"DTEND;VALUE=DATE:{_format_ics_date(dtend_date)}"
        location_line = f"LOCATION:{_ics_escape(m.location)}" if m.location else ""

        lines += ["BEGIN:VEVENT", f"UID:{uid}", f"SUMMARY:{summary}", dtstart, dtend]
        if location_line:
            lines.append(location_line)
        lines.append("END:VEVENT")

    # Événements libres
    for e in lodge_events:
        if not _event_visible_to(e.visibility, member):
            continue
        uid = f"event-{e.id}@portail-socrate"
        summary = _ics_escape(e.title)
        if e.all_day:
            dtstart = f"DTSTART;VALUE=DATE:{_format_ics_date(e.start_datetime.date())}"
            if e.end_datetime:
                dtend_date = e.end_datetime.date() + timedelta(days=1)
            else:
                dtend_date = e.start_datetime.date() + timedelta(days=1)
            dtend = f"DTEND;VALUE=DATE:{_format_ics_date(dtend_date)}"
        else:
            dtstart = f"DTSTART:{_format_ics_datetime(e.start_datetime)}"
            if e.end_datetime:
                dtend = f"DTEND:{_format_ics_datetime(e.end_datetime)}"
            else:
                dtend = f"DTEND:{_format_ics_datetime(e.start_datetime + timedelta(hours=2))}"

        lines += ["BEGIN:VEVENT", f"UID:{uid}", f"SUMMARY:{summary}", dtstart, dtend]
        if e.location:
            lines.append(f"LOCATION:{_ics_escape(e.location)}")
        if e.description:
            lines.append(f"DESCRIPTION:{_ics_escape(e.description)}")
        lines.append(f"CATEGORIES:{e.event_type.value}")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    ics_content = "\r\n".join(lines) + "\r\n"

    return StreamingResponse(
        iter([ics_content]),
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=agenda-loge.ics"},
    )
