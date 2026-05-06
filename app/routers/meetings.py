"""Router Tenues & Agapes — CRUD + inscription publique + présences"""
from datetime import date, datetime, timedelta
from typing import Annotated, Optional
import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func as sql_func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import (
    require_auth, can_manage_meeting, can_lock_meeting,
)
from app.models.meetings import (
    Meeting, Attendance, AttendanceStatus, DegreeAttended,
    MeetingGrade, MeetingType, MeetingDegree,
    Visitor, MeetingVisitor, MeetingGuest, MeetingWaitlist,
    DietaryRestriction, GuestStatus,
)
from app.models.identity import Member
from app.models.lodge import MasonicYear

router = APIRouter(prefix="/meetings", tags=["meetings"])
templates = Jinja2Templates(directory="app/templates")


async def _count_agapes(db: AsyncSession, meeting_id: int) -> int:
    """Compte le total de couverts agapes pour une tenue (membres + visiteurs confirmés)."""
    # Membres
    r1 = await db.execute(
        select(sql_func.sum(Attendance.agape_guests + 1))
        .where(Attendance.meeting_id == meeting_id, Attendance.agape == True)
    )
    m = r1.scalar() or 0
    # Visiteurs confirmés
    r2 = await db.execute(
        select(sql_func.sum(MeetingVisitor.agape_guests + 1))
        .where(MeetingVisitor.meeting_id == meeting_id,
               MeetingVisitor.agape == True,
               MeetingVisitor.status == "CONFIRMED")
    )
    v = r2.scalar() or 0
    return int(m + v)


def _type_label(t) -> str:
    labels = {
        "BLANCHE":      "Tenue blanche",
        "SOLENNELLE":   "Tenue solennelle",
        "INSTRUCTION":  "Tenue d'instruction",
        "INITIATION":   "Initiation",
        "INSTALLATION": "Installation des officiers",
        "ELECTION":     "Élection du Vénérable",
        "PASSAGE":      "Passage au 2e degré",
        "ELEVATION":    "Élévation au 3e degré",
        "FETE":         "Fête maçonnique",
        "EXTRA":        "Tenue extraordinaire",
    }
    v = t.value if hasattr(t, "value") else str(t)
    return labels.get(v, v)


def _grade_label(g: MeetingGrade) -> str:
    return {
        "APPRENTI": "Apprentis",
        "COMPAGNON": "Compagnons",
        "MAITRE": "Maîtres",
        "ALL": "Toutes loges réunies",
    }.get(g, g)


# ── Liste des tenues ──────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def meetings_list(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    year_id: int = 0,
    upcoming_only: str = "0",   # "0" = absent du form (checkbox décochée)
    submitted: str = "0",
):
    user, member = ctx

    # Année maçonnique courante
    year_q = select(MasonicYear).order_by(MasonicYear.is_current.desc(), MasonicYear.start_date.desc())
    years_result = await db.execute(year_q)
    years = years_result.scalars().all()

    current_year = next((y for y in years if y.is_current), years[0] if years else None)
    selected_year = current_year

    if year_id:
        found = next((y for y in years if y.id == year_id), None)
        if found:
            selected_year = found

    # Requête tenues
    q = select(Meeting).order_by(Meeting.meeting_date.desc())
    if selected_year:
        q = q.where(Meeting.masonic_year_id == selected_year.id)
    # Si le form n'a pas encore été soumis → comportement par défaut = à venir
    # Si soumis sans la checkbox → upcoming_only sera absent → "0"
    effective_upcoming = upcoming_only if submitted == "1" else "1"
    if effective_upcoming == "1":
        q = q.where(Meeting.meeting_date >= date.today())
        q = q.order_by(Meeting.meeting_date.asc())

    result = await db.execute(q)
    meetings = result.scalars().all()

    # Nombre de tenues passées (pour afficher le hint quand filtre "à venir" actif)
    past_count = 0
    if effective_upcoming == "1" and selected_year:
        pc_r = await db.execute(
            select(sql_func.count()).where(
                Meeting.masonic_year_id == selected_year.id,
                Meeting.meeting_date < date.today(),
            )
        )
        past_count = pc_r.scalar() or 0

    # Compter les présences pour chaque tenue
    attendance_counts = {}
    if meetings:
        ids = [m.id for m in meetings]
        count_q = select(
            Attendance.meeting_id,
            sql_func.count(Attendance.id)
        ).where(
            Attendance.meeting_id.in_(ids),
            Attendance.status == AttendanceStatus.PRESENT
        ).group_by(Attendance.meeting_id)
        count_result = await db.execute(count_q)
        attendance_counts = dict(count_result.all())

    # Inscription du membre courant
    member_attendances = {}
    if meetings:
        att_q = select(Attendance).where(
            Attendance.meeting_id.in_([m.id for m in meetings]),
            Attendance.member_id == member.id,
        )
        att_result = await db.execute(att_q)
        for att in att_result.scalars().all():
            member_attendances[att.meeting_id] = att

    return templates.TemplateResponse(request, "pages/meetings/list.html", {
        "current_member": member,
        "current_user": user,
        "meetings": meetings,
        "years": years,
        "selected_year": selected_year,
        "upcoming_only": effective_upcoming,
        "attendance_counts": attendance_counts,
        "member_attendances": member_attendances,
        "type_label": _type_label,
        "grade_label": _grade_label,
        "can_manage": can_manage_meeting(member) or user.is_admin,
        "today": date.today(),
        "past_count": past_count,
    })


# ── Détail d'une tenue ────────────────────────────────────────────────────────

@router.get("/{meeting_id}", response_class=HTMLResponse)
async def meeting_detail(
    request: Request,
    meeting_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx

    # Charger la tenue avec ses présences
    result = await db.execute(
        select(Meeting)
        .options(
            selectinload(Meeting.attendances).selectinload(Attendance.member),
            selectinload(Meeting.meeting_visitors).selectinload(MeetingVisitor.visitor),
            selectinload(Meeting.meeting_guests),
            selectinload(Meeting.waitlist),
            selectinload(Meeting.degrees),
        )
        .where(Meeting.id == meeting_id)
    )
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Tenue introuvable")

    # Présence du membre courant
    my_attendance = next(
        (a for a in meeting.attendances if a.member_id == member.id), None
    )

    # Compter agapes
    agape_count = sum(
        (1 if a.agape else 0) + a.agape_guests
        for a in meeting.attendances
    ) + sum(
        (1 if mv.agape else 0) + mv.agape_guests
        for mv in meeting.meeting_visitors
        if mv.status.value == "CONFIRMED"
    ) + sum(
        1 for g in meeting.meeting_guests
        if g.status.value == "CONFIRMED" and g.agape
    )

    present_count = sum(1 for a in meeting.attendances if a.status.value == "PRESENT")
    excused_count = sum(1 for a in meeting.attendances if a.status.value == "EXCUSED")

    return templates.TemplateResponse(request, "pages/meetings/detail.html", {
        "current_member": member,
        "current_user": user,
        "meeting": meeting,
        "my_attendance": my_attendance,
        "present_count": present_count,
        "excused_count": excused_count,
        "agape_count": agape_count,
        "type_label": _type_label,
        "grade_label": _grade_label,
        "AttendanceStatus": AttendanceStatus,
        "can_manage": can_manage_meeting(member) or user.is_admin,
        "can_lock": can_lock_meeting(member),
        "registration_url": f"{request.base_url}inscription/{meeting.token}",
    })


# ── Formulaire nouvelle tenue ─────────────────────────────────────────────────

@router.get("/new/form", response_class=HTMLResponse)
async def meeting_new_form(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not (can_manage_meeting(member) or user.is_admin):
        raise HTTPException(status_code=403, detail="Accès refusé")

    # Année courante
    year_result = await db.execute(select(MasonicYear).where(MasonicYear.is_current == True))
    current_year = year_result.scalar_one_or_none()

    return templates.TemplateResponse(request, "pages/meetings/form.html", {
        "current_member": member,
        "current_user": user,
        "meeting": None,
        "current_year": current_year,
        "MeetingType": MeetingType,
        "MeetingGrade": MeetingGrade,
        "type_label": _type_label,
        "grade_label": _grade_label,
        "errors": {},
        "is_new": True,
        "form_action": "/meetings/new/form",
        "degree_labels": {
            "APPRENTI": "1er degré — Apprentis",
            "COMPAGNON": "2e degré — Compagnons",
            "MAITRE": "3e degré — Maîtres",
            "ALL": "Toutes loges réunies",
        },
    })


@router.post("/new/form", response_class=HTMLResponse)
async def meeting_create(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    meeting_date:    str = Form(...),
    meeting_time:    str = Form("20:30"),
    meeting_type:    str = Form("BLANCHE"),
    meeting_grade:   str = Form("MAITRE"),
    meeting_number:  str = Form(""),
    title:           str = Form(""),
    theme:           str = Form(""),
    location:        str = Form(""),
    address:         str = Form(""),
    agenda_html:     str = Form(""),
    agape_enabled:   str = Form(""),
    agape_capacity:  str = Form(""),
    agape_location:  str = Form(""),
    visio_url:       str = Form(""),
    # Multi-degrés : liste de degrés séparés par virgule + descriptions
    degrees_grades:  str = Form(""),  # ex: "APPRENTI,COMPAGNON,MAITRE"
    degrees_descs:   str = Form(""),  # ex: "Ouverture,Passage,Travaux"
):
    user, member = ctx
    if not (can_manage_meeting(member) or user.is_admin):
        raise HTTPException(status_code=403, detail="Accès refusé")

    # Récupérer l'année maçonnique correspondante
    d = date.fromisoformat(meeting_date)
    year_result = await db.execute(
        select(MasonicYear).where(
            MasonicYear.start_date <= d,
            MasonicYear.end_date >= d,
        )
    )
    masonic_year = year_result.scalar_one_or_none()
    if not masonic_year:
        # Fallback : année courante
        year_result = await db.execute(select(MasonicYear).where(MasonicYear.is_current == True))
        masonic_year = year_result.scalar_one_or_none()

    # Date de clôture des inscriptions = veille J-1 à 8h du matin
    reg_closes = datetime(d.year, d.month, d.day, 8, 0, 0) - timedelta(days=1)

    new_meeting = Meeting(
        masonic_year_id=masonic_year.id if masonic_year else 1,
        meeting_date=d,
        meeting_time=meeting_time or "20:30",
        type=MeetingType(meeting_type),
        grade=MeetingGrade(meeting_grade),
        meeting_number=int(meeting_number) if meeting_number.strip().isdigit() else None,
        title=title or None,
        theme=theme or None,
        location=location or None,
        address=address or None,
        agenda_html=agenda_html or None,
        agape_enabled=bool(agape_enabled),
        agape_capacity=int(agape_capacity) if agape_capacity else None,
        agape_location=agape_location or None,
        visio_url=visio_url or None,
        registration_closes_at=reg_closes,
        created_by_id=member.id,
    )
    db.add(new_meeting)
    await db.flush()  # obtenir l'ID

    # Enregistrer la séquence des degrés si multi-degrés
    if degrees_grades.strip():
        grades_list = [g.strip() for g in degrees_grades.split(",") if g.strip()]
        descs_list  = [d.strip() for d in degrees_descs.split("|") if True]  # séparateur |
        for i, grade_str in enumerate(grades_list):
            try:
                g = MeetingGrade(grade_str)
            except ValueError:
                continue
            desc = descs_list[i] if i < len(descs_list) else ""
            deg = MeetingDegree(
                meeting_id=new_meeting.id,
                order_position=i + 1,
                grade=g,
                description=desc or None,
            )
            db.add(deg)

    await db.commit()
    return RedirectResponse(url=f"/meetings/{new_meeting.id}", status_code=302)


# ── Édition d'une tenue ──────────────────────────────────────────────────────

@router.get("/{meeting_id}/edit", response_class=HTMLResponse)
async def meeting_edit_form(
    request: Request,
    meeting_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not (can_manage_meeting(member) or user.is_admin):
        raise HTTPException(status_code=403, detail="Accès refusé")

    result = await db.execute(
        select(Meeting)
        .options(selectinload(Meeting.degrees))
        .where(Meeting.id == meeting_id)
    )
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Tenue introuvable")

    year_result = await db.execute(select(MasonicYear).where(MasonicYear.is_current == True))
    current_year = year_result.scalar_one_or_none()

    return templates.TemplateResponse(request, "pages/meetings/form.html", {
        "current_member": member,
        "current_user": user,
        "meeting": meeting,
        "current_year": current_year,
        "MeetingType": MeetingType,
        "MeetingGrade": MeetingGrade,
        "type_label": _type_label,
        "grade_label": _grade_label,
        "errors": {},
        "is_new": False,
        "form_action": f"/meetings/{meeting_id}/edit",
        "degree_labels": {
            "APPRENTI": "1er degré — Apprentis",
            "COMPAGNON": "2e degré — Compagnons",
            "MAITRE": "3e degré — Maîtres",
            "ALL": "Toutes loges réunies",
        },
    })


@router.post("/{meeting_id}/edit", response_class=HTMLResponse)
async def meeting_edit_save(
    request: Request,
    meeting_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    meeting_date:    str = Form(...),
    meeting_time:    str = Form("20:30"),
    meeting_type:    str = Form("BLANCHE"),
    meeting_grade:   str = Form("MAITRE"),
    meeting_number:  str = Form(""),
    title:           str = Form(""),
    theme:           str = Form(""),
    location:        str = Form(""),
    address:         str = Form(""),
    agenda_html:     str = Form(""),
    agape_enabled:   str = Form(""),
    agape_capacity:  str = Form(""),
    agape_location:  str = Form(""),
    visio_url:       str = Form(""),
    degrees_grades:  str = Form(""),
    degrees_descs:   str = Form(""),
):
    user, member = ctx
    if not (can_manage_meeting(member) or user.is_admin):
        raise HTTPException(status_code=403, detail="Accès refusé")

    result = await db.execute(
        select(Meeting)
        .options(selectinload(Meeting.degrees))
        .where(Meeting.id == meeting_id)
    )
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404)

    d = date.fromisoformat(meeting_date)

    meeting.meeting_date    = d
    meeting.meeting_time    = meeting_time or "20:30"
    meeting.type            = MeetingType(meeting_type)
    meeting.grade           = MeetingGrade(meeting_grade)
    meeting.meeting_number  = int(meeting_number) if meeting_number.strip().isdigit() else None
    meeting.title           = title or None
    meeting.theme           = theme or None
    meeting.location        = location or None
    meeting.address         = address or None
    meeting.agenda_html     = agenda_html or None
    meeting.agape_enabled   = bool(agape_enabled)
    meeting.agape_capacity  = int(agape_capacity) if agape_capacity else None
    meeting.agape_location  = agape_location or None
    meeting.visio_url       = visio_url or None

    # Reconstruire la séquence des degrés
    for deg in list(meeting.degrees):
        await db.delete(deg)
    await db.flush()

    if degrees_grades.strip():
        grades_list = [g.strip() for g in degrees_grades.split(",") if g.strip()]
        descs_list  = [d.strip() for d in degrees_descs.split("|")]
        for i, grade_str in enumerate(grades_list):
            try:
                g = MeetingGrade(grade_str)
            except ValueError:
                continue
            desc = descs_list[i] if i < len(descs_list) else ""
            db.add(MeetingDegree(
                meeting_id=meeting.id,
                order_position=i + 1,
                grade=g,
                description=desc or None,
            ))

    await db.commit()
    return RedirectResponse(url=f"/meetings/{meeting_id}", status_code=302)


# ── Suppression d'une tenue ───────────────────────────────────────────────────

@router.post("/{meeting_id}/delete", response_class=HTMLResponse)
async def meeting_delete(
    request: Request,
    meeting_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not (user.is_admin):
        raise HTTPException(status_code=403, detail="Seul un administrateur peut supprimer une tenue")

    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404)

    await db.delete(meeting)
    await db.commit()
    return RedirectResponse(url="/meetings/", status_code=302)


# ── Inscription interne (membre connecté) ─────────────────────────────────────

@router.post("/{meeting_id}/register", response_class=HTMLResponse)
async def meeting_register(
    request: Request,
    meeting_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    attendance_status: str = Form("PRESENT"),
    agape:             str = Form(""),
    agape_guests:      str = Form("0"),
    excuse_reason:     str = Form(""),
):
    user, member = ctx

    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404)

    # Vérifier si déjà inscrit
    existing = await db.execute(
        select(Attendance).where(
            Attendance.meeting_id == meeting_id,
            Attendance.member_id == member.id,
        )
    )
    att = existing.scalar_one_or_none()

    if att:
        att.status = AttendanceStatus(attendance_status)
        att.agape = bool(agape)
        att.agape_guests = int(agape_guests) if agape_guests else 0
        att.excuse_reason = excuse_reason or None
    else:
        att = Attendance(
            meeting_id=meeting_id,
            member_id=member.id,
            status=AttendanceStatus(attendance_status),
            agape=bool(agape),
            agape_guests=int(agape_guests) if agape_guests else 0,
            excuse_reason=excuse_reason or None,
        )
        db.add(att)

    await db.commit()
    return RedirectResponse(url=f"/meetings/{meeting_id}", status_code=302)


# ── Inscription publique (lien depuis le programme PDF) ───────────────────────

@router.get("/public/{token}", response_class=HTMLResponse)
async def public_register_page(
    request: Request,
    token: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Page publique d'inscription — accessible sans login."""
    result = await db.execute(select(Meeting).where(Meeting.token == token))
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Lien d'inscription invalide")

    # Fermeture auto : si la tenue est passée
    if meeting.meeting_date < date.today():
        if meeting.registration_open:
            meeting.registration_open = False
            await db.commit()

    if not meeting.registration_open:
        return templates.TemplateResponse(request, "pages/meetings/register_closed.html", {
            "meeting": meeting,
            "type_label": _type_label,
        })

    from app.models.lodge import LodgeSettings as _LS
    r2 = await db.execute(select(_LS).limit(1))
    lodge = r2.scalar_one_or_none()

    # Compteur agapes actuel
    agape_count = await _count_agapes(db, meeting.id)

    # Liste membres actifs (pour dropdown PIN)
    members_r = await db.execute(
        select(Member)
        .where(Member.status == "ACTIVE")
        .order_by(Member.last_name, Member.first_name)
    )
    active_members = members_r.scalars().all()

    return templates.TemplateResponse(request, "pages/meetings/register_public.html", {
        "meeting": meeting,
        "token": token,
        "type_label": _type_label,
        "DietaryRestriction": DietaryRestriction,
        "lodge": lodge,
        "agape_count": agape_count,
        "active_members": active_members,
    })


@router.post("/public/{token}", response_class=HTMLResponse)
async def public_register_submit(
    request: Request,
    token: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    visitor_type:      str = Form("visitor"),  # "member" | "visitor"
    # Inscription membre (PIN)
    member_id:         str = Form(""),
    pin_code:          str = Form(""),
    # Inscription visiteur
    last_name:         str = Form(""),
    first_name:        str = Form(""),
    email:             str = Form(""),
    civility:          str = Form("F"),
    lodge_name:        str = Form(""),
    orient_city:       str = Form(""),
    obedience:         str = Form(""),
    masonic_grade_str: str = Form(""),
    is_vm:             str = Form(""),
    comment:           str = Form(""),
    agape:             str = Form(""),
    agape_guests:      str = Form("0"),
    phone:             str = Form(""),
):
    """Traitement de l'inscription publique."""
    from app.dependencies import verify_password

    result = await db.execute(select(Meeting).where(Meeting.token == token))
    meeting = result.scalar_one_or_none()
    if not meeting or not meeting.registration_open:
        raise HTTPException(status_code=404)

    agape_bool = bool(agape)
    agape_guests_int = max(0, int(agape_guests) if agape_guests.isdigit() else 0)

    # ── Vérifier capacité agapes ───────────────────────────────────────────
    places_needed = (1 + agape_guests_int) if agape_bool else 0
    if meeting.agape_capacity and agape_bool:
        current = await _count_agapes(db, meeting.id)
        remaining = meeting.agape_capacity - current
        if places_needed > remaining:
            # Liste d'attente
            pos_r = await db.execute(
                select(sql_func.count()).select_from(MeetingWaitlist)
                .where(MeetingWaitlist.meeting_id == meeting.id)
            )
            position = (pos_r.scalar() or 0) + 1
            db.add(MeetingWaitlist(
                meeting_id=meeting.id,
                external_name=f"{first_name} {last_name}".strip() or f"Membre #{member_id}",
                external_email=email or None,
                position=position,
            ))
            await db.commit()
            return templates.TemplateResponse(request, "pages/meetings/register_waitlist.html", {
                "meeting": meeting, "position": position, "type_label": _type_label,
            })

    # ── Cas 1 : Membre de la loge (PIN) ───────────────────────────────────
    if visitor_type == "member":
        error = None
        target_member = None

        if not member_id.isdigit():
            error = "Veuillez sélectionner votre nom."
        else:
            m_r = await db.execute(select(Member).where(Member.id == int(member_id)))
            target_member = m_r.scalar_one_or_none()
            if not target_member:
                error = "Membre introuvable."
            elif not target_member.pin_code_hash:
                error = "Aucun code PIN configuré pour ce compte. Contactez le secrétaire."
            elif not pin_code or not verify_password(pin_code, target_member.pin_code_hash):
                error = "Code PIN incorrect."

        if error:
            from app.models.lodge import LodgeSettings as _LS
            r2 = await db.execute(select(_LS).limit(1))
            lodge = r2.scalar_one_or_none()
            agape_count = await _count_agapes(db, meeting.id)
            members_r = await db.execute(
                select(Member).where(Member.status == "ACTIVE")
                .order_by(Member.last_name, Member.first_name)
            )
            return templates.TemplateResponse(request, "pages/meetings/register_public.html", {
                "meeting": meeting, "token": token, "type_label": _type_label,
                "DietaryRestriction": DietaryRestriction, "lodge": lodge,
                "agape_count": agape_count,
                "active_members": members_r.scalars().all(),
                "pin_error": error,
                "prefill_member_id": member_id,
            }, status_code=422)

        # PIN OK — enregistrer ou mettre à jour la présence
        existing_r = await db.execute(
            select(Attendance).where(
                Attendance.meeting_id == meeting.id,
                Attendance.member_id == target_member.id,
            )
        )
        att = existing_r.scalar_one_or_none()
        if att:
            att.agape = agape_bool
            att.agape_guests = agape_guests_int
            # Ne pas écraser un statut déjà saisi par l'admin
            if att.status == AttendanceStatus.ABSENT:
                att.status = AttendanceStatus.PRESENT
        else:
            att = Attendance(
                meeting_id=meeting.id,
                member_id=target_member.id,
                status=AttendanceStatus.PRESENT,
                agape=agape_bool,
                agape_guests=agape_guests_int,
            )
            db.add(att)

        await db.commit()
        return templates.TemplateResponse(request, "pages/meetings/register_success.html", {
            "meeting": meeting, "visitor_type": "member",
            "first_name": target_member.first_name,
            "last_name": target_member.last_name,
            "agape": agape_bool, "type_label": _type_label,
        })

    # ── Cas 2 : Maçon passant ──────────────────────────────────────────────
    visitor = Visitor(
        civility=civility if civility in ("F", "S") else "F",
        last_name=last_name.strip().upper(),
        first_name=first_name.strip().title(),
        email=email.strip().lower() if email else None,
        lodge_name=lodge_name or None,
        orient_city=orient_city or None,
        obedience=obedience or None,
        masonic_grade=masonic_grade_str or None,
        is_vm=bool(is_vm),
        phone=phone or None,
    )
    db.add(visitor)
    await db.flush()

    db.add(MeetingVisitor(
        meeting_id=meeting.id,
        visitor_id=visitor.id,
        agape=agape_bool,
        agape_guests=agape_guests_int,
        token_used=token,
        comment=comment.strip() or None,
    ))
    await db.commit()

    return templates.TemplateResponse(request, "pages/meetings/register_success.html", {
        "meeting": meeting, "visitor_type": "visitor",
        "first_name": first_name, "last_name": last_name,
        "agape": agape_bool, "type_label": _type_label,
    })


# ── Verrouillage de la tenue ──────────────────────────────────────────────────

@router.post("/{meeting_id}/lock", response_class=HTMLResponse)
async def meeting_lock(
    request: Request,
    meeting_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not (can_lock_meeting(member) or user.is_admin):
        raise HTTPException(status_code=403)

    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404)

    meeting.is_locked = not meeting.is_locked
    if meeting.is_locked:
        meeting.locked_by_id = member.id
        meeting.locked_at = datetime.utcnow()
        meeting.registration_open = False
    else:
        meeting.locked_by_id = None
        meeting.locked_at = None

    await db.commit()
    return RedirectResponse(url=f"/meetings/{meeting_id}", status_code=302)
