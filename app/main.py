"""Point d'entrée FastAPI — Portail Socrate"""
from contextlib import asynccontextmanager
from datetime import date, datetime

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from typing import Annotated
from app.config import get_settings
from app.database import engine, Base, get_db
from app.dependencies import get_current_user, can_manage_attendance
from app.routers import auth, members, meetings, finance, programs, attendance, announcements
from app.routers import settings as settings_router
from app.routers import messages as messages_router
from app.routers import calendar as calendar_router
# Import des modèles pour que Base.metadata.create_all les crée
import app.models.messaging      # noqa: F401
import app.models.lodge_calendar  # noqa: F401
from sqlalchemy import select, func as sql_func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import Member, MemberStatus
from app.models.lodge import MasonicYear
from app.models.meetings import (
    Meeting, Attendance, AttendanceStatus,
    MeetingVisitor, VisitorStatus, Visitor,
)
from app.models.communication import Announcement, AnnouncementRead

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Démarrage : créer les tables si elles n'existent pas
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # ── Migrations légères (ajout de colonnes manquantes) ──────────────────
    async with engine.begin() as conn:
        # budget_lines.category_label
        r = await conn.exec_driver_sql("PRAGMA table_info(budget_lines)")
        cols = [row[1] for row in r.fetchall()]
        if "category_label" not in cols:
            await conn.exec_driver_sql(
                "ALTER TABLE budget_lines ADD COLUMN category_label VARCHAR(200)"
            )

        # contribution_configs.initial_treasury
        r2 = await conn.exec_driver_sql("PRAGMA table_info(contribution_configs)")
        cols2 = [row[1] for row in r2.fetchall()]
        if "initial_treasury" not in cols2:
            await conn.exec_driver_sql(
                "ALTER TABLE contribution_configs ADD COLUMN initial_treasury NUMERIC(10,2) DEFAULT 0"
            )
        if "tier_selection_open" not in cols2:
            await conn.exec_driver_sql(
                "ALTER TABLE contribution_configs ADD COLUMN tier_selection_open BOOLEAN DEFAULT 0"
            )

        # ── Messagerie interne ──────────────────────────────────────────────
        # Les tables messages et message_recipients sont créées par Base.metadata.create_all
        # (nouveaux modèles — pas besoin d'ALTER TABLE)

        # ── Agenda ─────────────────────────────────────────────────────────
        # La table lodge_events est créée par Base.metadata.create_all

    yield
    # Arrêt
    await engine.dispose()


import traceback as _tb
from fastapi.responses import PlainTextResponse

app = FastAPI(
    title="Portail Socrate",
    description="Plateforme unifiée — Loge Socrate Raison et Progrès",
    version="1.0.0",
    docs_url="/api/docs" if settings.environment == "development" else None,
    redoc_url=None,
    lifespan=lifespan,
)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    tb = _tb.format_exc()
    return PlainTextResponse(f"Erreur interne:\n{tb}", status_code=500)

# ── Static files ───────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")

# ── Routers ────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(members.router)
app.include_router(meetings.router)
app.include_router(finance.router)
app.include_router(programs.router)
app.include_router(settings_router.router)
app.include_router(attendance.router)
app.include_router(announcements.router)
app.include_router(messages_router.router)
app.include_router(calendar_router.router)
# app.include_router(finance.router)
# app.include_router(documents.router)
# app.include_router(calendar.router)
# app.include_router(forum.router)
# app.include_router(chat.router)
# app.include_router(admin.router)


# ── Page d'accueil ─────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    ctx: Annotated[object, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if not ctx:
        return RedirectResponse(url="/auth/login")
    user, member = ctx
    today = date.today()

    # ── Prochaine tenue ──────────────────────────────────────────────────────
    next_r = await db.execute(
        select(Meeting)
        .where(Meeting.meeting_date >= today)
        .order_by(Meeting.meeting_date)
        .limit(1)
    )
    next_meeting = next_r.scalar_one_or_none()

    # Mon inscription à la prochaine tenue
    my_next_att = None
    next_inscriptions = 0
    next_visitors = 0
    next_agape = 0
    if next_meeting:
        my_att_r = await db.execute(
            select(Attendance).where(
                Attendance.meeting_id == next_meeting.id,
                Attendance.member_id == member.id,
            )
        )
        my_next_att = my_att_r.scalar_one_or_none()

        ins_r = await db.execute(
            select(sql_func.count()).where(
                Attendance.meeting_id == next_meeting.id,
                Attendance.status == AttendanceStatus.PRESENT,
            )
        )
        next_inscriptions = ins_r.scalar() or 0

        agape_r = await db.execute(
            select(sql_func.count()).where(
                Attendance.meeting_id == next_meeting.id,
                Attendance.status == AttendanceStatus.PRESENT,
                Attendance.agape == True,
            )
        )
        next_agape = agape_r.scalar() or 0

        vis_r = await db.execute(
            select(sql_func.count()).where(
                MeetingVisitor.meeting_id == next_meeting.id,
                MeetingVisitor.status == VisitorStatus.CONFIRMED,
            )
        )
        next_visitors = vis_r.scalar() or 0

    # ── 3 prochaines tenues après la prochaine ───────────────────────────────
    upcoming_r = await db.execute(
        select(Meeting)
        .where(Meeting.meeting_date >= today)
        .order_by(Meeting.meeting_date)
        .offset(1).limit(3)
    )
    upcoming_meetings = upcoming_r.scalars().all()

    # Mon statut sur chaque tenue à venir
    all_upcoming_ids = ([next_meeting.id] if next_meeting else []) + [m.id for m in upcoming_meetings]
    my_upcoming_att = {}
    if all_upcoming_ids:
        mua_r = await db.execute(
            select(Attendance).where(
                Attendance.meeting_id.in_(all_upcoming_ids),
                Attendance.member_id == member.id,
            )
        )
        my_upcoming_att = {a.meeting_id: a for a in mua_r.scalars().all()}

    # ── Année en cours ───────────────────────────────────────────────────────
    year_r = await db.execute(
        select(MasonicYear).where(MasonicYear.is_current == True).limit(1)
    )
    current_year = year_r.scalar_one_or_none()

    # ── Stats assiduité de l'année (pour managers) ──────────────────────────
    year_present = year_total = 0
    alert_members = []   # membres avec >= 3 absences

    if current_year:
        past_ids_r = await db.execute(
            select(Meeting.id).where(
                Meeting.masonic_year_id == current_year.id,
                Meeting.meeting_date < today,
            )
        )
        past_ids = [r[0] for r in past_ids_r.all()]

        if past_ids:
            yr_r = await db.execute(
                select(
                    Attendance.status,
                    sql_func.count().label("n"),
                ).where(Attendance.meeting_id.in_(past_ids))
                .group_by(Attendance.status)
            )
            for row in yr_r.all():
                if row.status == AttendanceStatus.PRESENT:
                    year_present += row.n
                year_total += row.n

            # Membres avec >= 3 absences
            if can_manage_attendance(member) or user.is_admin:
                abs_r = await db.execute(
                    select(Attendance.member_id, sql_func.count().label("n"))
                    .where(
                        Attendance.meeting_id.in_(past_ids),
                        Attendance.status == AttendanceStatus.ABSENT,
                    )
                    .group_by(Attendance.member_id)
                    .having(sql_func.count() >= 3)
                    .order_by(sql_func.count().desc())
                )
                alert_ids = {row.member_id: row.n for row in abs_r.all()}
                if alert_ids:
                    am_r = await db.execute(
                        select(Member).where(Member.id.in_(alert_ids.keys()))
                    )
                    alert_members = [
                        {"member": m, "absences": alert_ids[m.id]}
                        for m in am_r.scalars().all()
                    ]
                    alert_members.sort(key=lambda x: -x["absences"])

    year_pct = round(year_present * 100 / year_total) if year_total else 0

    # ── Mon assiduité personnelle (année en cours) ───────────────────────────
    my_present = my_total = 0
    if current_year and past_ids:
        my_r = await db.execute(
            select(Attendance).where(
                Attendance.member_id == member.id,
                Attendance.meeting_id.in_(past_ids),
            )
        )
        my_atts = my_r.scalars().all()
        my_total = len(my_atts)
        my_present = sum(1 for a in my_atts if a.status == AttendanceStatus.PRESENT)

    my_pct = round(my_present * 100 / my_total) if my_total else None

    # ── Annonces non lues ────────────────────────────────────────────────────
    # Annonces actives (non expirées) + pas encore lues par ce membre
    all_ann_r = await db.execute(
        select(Announcement)
        .options(selectinload(Announcement.author), selectinload(Announcement.reads))
        .where(
            (Announcement.expires_at == None) | (Announcement.expires_at >= today)
        )
        .order_by(Announcement.is_pinned.desc(), Announcement.created_at.desc())
    )
    all_announcements = all_ann_r.scalars().all()

    read_ids_r = await db.execute(
        select(AnnouncementRead.announcement_id).where(
            AnnouncementRead.member_id == member.id
        )
    )
    read_ids = {r[0] for r in read_ids_r.all()}

    unread_announcements = [a for a in all_announcements if a.id not in read_ids]
    read_announcements   = [a for a in all_announcements if a.id in read_ids]

    # ── Derniers maçons passants ─────────────────────────────────────────────
    recent_visitors_r = await db.execute(
        select(MeetingVisitor)
        .options(
            selectinload(MeetingVisitor.visitor),
            selectinload(MeetingVisitor.meeting),
        )
        .where(MeetingVisitor.status == VisitorStatus.CONFIRMED)
        .order_by(MeetingVisitor.registered_at.desc())
        .limit(4)
    )
    recent_visitors = recent_visitors_r.scalars().all()

    return templates.TemplateResponse(request, "pages/dashboard.html", {
        "current_member": member,
        "current_user": user,
        "now": datetime.now(),
        "today": today,
        # prochaine tenue
        "next_meeting": next_meeting,
        "my_next_att": my_next_att,
        "next_inscriptions": next_inscriptions,
        "next_visitors": next_visitors,
        "next_agape": next_agape,
        # à venir
        "upcoming_meetings": upcoming_meetings,
        "my_upcoming_att": my_upcoming_att,
        # stats année
        "current_year": current_year,
        "year_pct": year_pct,
        "year_present": year_present,
        "year_total": year_total,
        "alert_members": alert_members,
        # mon assiduité
        "my_pct": my_pct,
        "my_present": my_present,
        "my_total": my_total,
        # passants
        "recent_visitors": recent_visitors,
        # annonces
        "unread_announcements": unread_announcements,
        "read_announcements": read_announcements,
    })


# ── Lien public inscription (alias court pour les programmes PDF) ──────────
# Ex: https://portail.amisdesocrate.fr/inscription/abc123

@app.get("/inscription/{token}", response_class=HTMLResponse)
async def public_registration(token: str):
    """Redirige vers la page d'inscription publique de la tenue."""
    return RedirectResponse(url=f"/meetings/public/{token}", status_code=302)


# ── Health check ───────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "app": settings.app_name}
