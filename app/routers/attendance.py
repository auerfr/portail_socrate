"""Router Présences & Assiduité"""
from datetime import date
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func as sql_func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_auth, can_manage_attendance
from app.models.identity import Member, MemberStatus
from app.models.lodge import MasonicYear
from app.models.meetings import (
    Meeting, Attendance, AttendanceStatus,
    MeetingVisitor, VisitorStatus, Visitor,
)

router = APIRouter(prefix="/attendance", tags=["attendance"])
templates = Jinja2Templates(directory="app/templates")


def _require_attendance_mgr(user, member):
    if not (can_manage_attendance(member) or user.is_admin):
        raise HTTPException(status_code=403, detail="Accès réservé au VM, Secrétaire et Surveillants")


# ── Dashboard assiduité ───────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def attendance_dashboard(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    year_id: int = 0,
):
    user, member = ctx
    _require_attendance_mgr(user, member)

    # Années maçonniques
    years_r = await db.execute(
        select(MasonicYear).order_by(MasonicYear.is_current.desc(), MasonicYear.start_date.desc())
    )
    years = years_r.scalars().all()
    current_year = next((y for y in years if y.is_current), years[0] if years else None)
    selected_year = next((y for y in years if y.id == year_id), current_year)

    year_filter = (Meeting.masonic_year_id == selected_year.id) if selected_year else True

    # ── Tenues passées (pour grille assiduité) ──────────────────────────────
    past_r = await db.execute(
        select(Meeting)
        .where(year_filter, Meeting.meeting_date <= date.today())
        .order_by(Meeting.meeting_date)
    )
    past_meetings = past_r.scalars().all()
    past_ids = [m.id for m in past_meetings]

    # ── Tenues à venir ──────────────────────────────────────────────────────
    upcoming_r = await db.execute(
        select(Meeting)
        .where(year_filter, Meeting.meeting_date > date.today())
        .order_by(Meeting.meeting_date)
    )
    upcoming_meetings = upcoming_r.scalars().all()
    upcoming_ids = [m.id for m in upcoming_meetings]

    # Inscriptions pour les tenues à venir
    upcoming_members_count = {}   # meeting_id → nb membres inscrits PRESENT
    upcoming_visitors_count = {}  # meeting_id → nb passants CONFIRMED
    if upcoming_ids:
        ua_r = await db.execute(
            select(Attendance.meeting_id, sql_func.count().label("n"))
            .where(
                Attendance.meeting_id.in_(upcoming_ids),
                Attendance.status == AttendanceStatus.PRESENT,
            )
            .group_by(Attendance.meeting_id)
        )
        upcoming_members_count = {row.meeting_id: row.n for row in ua_r}

        uv_r = await db.execute(
            select(MeetingVisitor.meeting_id, sql_func.count().label("n"))
            .where(
                MeetingVisitor.meeting_id.in_(upcoming_ids),
                MeetingVisitor.status == VisitorStatus.CONFIRMED,
            )
            .group_by(MeetingVisitor.meeting_id)
        )
        upcoming_visitors_count = {row.meeting_id: row.n for row in uv_r}

    # ── Membres actifs ──────────────────────────────────────────────────────
    members_r = await db.execute(
        select(Member)
        .where(Member.status == MemberStatus.ACTIVE)
        .order_by(Member.last_name, Member.first_name)
    )
    active_members = members_r.scalars().all()

    # ── Présences membres (tenues passées) ──────────────────────────────────
    stats = {}   # member_id → {PRESENT: n, EXCUSED: n, ABSENT: n}
    grid  = {}   # member_id → {meeting_id → status_value}
    if past_ids:
        att_r = await db.execute(
            select(Attendance).where(Attendance.meeting_id.in_(past_ids))
        )
        for att in att_r.scalars().all():
            s = stats.setdefault(att.member_id, {"PRESENT": 0, "EXCUSED": 0, "ABSENT": 0})
            s[att.status.value] = s.get(att.status.value, 0) + 1
            grid.setdefault(att.member_id, {})[att.meeting_id] = att.status.value

    # ── Maçons passants confirmés par tenue passée ──────────────────────────
    visitors_per_meeting = {}   # meeting_id → count
    if past_ids:
        mv_r = await db.execute(
            select(MeetingVisitor)
            .where(
                MeetingVisitor.meeting_id.in_(past_ids),
                MeetingVisitor.status == VisitorStatus.CONFIRMED,
            )
        )
        for mv in mv_r.scalars().all():
            visitors_per_meeting[mv.meeting_id] = visitors_per_meeting.get(mv.meeting_id, 0) + 1

    return templates.TemplateResponse(request, "pages/attendance/dashboard.html", {
        "current_member": member,
        "current_user": user,
        "years": years,
        "selected_year": selected_year,
        # passé
        "meetings": past_meetings,
        "total_meetings": len(past_meetings),
        "active_members": active_members,
        "stats": stats,
        "grid": grid,
        "visitors_per_meeting": visitors_per_meeting,
        # à venir
        "upcoming_meetings": upcoming_meetings,
        "upcoming_members_count": upcoming_members_count,
        "upcoming_visitors_count": upcoming_visitors_count,
    })


# ── Émargement d'une tenue ────────────────────────────────────────────────────

@router.get("/meeting/{meeting_id}", response_class=HTMLResponse)
async def emargement_page(
    request: Request,
    meeting_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    _require_attendance_mgr(user, member)

    result = await db.execute(
        select(Meeting)
        .options(
            selectinload(Meeting.attendances).selectinload(Attendance.member),
            selectinload(Meeting.meeting_visitors).selectinload(MeetingVisitor.visitor),
        )
        .where(Meeting.id == meeting_id)
    )
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404)

    members_r = await db.execute(
        select(Member)
        .where(Member.status == MemberStatus.ACTIVE)
        .order_by(Member.last_name, Member.first_name)
    )
    active_members = members_r.scalars().all()

    att_by_member = {a.member_id: a for a in meeting.attendances}
    visitors = sorted(meeting.meeting_visitors, key=lambda mv: mv.visitor.last_name)

    return templates.TemplateResponse(request, "pages/attendance/emargement.html", {
        "current_member": member,
        "current_user": user,
        "meeting": meeting,
        "active_members": active_members,
        "att_by_member": att_by_member,
        "AttendanceStatus": AttendanceStatus,
        "visitors": visitors,
        "VisitorStatus": VisitorStatus,
    })


@router.post("/meeting/{meeting_id}", response_class=HTMLResponse)
async def emargement_save(
    request: Request,
    meeting_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    _require_attendance_mgr(user, member)

    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404)

    form = await request.form()

    # ── Membres ─────────────────────────────────────────────────────────────
    members_r = await db.execute(select(Member).where(Member.status == MemberStatus.ACTIVE))
    for m in members_r.scalars().all():
        status_val = form.get(f"status_{m.id}", "")
        if not status_val:
            continue
        try:
            new_status = AttendanceStatus(status_val)
        except ValueError:
            continue

        existing_r = await db.execute(
            select(Attendance).where(
                Attendance.meeting_id == meeting_id,
                Attendance.member_id == m.id,
            )
        )
        att = existing_r.scalar_one_or_none()
        excuse = form.get(f"excuse_{m.id}", "").strip() or None

        if att:
            att.status = new_status
            if new_status == AttendanceStatus.EXCUSED:
                att.excuse_reason = excuse
        else:
            db.add(Attendance(
                meeting_id=meeting_id,
                member_id=m.id,
                status=new_status,
                agape=False,
                excuse_reason=excuse if new_status == AttendanceStatus.EXCUSED else None,
            ))

    # ── Maçons passants (présents / no-show) ────────────────────────────────
    mv_r = await db.execute(
        select(MeetingVisitor).where(MeetingVisitor.meeting_id == meeting_id)
    )
    for mv in mv_r.scalars().all():
        mv.status = (
            VisitorStatus.CONFIRMED
            if form.get(f"visitor_present_{mv.id}") == "1"
            else VisitorStatus.CANCELLED
        )

    await db.commit()
    return RedirectResponse(url=f"/attendance/meeting/{meeting_id}?saved=1", status_code=303)


# ── Ajout rapide d'un maçon passant à une tenue ──────────────────────────────

@router.get("/api/visitors/search", response_class=JSONResponse)
async def search_visitors(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    q: str = "",
):
    """Recherche de visiteurs existants pour l'autocomplete (passants assidus)."""
    user, member = ctx
    _require_attendance_mgr(user, member)

    if len(q) < 2:
        # Retourner les visiteurs les plus fréquents
        freq_r = await db.execute(
            select(Visitor, sql_func.count(MeetingVisitor.id).label("visits"))
            .join(MeetingVisitor, MeetingVisitor.visitor_id == Visitor.id)
            .group_by(Visitor.id)
            .order_by(sql_func.count(MeetingVisitor.id).desc())
            .limit(10)
        )
        results = freq_r.all()
    else:
        q_like = f"%{q}%"
        freq_r = await db.execute(
            select(Visitor, sql_func.count(MeetingVisitor.id).label("visits"))
            .join(MeetingVisitor, MeetingVisitor.visitor_id == Visitor.id, isouter=True)
            .where(
                (Visitor.last_name.ilike(q_like)) |
                (Visitor.first_name.ilike(q_like)) |
                (Visitor.lodge_name.ilike(q_like))
            )
            .group_by(Visitor.id)
            .order_by(sql_func.count(MeetingVisitor.id).desc())
            .limit(10)
        )
        results = freq_r.all()

    return JSONResponse([{
        "id": v.id,
        "civility": v.civility or "F",
        "first_name": v.first_name,
        "last_name": v.last_name,
        "lodge_name": v.lodge_name or "",
        "orient_city": v.orient_city or "",
        "is_vm": v.is_vm,
        "visits": visits,
    } for v, visits in results])


@router.post("/meeting/{meeting_id}/add-visitor")
async def add_visitor_to_meeting(
    request: Request,
    meeting_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    visitor_id: int = Form(0),
    civility: str = Form("F"),
    first_name: str = Form(""),
    last_name: str = Form(""),
    lodge_name: str = Form(""),
    orient_city: str = Form(""),
    is_vm: bool = Form(False),
):
    """Ajoute un maçon passant à la tenue (existant ou nouveau)."""
    user, member = ctx
    _require_attendance_mgr(user, member)

    # Vérifier que la tenue existe
    mtg_r = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    if not mtg_r.scalar_one_or_none():
        raise HTTPException(status_code=404)

    if visitor_id:
        # Visiteur existant
        vis_r = await db.execute(select(Visitor).where(Visitor.id == visitor_id))
        visitor = vis_r.scalar_one_or_none()
        if not visitor:
            raise HTTPException(status_code=404, detail="Visiteur introuvable")
    else:
        # Nouveau visiteur
        if not first_name.strip() or not last_name.strip():
            raise HTTPException(status_code=422, detail="Nom et prénom requis")
        visitor = Visitor(
            civility=civility,
            first_name=first_name.strip(),
            last_name=last_name.strip(),
            lodge_name=lodge_name.strip() or None,
            orient_city=orient_city.strip() or None,
            is_vm=is_vm,
        )
        db.add(visitor)
        await db.flush()  # pour avoir visitor.id

    # Vérifier qu'il n'est pas déjà inscrit
    existing_r = await db.execute(
        select(MeetingVisitor).where(
            MeetingVisitor.meeting_id == meeting_id,
            MeetingVisitor.visitor_id == visitor.id,
        )
    )
    if not existing_r.scalar_one_or_none():
        db.add(MeetingVisitor(
            meeting_id=meeting_id,
            visitor_id=visitor.id,
            status=VisitorStatus.CONFIRMED,
        ))

    await db.commit()
    return RedirectResponse(url=f"/attendance/meeting/{meeting_id}?saved=1", status_code=303)


# ── Synthèse post-tenue ───────────────────────────────────────────────────────

@router.get("/meeting/{meeting_id}/summary", response_class=HTMLResponse)
async def meeting_summary(
    request: Request,
    meeting_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    _require_attendance_mgr(user, member)

    result = await db.execute(
        select(Meeting)
        .options(
            selectinload(Meeting.attendances).selectinload(Attendance.member),
            selectinload(Meeting.meeting_visitors).selectinload(MeetingVisitor.visitor),
        )
        .where(Meeting.id == meeting_id)
    )
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404)

    # ── Stats membres ────────────────────────────────────────────────────────
    present = [a for a in meeting.attendances if a.status == AttendanceStatus.PRESENT]
    excused = [a for a in meeting.attendances if a.status == AttendanceStatus.EXCUSED]
    absent  = [a for a in meeting.attendances if a.status == AttendanceStatus.ABSENT]
    total_members = len(meeting.attendances)
    pct_present = round(len(present) * 100 / total_members) if total_members else 0

    # ── Maçons passants confirmés ────────────────────────────────────────────
    confirmed_visitors = [
        mv for mv in meeting.meeting_visitors
        if mv.status == VisitorStatus.CONFIRMED
    ]
    confirmed_visitors.sort(key=lambda mv: mv.visitor.last_name)

    # Loges représentées
    lodges: dict[str, list] = {}
    for mv in confirmed_visitors:
        lodge_key = mv.visitor.lodge_name or "Loge inconnue"
        lodges.setdefault(lodge_key, []).append(mv)

    # Fréquence de visite de chaque passant (toutes tenues confondues)
    visitor_ids = [mv.visitor_id for mv in confirmed_visitors]
    visit_counts = {}   # visitor_id → total visites à notre loge
    if visitor_ids:
        vc_r = await db.execute(
            select(MeetingVisitor.visitor_id, sql_func.count().label("n"))
            .where(MeetingVisitor.visitor_id.in_(visitor_ids))
            .group_by(MeetingVisitor.visitor_id)
        )
        visit_counts = {row.visitor_id: row.n for row in vc_r}

    return templates.TemplateResponse(request, "pages/attendance/summary.html", {
        "current_member": member,
        "current_user": user,
        "meeting": meeting,
        "present": present,
        "excused": excused,
        "absent": absent,
        "total_members": total_members,
        "pct_present": pct_present,
        "confirmed_visitors": confirmed_visitors,
        "lodges": lodges,
        "visit_counts": visit_counts,
    })


# ── Assiduité d'un membre ─────────────────────────────────────────────────────

@router.get("/member/{member_id}", response_class=HTMLResponse)
async def member_attendance(
    request: Request,
    member_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, current_member = ctx
    if member_id != current_member.id and not (can_manage_attendance(current_member) or user.is_admin):
        raise HTTPException(status_code=403)

    target_r = await db.execute(select(Member).where(Member.id == member_id))
    target = target_r.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404)

    att_r = await db.execute(
        select(Attendance)
        .options(selectinload(Attendance.meeting))
        .where(Attendance.member_id == member_id)
        .order_by(Attendance.meeting_id.desc())
    )
    attendances = att_r.scalars().all()

    total   = len(attendances)
    present = sum(1 for a in attendances if a.status == AttendanceStatus.PRESENT)
    excused = sum(1 for a in attendances if a.status == AttendanceStatus.EXCUSED)
    absent  = sum(1 for a in attendances if a.status == AttendanceStatus.ABSENT)

    return templates.TemplateResponse(request, "pages/attendance/member.html", {
        "current_member": current_member,
        "current_user": user,
        "target": target,
        "attendances": attendances,
        "total": total,
        "present": present,
        "excused": excused,
        "absent": absent,
        "pct": round(present * 100 / total) if total else 0,
    })
