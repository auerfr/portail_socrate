"""Router — Agenda / Calendrier global (Domaine 7)"""
import calendar as cal
from datetime import date, datetime, timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import require_auth
from app.models.lodge import LodgeSettings
from app.models.lodge_calendar import EventType, EventVisibility, LodgeEvent
from app.models.identity import LodgeFunction, MasonicGrade, Member, MemberStatus
from app.models.meetings import Meeting
from app.models.groups import LodgeGroup, GroupType
from app.routers.groups import resolve_group_member_ids, ensure_system_groups
from app.services.anniversaires import compute_anniversaires

router = APIRouter(prefix="/calendar", tags=["calendar"])
templates = Jinja2Templates(directory="app/templates")


# ── Helpers ────────────────────────────────────────────────────────────────

OFFICER_FUNCTIONS = {
    LodgeFunction.VM, LodgeFunction.PREMIER_S, LodgeFunction.SECOND_S,
    LodgeFunction.ORATEUR, LodgeFunction.SECRETAIRE, LodgeFunction.TRESORIER,
    LodgeFunction.EXPERT, LodgeFunction.MAITRE_CEREMONIES, LodgeFunction.HARMONISTE,
    LodgeFunction.HOSPITALIER, LodgeFunction.TUILEUR, LodgeFunction.ARCHITECTE,
    LodgeFunction.MAITRE_BANQUETS,
}


async def _event_visible_to(event: LodgeEvent, member: Member, db: AsyncSession, user) -> bool:
    """Renvoie True si l'événement est visible pour ce membre."""
    if event.is_personal:
        return user.is_admin or event.created_by_id == member.id

    v = event.visibility

    if v == EventVisibility.ALL:
        return True

    if v == EventVisibility.OFFICERS:
        return member.lodge_function in OFFICER_FUNCTIONS

    if v == EventVisibility.MAITRES:
        return member.masonic_grade == MasonicGrade.MAITRE

    if v == EventVisibility.COMPAGNONS_ET_MAITRES:
        return member.masonic_grade in (MasonicGrade.COMPAGNON, MasonicGrade.MAITRE)

    if v == EventVisibility.APPRENTIS:
        return member.masonic_grade == MasonicGrade.APPRENTI

    if v == EventVisibility.GROUP:
        if not event.visibility_group_id:
            return False
        group = await db.get(LodgeGroup, event.visibility_group_id)
        if not group:
            return False
        ids = await resolve_group_member_ids(db, group)
        return member.id in ids

    if v == EventVisibility.ADMIN:
        return user.is_admin

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


def _anniv_to_event(a) -> dict:
    """Convertit un Anniversaire en dict pseudo-event pour le calendrier."""
    if a.event_label == "Naissance":
        title = f"🎂 {a.first_name} {a.last_name} — {a.years} ans"
    else:
        title = f"🎖 {a.first_name} {a.last_name} — {a.years} ans de {a.event_label.lower()}"
    return {
        "id": f"anniv_{a.member_id}_{a.event_label}",
        "title": title,
        "date": a.anniv_date,
        "type": "ANNIV",
        "location": None,
        "url": "/anniversaires/",
        "is_meeting": False,
        "all_day": True,
        "start_datetime": None,
        "_anniv": a,
    }


async def _load_anniversaires_in_range(db: AsyncSession, date_from: date, date_to: date) -> list[dict]:
    """Charge les anniversaires (civils + maçonniques) tombant dans [date_from, date_to]."""
    r = await db.execute(select(Member).where(Member.status == MemberStatus.ACTIVE))
    members = list(r.scalars().all())
    out = []
    # On peut couvrir plusieurs années si la plage chevauche un nouvel an
    years = {date_from.year, date_to.year}
    for y in years:
        ref = date(y, 1, 1)
        all_ann = compute_anniversaires(members, today=ref)
        for a in all_ann:
            if date_from <= a.anniv_date <= date_to:
                out.append(_anniv_to_event(a))
    return out


def _lodge_event_to_dict(e: LodgeEvent) -> dict:
    """Convertit un LodgeEvent en dict unifié."""
    return {
        "id": e.id,
        "title": e.title,
        "date": e.start_datetime.date() if e.start_datetime else None,
        "type": e.event_type.value,
        "location": e.location,
        "meeting_url": e.meeting_url,
        "url": f"/calendar/events/{e.id}",
        "is_meeting": False,
        "all_day": e.all_day,
        "start_datetime": e.start_datetime,
        "end_datetime": e.end_datetime,
        "description": e.description,
        "visibility": e.event_type,
        "_obj": e,
    }


def _can_create_event(user, member: Member) -> bool:
    """Tous les membres peuvent créer des événements (personnels ou de groupe)."""
    return True


def _can_create_shared_event(user, member: Member) -> bool:
    """VM, Secrétaire, surveillants ou admin peuvent créer des événements visibles par tous."""
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
        if not await _event_visible_to(e, member, db, user):
            continue
        ev = _lodge_event_to_dict(e)
        d = e.start_datetime.date()
        events_by_day.setdefault(d, []).append(ev)

    # Anniversaires (civils + maçonniques) — visibles par tous
    for ev in await _load_anniversaires_in_range(db, date_from, date_to):
        events_by_day.setdefault(ev["date"], []).append(ev)

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
        if not await _event_visible_to(e, member, db, user):
            continue
        all_events.append(_lodge_event_to_dict(e))

    # Anniversaires
    all_events.extend(await _load_anniversaires_in_range(db, today, end_date))

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

    await ensure_system_groups(db)
    await db.commit()

    groups_r = await db.execute(
        select(LodgeGroup).order_by(LodgeGroup.is_system.desc(), LodgeGroup.name)
    )
    all_groups = groups_r.scalars().all()

    # Groupes auxquels le membre appartient (pour la sélection si non-privilégié)
    from app.routers.groups import resolve_group_member_ids
    member_groups = []
    for g in all_groups:
        ids = await resolve_group_member_ids(db, g)
        if member.id in ids:
            member_groups.append(g)

    ls_r = await db.execute(select(LodgeSettings).limit(1))
    lodge_cfg = ls_r.scalar_one_or_none()
    visio_server = lodge_cfg.visio_server_url.rstrip("/") if lodge_cfg and lodge_cfg.visio_server_url else ""
    visio_prefix = (lodge_cfg.visio_room_prefix or "loge") if lodge_cfg else "loge"

    can_manage = _can_create_shared_event(user, member)

    return templates.TemplateResponse(request, "pages/calendar/compose.html", {
        "current_member": member,
        "current_user": user,
        "event_types": EventType,
        "event_visibilities": EventVisibility,
        "all_groups": all_groups,
        "member_groups": member_groups,
        "can_manage_calendar": can_manage,
        "today_str": date.today().isoformat(),
        "visio_server": visio_server,
        "visio_prefix": visio_prefix,
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
    visibility_group_id: Optional[int] = Form(None),
    meeting_url: Optional[str] = Form(None),
    is_personal: Optional[str] = Form(None),
):
    user, member = ctx
    personal = is_personal in ("on", "1", "true")

    # Validation : les non-privilégiés ne peuvent créer que des événements
    # personnels ou des événements de groupe dont ils font partie
    if not personal and not _can_create_shared_event(user, member):
        if visibility != "GROUP":
            raise HTTPException(status_code=403, detail="Vous ne pouvez créer que des événements personnels ou de groupe.")
        # Pour GROUP : vérifier que le membre appartient au groupe
        if visibility_group_id:
            from app.routers.groups import resolve_group_member_ids
            grp_obj = await db.get(LodgeGroup, visibility_group_id)
            if grp_obj:
                ids = await resolve_group_member_ids(db, grp_obj)
                if member.id not in ids:
                    raise HTTPException(status_code=403, detail="Vous n'appartenez pas à ce groupe.")

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

    vis = EventVisibility(visibility)
    grp_id = visibility_group_id if vis == EventVisibility.GROUP else None

    url = (meeting_url or "").strip() or None
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url

    if not url:
        ls_r = await db.execute(select(LodgeSettings).limit(1))
        lodge_cfg = ls_r.scalar_one_or_none()
        if lodge_cfg and lodge_cfg.visio_server_url:
            prefix = lodge_cfg.visio_room_prefix or "loge"
            d = datetime.strptime(start_date, "%Y-%m-%d")
            room = f"{prefix}-{d.strftime('%Y%m%d')}"
            url = f"{lodge_cfg.visio_server_url.rstrip('/')}/{room}"

    event = LodgeEvent(
        title=title,
        description=description or None,
        location=location or None,
        meeting_url=url,
        start_datetime=start_dt,
        end_datetime=end_dt,
        all_day=is_all_day,
        event_type=EventType(event_type),
        visibility=vis,
        visibility_group_id=grp_id,
        is_personal=personal,
        created_by_id=member.id,
    )
    db.add(event)
    await db.flush()

    return RedirectResponse(url="/calendar/", status_code=302)


# ── Détail d'un événement ─────────────────────────────────────────────────

@router.get("/events/{event_id}", response_class=HTMLResponse)
async def calendar_event_detail(
    event_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx

    result = await db.execute(select(LodgeEvent).where(LodgeEvent.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Événement introuvable")

    if not await _event_visible_to(event, member, db, user):
        raise HTTPException(status_code=403, detail="Accès refusé")

    can_edit = user.is_admin or event.created_by_id == member.id or _can_create_event(user, member)

    # Charger le groupe de visibilité si GROUP
    vis_group = None
    if event.visibility_group_id:
        vis_group = await db.get(LodgeGroup, event.visibility_group_id)

    # Charger tous les groupes pour le formulaire d'édition
    groups_r = await db.execute(
        select(LodgeGroup).order_by(LodgeGroup.is_system.desc(), LodgeGroup.name)
    )
    all_groups = groups_r.scalars().all()

    vis_labels = {
        "ALL": "Tous les membres",
        "MAITRES": "Maîtres uniquement",
        "COMPAGNONS_ET_MAITRES": "Compagnons et Maîtres",
        "APPRENTIS": "Apprentis uniquement",
        "OFFICERS": "Conseil d'officiers",
        "GROUP": f"Groupe : {vis_group.name}" if vis_group else "Groupe spécifique",
        "ADMIN": "Administrateurs seulement",
    }
    type_labels = {
        "RITUAL": "Tenue rituelle",
        "AGAPE": "Agape / repas",
        "EXTERNAL": "Événement extérieur",
        "ADMIN": "Réunion administrative",
        "DEADLINE": "Échéance",
        "OTHER": "Autre",
    }

    return templates.TemplateResponse(request, "pages/calendar/detail.html", {
        "current_member": member,
        "current_user": user,
        "event": event,
        "can_edit": can_edit,
        "vis_group": vis_group,
        "all_groups": all_groups,
        "vis_label": vis_labels.get(event.visibility.value, event.visibility.value),
        "type_label": type_labels.get(event.event_type.value, event.event_type.value),
        "event_types": EventType,
        "event_visibilities": EventVisibility,
    })


# ── Édition d'un événement ─────────────────────────────────────────────────

@router.post("/events/{event_id}/edit")
async def calendar_event_edit(
    event_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    title: str = Form(...),
    description: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    meeting_url: Optional[str] = Form(None),
    start_date: str = Form(...),
    start_time: Optional[str] = Form(None),
    end_date: Optional[str] = Form(None),
    end_time: Optional[str] = Form(None),
    all_day: Optional[str] = Form(None),
    event_type: str = Form(...),
    visibility: str = Form(...),
    visibility_group_id: Optional[int] = Form(None),
):
    user, member = ctx

    result = await db.execute(select(LodgeEvent).where(LodgeEvent.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404)

    if not (user.is_admin or event.created_by_id == member.id or _can_create_event(user, member)):
        raise HTTPException(status_code=403)

    is_all_day = all_day == "on" or all_day == "1"
    start_dt = (
        datetime.strptime(start_date, "%Y-%m-%d")
        if is_all_day or not start_time
        else datetime.strptime(f"{start_date} {start_time}", "%Y-%m-%d %H:%M")
    )
    end_dt = None
    if end_date:
        end_dt = (
            datetime.strptime(end_date, "%Y-%m-%d")
            if is_all_day or not end_time
            else datetime.strptime(f"{end_date} {end_time}", "%Y-%m-%d %H:%M")
        )

    vis = EventVisibility(visibility)
    url = (meeting_url or "").strip() or None
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url

    event.title = title.strip()
    event.description = (description or "").strip() or None
    event.location = (location or "").strip() or None
    event.meeting_url = url
    event.start_datetime = start_dt
    event.end_datetime = end_dt
    event.all_day = is_all_day
    event.event_type = EventType(event_type)
    event.visibility = vis
    event.visibility_group_id = visibility_group_id if vis == EventVisibility.GROUP else None

    await db.commit()
    return RedirectResponse(url=f"/calendar/events/{event_id}", status_code=303)


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
        if not await _event_visible_to(e, member, db, user):
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

    # Anniversaires (12 mois à venir)
    anniv_to = today + timedelta(days=365)
    for ev in await _load_anniversaires_in_range(db, today, anniv_to):
        a = ev["_anniv"]
        uid = f"anniv-{a.member_id}-{a.event_label}-{a.anniv_date.year}@portail-socrate"
        summary = _ics_escape(ev["title"])
        dtstart = f"DTSTART;VALUE=DATE:{_format_ics_date(a.anniv_date)}"
        dtend = f"DTEND;VALUE=DATE:{_format_ics_date(a.anniv_date + timedelta(days=1))}"
        lines += ["BEGIN:VEVENT", f"UID:{uid}", f"SUMMARY:{summary}", dtstart, dtend, "CATEGORIES:ANNIV", "END:VEVENT"]

    lines.append("END:VCALENDAR")
    ics_content = "\r\n".join(lines) + "\r\n"

    return StreamingResponse(
        iter([ics_content]),
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=agenda-loge.ics"},
    )
