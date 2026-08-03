"""Point d'entrée FastAPI — Portail Socrate"""
import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime

# Logging configuré en tout premier (avant les autres imports)
from app.logging_config import configure_logging as _configure_logging
from app.config import get_settings as _get_settings_early
_configure_logging(_get_settings_early().environment)

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# IMPORTANT : importer labels AVANT les routers pour que le monkey-patch
# de Jinja2Templates s'applique à toutes les instances créées ensuite.
import app.services.labels  # noqa: F401

from typing import Annotated
from app.config import get_settings
from app.database import engine, Base, get_db
from app.migrations import run_lightweight_migrations, ensure_wal_mode
from app.dependencies import get_current_user, can_manage_attendance
from app.routers import auth, members, meetings, finance, programs, attendance, announcements
from app.routers import settings as settings_router
from app.routers import messages as messages_router
from app.routers import calendar as calendar_router
from app.routers import groups as groups_router
from app.routers import documents as documents_router
from app.routers import chat as chat_router
from app.routers import sharing as sharing_router
from app.routers import news as news_router
from app.routers import polls as polls_router
from app.routers import planches as planches_router
from app.routers import anniversaires as anniv_router
from app.routers import push as push_router
from app.routers import forum as forum_router
from app.routers import projects as projects_router
from app.routers import admin as admin_router
from app.routers import mailing as mailing_router
from app.routers import bookmarks as bookmarks_router
from app.routers import guide as guide_router
from app.routers import faq as faq_router
from app.routers import presence as presence_router
from app.routers import contact_confirmation as contact_confirmation_router
from app.routers import search as search_router
from app.routers import notifications as notifications_router
from app.routers import engagement as engagement_router
# Import des modèles pour que Base.metadata.create_all les crée
import app.models.messaging      # noqa: F401
import app.models.reports        # noqa: F401
import app.models.planches       # noqa: F401
import app.models.lodge_calendar  # noqa: F401
import app.models.groups          # noqa: F401
import app.models.documents       # noqa: F401
import app.models.chat            # noqa: F401
import app.models.content       # noqa: F401
import app.models.system        # noqa: F401  # PushSubscription, Notification, etc.
import app.models.forum         # noqa: F401  # ForumTheme/Subject/Message/Subscription
import app.models.mailing       # noqa: F401  # MailingList/Campaign/Delivery
import app.models.bookmarks     # noqa: F401  # Bookmark
import app.models.analytics     # noqa: F401  # PageView
from sqlalchemy import select, func as sql_func, or_
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
from app.models.lodge_calendar import LodgeEvent, EventVisibility
from app.routers.calendar import _event_visible_to
from app.models.groups import LodgeGroup, GroupMembership
from app.models.chat import ChatChannel, ChatChannelMember, ChatMessage, ChatRead, ChannelType

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── WAL mode : lectures simultanées même pendant une écriture ────────────
    # Indispensable pour éviter "database is locked" quand la ré-indexation
    # ou une sauvegarde tourne en parallèle d'une requête utilisateur.
    await ensure_wal_mode(engine)

    # Démarrage : créer les tables si elles n'existent pas
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await run_lightweight_migrations(engine)

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

    # ── Sauvegarde hebdomadaire ───────────────────────────────────────────────
    from app.services.backup import weekly_backup_loop

    async def _get_admin_email():
        async with engine.begin() as _conn:
            from sqlalchemy import text as _text
            r = await _conn.execute(_text("SELECT admin_email FROM lodge_settings LIMIT 1"))
            row = r.fetchone()
            return row[0] if row and row[0] else None

    _backup_task = asyncio.ensure_future(weekly_backup_loop(_get_admin_email))

    # ── Anniversaires maçonniques (rappel J-1) ────────────────────────────────
    from app.services.anniversaires import daily_anniversary_loop
    from app.database import AsyncSessionLocal

    async def _get_active_members():
        async with AsyncSessionLocal() as s:
            r = await s.execute(
                select(Member).where(Member.status == MemberStatus.ACTIVE)
            )
            return list(r.scalars().all())

    async def _get_lodge_name():
        async with engine.begin() as _conn:
            from sqlalchemy import text as _text
            r = await _conn.execute(_text("SELECT name FROM lodge_settings LIMIT 1"))
            row = r.fetchone()
            return row[0] if row and row[0] else settings.lodge_name

    _anniv_task = asyncio.ensure_future(daily_anniversary_loop(_get_active_members, _get_lodge_name))

    # ── Rappels J-3 sur les tâches projet ────────────────────────────────────
    from app.services.projects_reminders import daily_task_reminder_loop
    _task_reminder_task = asyncio.ensure_future(daily_task_reminder_loop())

    # ── Rappels J-3 avant clôture de l'appel à tranche ───────────────────────
    from app.services.contribution_reminders import daily_contribution_reminder_loop
    _contrib_reminder_task = asyncio.ensure_future(daily_contribution_reminder_loop())

    # ── Planificateur d'envois mailing différés ───────────────────────────────
    from app.services.mailing_scheduler import mailing_scheduler_loop
    _mailing_sched_task = asyncio.ensure_future(mailing_scheduler_loop())

    # ── Pré-chargement du cache des libellés personnalisés ───────────────────
    try:
        from app.services.labels import _load_all as _load_labels
        await _load_labels()
    except Exception:
        pass

    # ── Bootstrap des listes système de diffusion ────────────────────────────
    try:
        from app.services.mailing import ensure_system_lists
        await ensure_system_lists()
    except Exception:
        pass

    # ── Index FTS5 pour la recherche full-text GED ───────────────────────────
    try:
        from app.services.doc_index import ensure_fts_table
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as _s:
            await ensure_fts_table(_s)
    except Exception:
        pass

    yield
    # Arrêt
    _backup_task.cancel()
    _anniv_task.cancel()
    _task_reminder_task.cancel()
    _contrib_reminder_task.cancel()
    _mailing_sched_task.cancel()
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

def _is_api_request(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return (
        request.url.path.startswith("/api/")
        or ("application/json" in accept and "text/html" not in accept)
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    tb = _tb.format_exc()
    # Toujours tracer dans le log serveur (sinon les 500 sont invisibles en prod)
    import logging as _logging
    _logging.getLogger("portail.errors").error(
        "500 sur %s %s\n%s", request.method, request.url.path, tb
    )
    if _is_api_request(request):
        return PlainTextResponse(f"Erreur interne:\n{tb}", status_code=500)
    try:
        return templates.TemplateResponse(
            request, "errors/500.html",
            {"detail": tb if settings.environment == "development" else None},
            status_code=500,
        )
    except Exception:
        return PlainTextResponse(f"Erreur interne:\n{tb}", status_code=500)


@app.exception_handler(401)
async def unauthorized_handler(request: Request, exc):
    if _is_api_request(request):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Authentification requise"}, status_code=401)
    from urllib.parse import quote
    next_url = quote(str(request.url.path))
    return RedirectResponse(url=f"/auth/login?next={next_url}", status_code=302)


@app.exception_handler(403)
async def forbidden_handler(request: Request, exc):
    if _is_api_request(request):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Accès refusé"}, status_code=403)
    try:
        return templates.TemplateResponse(request, "errors/403.html", {}, status_code=403)
    except Exception:
        return PlainTextResponse("Accès refusé", status_code=403)


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    if _is_api_request(request):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Ressource introuvable"}, status_code=404)
    try:
        return templates.TemplateResponse(request, "errors/404.html", {}, status_code=404)
    except Exception:
        return PlainTextResponse("Page introuvable", status_code=404)


# Cache en mémoire pour les settings lus à chaque requête (TTL 30s)
import time as _time
_settings_cache: dict = {}
_SETTINGS_TTL = 30.0

async def _get_setting_cached(key: str):
    """Lit un setting avec cache mémoire TTL 30s pour ne pas interroger la DB à chaque requête."""
    from app.services.settings_store import get_setting
    now = _time.monotonic()
    entry = _settings_cache.get(key)
    if entry and now - entry[1] < _SETTINGS_TTL:
        return entry[0]
    value = await get_setting(key)
    _settings_cache[key] = (value, now)
    return value


@app.middleware("http")
async def maintenance_banner_middleware(request: Request, call_next):
    """Charge la bannière maintenance + flag confidentialité dans request.state.
    Si maintenance_mode est activé, redirige tous les non-admins vers la page maintenance."""
    path = request.url.path
    bypass = (
        path.startswith("/static")
        or path in {"/auth/login", "/auth/logout"}
        or path == "/maintenance"
    )
    if not bypass:
        try:
            maintenance_mode = await _get_setting_cached("maintenance_mode")
            if maintenance_mode:
                # Vérifier si l'utilisateur est admin via le token (sans DB — juste le payload JWT)
                is_admin = False
                token = request.cookies.get("access_token")
                if token:
                    try:
                        from app.dependencies import decode_token
                        from app.database import AsyncSessionLocal
                        from app.models.identity import User
                        from sqlalchemy import select as _sel
                        payload = decode_token(token)
                        user_id = int(payload.get("sub", 0))
                        async with AsyncSessionLocal() as _db:
                            _u = (await _db.execute(_sel(User).where(User.id == user_id))).scalar_one_or_none()
                            is_admin = bool(_u and _u.is_admin)
                    except Exception:
                        pass
                if not is_admin:
                    msg = await _get_setting_cached("maintenance_message") or None
                    return templates.TemplateResponse(
                        request, "errors/maintenance.html",
                        {"message": msg},
                        status_code=503,
                    )
        except Exception:
            pass

    # ── Bannière maintenance (simple message) ────────────────────────────────
    try:
        request.state.banner = await _get_setting_cached("maintenance_banner")
    except Exception:
        request.state.banner = None

    # ── Flag confidentialité (via cache 30s) ────────────────────────────────
    try:
        from app.services.confidentiality import KEY as _CONF_KEY, DEFAULTS as _CONF_DEFAULTS
        _conf_stored = await _get_setting_cached(_CONF_KEY) or {}
        _conf = dict(_CONF_DEFAULTS)
        if isinstance(_conf_stored, dict):
            _conf.update(_conf_stored)
        request.state.show_conf_banner = bool(_conf.get("show_confidentiality_banner"))
    except Exception:
        request.state.show_conf_banner = False

    return await call_next(request)


@app.middleware("http")
async def two_factor_gate_middleware(request: Request, call_next):
    """Bloque l'accès aux pages tant que le code 2FA n'a pas été vérifié après
    un login (claim "2fa_pending" présent dans le token d'accès)."""
    path = request.url.path
    bypass = (
        path.startswith("/static")
        or path.startswith("/auth/")
        or path in {"/manifest.json", "/sw.js", "/favicon.ico"}
    )
    if not bypass:
        token = request.cookies.get("access_token")
        if token:
            try:
                from app.dependencies import decode_token
                payload = decode_token(token)
                if payload.get("2fa_pending"):
                    from urllib.parse import quote
                    next_path = path + (f"?{request.url.query}" if request.url.query else "")
                    return RedirectResponse(
                        url=f"/auth/2fa/verify?next={quote(next_path, safe='')}",
                        status_code=303,
                    )
            except Exception:
                pass
    return await call_next(request)


def _detect_device(user_agent: str) -> str:
    ua = (user_agent or "").lower()
    if not ua:
        return "inconnu"
    if any(b in ua for b in ("bot", "spider", "crawler", "slurp", "facebookexternalhit", "preview")):
        return "bot"
    if "ipad" in ua or "tablet" in ua or ("android" in ua and "mobile" not in ua):
        return "tablette"
    if "mobile" in ua or "iphone" in ua or "android" in ua:
        return "mobile"
    return "ordinateur"


async def _record_pageview(request: Request) -> None:
    """Enregistre une page vue (analytique interne, sans JS). Best-effort :
    ne doit jamais faire échouer la requête qui l'a déclenché."""
    try:
        from app.database import AsyncSessionLocal
        from app.models.analytics import PageView
        from app.models.identity import User
        from app.dependencies import decode_token
        from urllib.parse import urlparse

        referrer = request.headers.get("referer", "")
        referrer_host = "direct"
        if referrer:
            try:
                host = urlparse(referrer).netloc
                if host:
                    referrer_host = "interne" if host == request.url.netloc else host
            except Exception:
                pass

        device = _detect_device(request.headers.get("user-agent", ""))

        member_id = None
        session_id = None
        token = request.cookies.get("access_token")
        if token:
            try:
                payload = decode_token(token)
                session_id = payload.get("jti") or None
                user_id = int(payload.get("sub"))
            except Exception:
                user_id = None
        else:
            user_id = None

        async with AsyncSessionLocal() as db:
            if user_id:
                r = await db.execute(select(User.member_id).where(User.id == user_id))
                member_id = r.scalar_one_or_none()
            db.add(PageView(
                path=request.url.path,
                referrer_host=referrer_host,
                device=device,
                member_id=member_id,
                session_id=session_id,
            ))
            await db.commit()
    except Exception:
        import logging as _logging
        _logging.getLogger(__name__).warning("Échec enregistrement page vue", exc_info=True)


@app.middleware("http")
async def analytics_middleware(request: Request, call_next):
    """Analytique interne légère, côté serveur uniquement (pas de JS, pas de
    cookie tiers) : chemin, provenance (hôte seulement), appareil déduit du
    user-agent, membre si connecté. Ne ralentit jamais la réponse : l'écriture
    se fait en tâche de fond après le retour de la page."""
    response = await call_next(request)
    try:
        if (
            request.method == "GET"
            and response.status_code == 200
            and response.headers.get("content-type", "").startswith("text/html")
        ):
            asyncio.create_task(_record_pageview(request))  # noqa — fire & forget
    except Exception:
        pass
    return response


# Instance Jinja2 partagée (filtres datefr + label déjà enregistrés)
from app.template_engine import templates

# Valeur de fallback : les pages qui ne calculent pas le compteur affichent 0
templates.env.globals["global_unread_messages"] = 0
templates.env.globals["global_unread_chat"] = 0

# Filtre `| label` pour personnaliser l'affichage des enums depuis l'admin
from app.services.labels import register_jinja as _register_label_filter
_register_label_filter(templates.env)

# Filtres `| presence_status` / `| presence_label` — présence en ligne
from app.services.presence import register_jinja as _register_presence_filters
_register_presence_filters(templates.env)

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


def _linkify(text: str) -> Markup:
    """Échappe le texte puis rend les URLs cliquables — pour les corps de
    message en texte brut (notifications système : sondages, actualités,
    documents partagés) qui n'ont pas de version body_html."""
    if not text:
        return Markup("")
    url_pat = re.compile(r"(https?://[^\s]+)")
    parts = []
    last = 0
    for m in url_pat.finditer(text):
        parts.append(str(_escape(text[last:m.start()])))
        url = m.group(1)
        eu = str(_escape(url))
        parts.append(
            f'<a href="{eu}" target="_blank" rel="noopener" '
            f'class="text-loge-700 underline hover:text-loge-900 break-all">{eu}</a>'
        )
        last = m.end()
    parts.append(str(_escape(text[last:])))
    return Markup("".join(parts))

templates.env.filters["linkify"] = _linkify

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
app.include_router(news_router.router)
app.include_router(polls_router.router)
app.include_router(planches_router.router)
app.include_router(anniv_router.router)
app.include_router(push_router.router)
app.include_router(forum_router.router)
app.include_router(projects_router.router)
app.include_router(admin_router.router)
app.include_router(mailing_router.router)
app.include_router(bookmarks_router.router)
app.include_router(guide_router.router)
app.include_router(faq_router.router)
app.include_router(presence_router.router)
app.include_router(contact_confirmation_router.router)
app.include_router(search_router.router)
app.include_router(notifications_router.router)
app.include_router(engagement_router.router)
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

    # Précharger les groupes des événements GROUP pour éviter 1 SELECT par événement
    _group_event_ids = {ev.visibility_group_id for ev in _all_upcoming_events
                       if ev.visibility == EventVisibility.GROUP and ev.visibility_group_id}
    _group_member_sets: dict[int, set[int]] = {}
    if _group_event_ids:
        from app.routers.groups import resolve_group_member_ids
        for gid in _group_event_ids:
            grp = await db.get(LodgeGroup, gid)
            if grp:
                _group_member_sets[gid] = await resolve_group_member_ids(db, grp)

    upcoming_events = []
    for ev in _all_upcoming_events:
        # Vérification sans DB pour les cas non-GROUP
        if ev.is_personal:
            visible = user.is_admin or ev.created_by_id == member.id
        elif ev.visibility == EventVisibility.GROUP:
            if ev.visibility_group_id and ev.visibility_group_id in _group_member_sets:
                visible = member.id in _group_member_sets[ev.visibility_group_id]
            else:
                visible = await _event_visible_to(ev, member, db, user)
        else:
            visible = await _event_visible_to(ev, member, db, user)
        if visible:
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

    # ── Actualités récentes (dashboard widget) ────────────────────────────────
    from app.models.content import NewsArticle as _NewsArticle
    _now = datetime.now()
    _news_r = await db.execute(
        select(_NewsArticle)
        .where(
            _NewsArticle.is_online == True,
            or_(_NewsArticle.publish_from == None, _NewsArticle.publish_from <= _now),
            or_(_NewsArticle.publish_until == None, _NewsArticle.publish_until >= _now),
        )
        .order_by(_NewsArticle.is_featured.desc(), _NewsArticle.created_at.desc())
        .limit(3)
    )
    recent_news = _news_r.scalars().all()

    # ── Sondages actifs (dashboard widget) ───────────────────────────────────
    from app.models.content import Poll as _Poll, PollVote as _PollVote
    from app.routers.polls import _can_access as _poll_can_access
    _polls_r = await db.execute(
        select(_Poll)
        .options(selectinload(_Poll.votes))
        .where(or_(_Poll.ends_at == None, _Poll.ends_at > datetime.now()))
        .order_by(_Poll.created_at.desc())
        .limit(20)
    )
    _active_polls = _polls_r.scalars().all()
    _active_polls_filtered = []
    for _p in _active_polls:
        if await _poll_can_access(_p, member, user.is_admin, db):
            _active_polls_filtered.append(_p)
    _voted_ids_r = await db.execute(
        select(_PollVote.poll_id).where(_PollVote.member_id == member.id)
    )
    _voted_ids = {r[0] for r in _voted_ids_r.all()}
    active_polls = _active_polls_filtered[:5]
    pending_polls = [p for p in _active_polls_filtered if p.id not in _voted_ids][:3]

    # ── Anniversaires maçonniques (30 prochains jours) ───────────────────────
    from app.services.anniversaires import upcoming as _upcoming_anniv
    _all_active = await db.execute(
        select(Member).where(Member.status == MemberStatus.ACTIVE)
    )
    upcoming_anniv = _upcoming_anniv(list(_all_active.scalars().all()), days=30, today=today)[:5]

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
        "recent_news": recent_news,
        "active_polls": active_polls,
        "pending_polls": pending_polls,
        "voted_poll_ids": _voted_ids,
        "upcoming_anniv": upcoming_anniv,
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
