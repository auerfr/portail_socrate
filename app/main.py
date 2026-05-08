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
from app.routers import groups as groups_router
from app.routers import documents as documents_router
from app.routers import chat as chat_router
from app.routers import sharing as sharing_router
# Import des modèles pour que Base.metadata.create_all les crée
import app.models.messaging      # noqa: F401
import app.models.lodge_calendar  # noqa: F401
import app.models.groups          # noqa: F401
import app.models.documents       # noqa: F401
import app.models.chat            # noqa: F401
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
from app.models.messaging import MessageRecipient as MsgRecipient, Message as Msg
from app.models.lodge_calendar import LodgeEvent
from app.routers.calendar import _event_visible_to
from app.models.chat import ChatChannel, ChatChannelMember, ChatMessage, ChatRead, ChannelType

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Démarrage : créer les tables si elles n'existent pas
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # ── Migrations légères (ajout de colonnes manquantes) ──────────────────
    async with engine.begin() as conn:
        # members.email_notifications
        r_mem = await conn.exec_driver_sql("PRAGMA table_info(members)")
        cols_mem = [row[1] for row in r_mem.fetchall()]
        if "email_notifications" not in cols_mem:
            await conn.exec_driver_sql(
                "ALTER TABLE members ADD COLUMN email_notifications BOOLEAN NOT NULL DEFAULT 1"
            )
        if "membership_type" not in cols_mem:
            await conn.exec_driver_sql(
                "ALTER TABLE members ADD COLUMN membership_type VARCHAR(20) NOT NULL DEFAULT 'APPARTENANCE'"
            )
        if "membership_start_date" not in cols_mem:
            await conn.exec_driver_sql(
                "ALTER TABLE members ADD COLUMN membership_start_date DATE"
            )

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
        r_msg = await conn.exec_driver_sql("PRAGMA table_info(messages)")
        cols_msg = [row[1] for row in r_msg.fetchall()]
        if "parent_id" not in cols_msg:
            await conn.exec_driver_sql(
                "ALTER TABLE messages ADD COLUMN parent_id INTEGER REFERENCES messages(id)"
            )
        if "visio_url" not in cols_msg:
            await conn.exec_driver_sql(
                "ALTER TABLE messages ADD COLUMN visio_url VARCHAR(500)"
            )
        # message_attachments : créée par Base.metadata.create_all (nouveau modèle)

        # ── Agenda ─────────────────────────────────────────────────────────
        # La table lodge_events est créée par Base.metadata.create_all
        r_ev = await conn.exec_driver_sql("PRAGMA table_info(lodge_events)")
        cols_ev = [row[1] for row in r_ev.fetchall()]
        if "visibility_group_id" not in cols_ev:
            await conn.exec_driver_sql(
                "ALTER TABLE lodge_events ADD COLUMN visibility_group_id INTEGER REFERENCES lodge_groups(id)"
            )
        if "meeting_url" not in cols_ev:
            await conn.exec_driver_sql(
                "ALTER TABLE lodge_events ADD COLUMN meeting_url VARCHAR(500)"
            )

        # ── Tracé de tenue — corps narratif ────────────────────────────────
        r_mtg = await conn.exec_driver_sql("PRAGMA table_info(meetings)")
        cols_mtg = [row[1] for row in r_mtg.fetchall()]
        if "compte_rendu_html" not in cols_mtg:
            await conn.exec_driver_sql(
                "ALTER TABLE meetings ADD COLUMN compte_rendu_html TEXT"
            )

        # ── GED — group_id sur doc_spaces et doc_folders ───────────────────
        r_ds = await conn.exec_driver_sql("PRAGMA table_info(doc_spaces)")
        cols_ds = [row[1] for row in r_ds.fetchall()]
        if "group_id" not in cols_ds:
            await conn.exec_driver_sql(
                "ALTER TABLE doc_spaces ADD COLUMN group_id INTEGER REFERENCES lodge_groups(id)"
            )

        r_df = await conn.exec_driver_sql("PRAGMA table_info(doc_folders)")
        cols_df = [row[1] for row in r_df.fetchall()]
        if "group_id" not in cols_df:
            await conn.exec_driver_sql(
                "ALTER TABLE doc_folders ADD COLUMN group_id INTEGER REFERENCES lodge_groups(id)"
            )

        # ── GED — table doc_shares (partage externe) ──────────────────────────
        r_ds2 = await conn.exec_driver_sql("PRAGMA table_info(doc_shares)")
        cols_ds2 = [row[1] for row in r_ds2.fetchall()]
        if not cols_ds2:
            # La table sera créée par Base.metadata.create_all au prochain démarrage
            # mais on la crée immédiatement si elle manque
            await conn.exec_driver_sql("""
                CREATE TABLE IF NOT EXISTS doc_shares (
                    id INTEGER PRIMARY KEY,
                    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    token VARCHAR(64) NOT NULL UNIQUE,
                    label VARCHAR(200),
                    expires_at DATETIME,
                    max_uses INTEGER,
                    use_count INTEGER NOT NULL DEFAULT 0,
                    password_hash VARCHAR(200),
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    created_by_id INTEGER REFERENCES members(id),
                    created_at DATETIME DEFAULT (datetime('now'))
                )
            """)
            await conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_doc_shares_token ON doc_shares(token)"
            )

        # ── GED — link_url sur documents + original_filename nullable ───────
        r_doc = await conn.exec_driver_sql("PRAGMA table_info(documents)")
        cols_doc_info = r_doc.fetchall()
        cols_doc = [row[1] for row in cols_doc_info]

        if "link_url" not in cols_doc:
            await conn.exec_driver_sql(
                "ALTER TABLE documents ADD COLUMN link_url VARCHAR(2000)"
            )

        # Rendre original_filename nullable (NOT NULL → NULL) via recréation SQLite
        orig_col = next((row for row in cols_doc_info if row[1] == "original_filename"), None)
        if orig_col and orig_col[3] == 1:  # notnull == 1
            await conn.exec_driver_sql("""
                CREATE TABLE IF NOT EXISTS documents_new (
                    id INTEGER PRIMARY KEY,
                    folder_id INTEGER NOT NULL REFERENCES doc_folders(id) ON DELETE CASCADE,
                    name VARCHAR(300) NOT NULL,
                    description TEXT,
                    original_filename VARCHAR(300),
                    mime_type VARCHAR(100),
                    file_size INTEGER,
                    storage_path VARCHAR(500),
                    link_url VARCHAR(2000),
                    download_count INTEGER NOT NULL DEFAULT 0,
                    status VARCHAR(20) NOT NULL,
                    author_id INTEGER REFERENCES members(id),
                    validated_by_id INTEGER REFERENCES members(id),
                    validated_at DATETIME,
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """)
            await conn.exec_driver_sql(
                "INSERT OR IGNORE INTO documents_new SELECT * FROM documents"
            )
            await conn.exec_driver_sql("DROP TABLE documents")
            await conn.exec_driver_sql("ALTER TABLE documents_new RENAME TO documents")

        # ── Correction logo blanc → transparent dans lodge_settings ──────────
        await conn.exec_driver_sql(
            "UPDATE lodge_settings SET logo_url = '/static/img/sceau-socrate-transparent.png' "
            "WHERE logo_url = '/static/img/sceau-socrate-blanc.png'"
        )

        # ── Seuils assiduité dans lodge_settings ──────────────────────────────
        r_ls = await conn.exec_driver_sql("PRAGMA table_info(lodge_settings)")
        ls_cols = {row[1] for row in r_ls.fetchall()}
        if "attendance_threshold_warn" not in ls_cols:
            await conn.exec_driver_sql(
                "ALTER TABLE lodge_settings ADD COLUMN attendance_threshold_warn INTEGER DEFAULT 70"
            )
        if "attendance_threshold_danger" not in ls_cols:
            await conn.exec_driver_sql(
                "ALTER TABLE lodge_settings ADD COLUMN attendance_threshold_danger INTEGER DEFAULT 50"
            )
        if "visio_provider" not in ls_cols:
            await conn.exec_driver_sql(
                "ALTER TABLE lodge_settings ADD COLUMN visio_provider VARCHAR(50)"
            )
        if "visio_server_url" not in ls_cols:
            await conn.exec_driver_sql(
                "ALTER TABLE lodge_settings ADD COLUMN visio_server_url VARCHAR(500)"
            )
        if "visio_room_prefix" not in ls_cols:
            await conn.exec_driver_sql(
                "ALTER TABLE lodge_settings ADD COLUMN visio_room_prefix VARCHAR(100)"
            )

    # ── Canal "Général" par défaut ─────────────────────────────────────────
    async with engine.begin() as conn:
        from sqlalchemy import text
        result = await conn.execute(text("SELECT COUNT(*) FROM chat_channels"))
        count = result.scalar()
        if count == 0:
            await conn.execute(text(
                "INSERT INTO chat_channels (name, description, type, is_readonly, created_at) "
                "VALUES ('Général', 'Canal principal de la loge', 'GENERAL', 0, datetime('now'))"
            ))
            await conn.execute(text(
                "INSERT INTO chat_channels (name, description, type, is_readonly, created_at) "
                "VALUES ('Annonces', 'Annonces officielles', 'GENERAL', 1, datetime('now'))"
            ))

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

templates = Jinja2Templates(directory="app/templates")
# Valeur de fallback : les pages qui ne calculent pas le compteur affichent 0
templates.env.globals["global_unread_messages"] = 0

# ── Filtre Jinja2 : rendu des messages chat (bold, liens cliquables) ──────────
import re
from markupsafe import Markup, escape as _escape

def _render_chat(text: str) -> Markup:
    if not text:
        return Markup("")
    url_pat = re.compile(r"(https?://[^\s]+)")
    parts = []
    last = 0
    for m in url_pat.finditer(text):
        segment = str(_escape(text[last:m.start()]))
        segment = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", segment)
        segment = segment.replace("\n", "<br>")
        parts.append(segment)
        url = m.group(1)
        eu = str(_escape(url))
        parts.append(
            f'<a href="{eu}" target="_blank" rel="noopener" '
            f'class="underline opacity-80 hover:opacity-100 break-all">{eu}</a>'
        )
        last = m.end()
    tail = str(_escape(text[last:]))
    tail = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", tail)
    tail = tail.replace("\n", "<br>")
    parts.append(tail)
    return Markup("".join(parts))

templates.env.filters["render_chat"] = _render_chat

# ── Static files ───────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="app/static"), name="static")

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
app.include_router(groups_router.router)
app.include_router(documents_router.router)
app.include_router(chat_router.router)
app.include_router(sharing_router.router)          # /documents/file/{id}/share/…
app.include_router(sharing_router.public_router)   # /share/{token} — accès public sans auth
# app.include_router(forum.router)
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

    # ── Messages non lus ────────────────────────────────────────────────────
    unread_msg_r = await db.execute(
        select(sql_func.count(MsgRecipient.id))
        .join(Msg, Msg.id == MsgRecipient.message_id)
        .where(
            MsgRecipient.member_id == member.id,
            MsgRecipient.read_at.is_(None),
            Msg.sent_at.isnot(None),
        )
    )
    global_unread_messages = unread_msg_r.scalar_one() or 0

    # ── Messages chat non lus ─────────────────────────────────────────────
    try:
        from app.routers.chat import _accessible_channels, _unread_count_per_channel
        chat_channels = await _accessible_channels(member, db)
        chat_ch_ids = [c.id for c in chat_channels]
        chat_unread_map = await _unread_count_per_channel(member.id, chat_ch_ids, db)
        global_unread_chat = sum(chat_unread_map.values())
    except Exception:
        global_unread_chat = 0

    # ── Prochains événements agenda (visibles par ce membre) ────────────────
    upcoming_events_r = await db.execute(
        select(LodgeEvent)
        .where(LodgeEvent.start_datetime >= datetime.combine(today, datetime.min.time()))
        .order_by(LodgeEvent.start_datetime)
        .limit(20)  # on filtre côté Python après vérification visibilité
    )
    _all_upcoming_events = upcoming_events_r.scalars().all()
    upcoming_events = []
    for ev in _all_upcoming_events:
        if await _event_visible_to(ev, member, db, user):
            upcoming_events.append(ev)
            if len(upcoming_events) >= 4:
                break

    # ── Messages récents non lus ─────────────────────────────────────────────
    recent_msgs_r = await db.execute(
        select(MsgRecipient)
        .join(Msg, Msg.id == MsgRecipient.message_id)
        .where(
            MsgRecipient.member_id == member.id,
            MsgRecipient.read_at.is_(None),
            Msg.sent_at.isnot(None),
        )
        .options(selectinload(MsgRecipient.message))
        .order_by(Msg.sent_at.desc())
        .limit(4)
    )
    recent_unread_msgs = recent_msgs_r.scalars().all()

    # Expéditeurs des messages récents
    recent_sender_ids = {r.message.sender_id for r in recent_unread_msgs}
    recent_senders_map: dict[int, Member] = {}
    if recent_sender_ids:
        rs = await db.execute(select(Member).where(Member.id.in_(recent_sender_ids)))
        recent_senders_map = {m.id: m for m in rs.scalars().all()}

    # ── Derniers maçons passants ─────────────────────────────────────────────
    recent_visitors_r = await db.execute(
        select(MeetingVisitor)
        .options(
            selectinload(MeetingVisitor.visitor),
            selectinload(MeetingVisitor.meeting),
        )
        .join(Meeting, Meeting.id == MeetingVisitor.meeting_id)
        .where(MeetingVisitor.status == VisitorStatus.CONFIRMED)
        .order_by(Meeting.meeting_date.desc(), MeetingVisitor.id.desc())
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
        # pastille messages
        "global_unread_messages": global_unread_messages,
        "global_unread_chat": global_unread_chat,
        # agenda & messages pour dashboard
        "upcoming_events": upcoming_events,
        "recent_unread_msgs": recent_unread_msgs,
        "recent_senders_map": recent_senders_map,
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
