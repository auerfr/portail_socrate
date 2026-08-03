"""Module Administration — réservé aux admins techniques.

6 onglets :
  /admin/                  → vue d'ensemble (santé, KPIs, alertes)
  /admin/users             → console utilisateurs
  /admin/audit             → journal d'audit
  /admin/data              → backups, RGPD, maintenance DB         (à venir)
  /admin/comm              → file SMTP, templates                  (à venir)
  /admin/config            → grades, tags, modèles PV, calendrier  (à venir)
"""
import os
import shutil
from datetime import datetime, date, timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, func, desc, or_, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin
from app.models.identity import User, Member, MemberStatus
from app.models.meetings import Meeting, Attendance
from app.models.projects import Task, TaskStatus
from app.models.system import AuditLog
from app.services.audit import log_audit


router = APIRouter(prefix="/admin", tags=["admin"])
from app.template_engine import templates
# Filtre `| label` pour afficher les libellés personnalisés depuis l'admin
from app.services.labels import register_jinja as _register_label_filter
_register_label_filter(templates.env)


# ─────────────────────────────────────────────────────────────────────────────
#  Vue d'ensemble — santé du système
# ─────────────────────────────────────────────────────────────────────────────

def _disk_usage_db() -> dict:
    """Taille de la base + de l'arborescence uploads/backups."""
    base = os.getcwd()
    out = {}
    for name in ("socrate_local.db", "socrate.db"):
        p = os.path.join(base, name)
        if os.path.isfile(p):
            out["db_path"] = p
            out["db_size_mb"] = round(os.path.getsize(p) / (1024 * 1024), 2)
            break
    for sub in ("uploads", "backups"):
        d = os.path.join(base, sub)
        total = 0
        if os.path.isdir(d):
            for root, _, files in os.walk(d):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
        out[f"{sub}_size_mb"] = round(total / (1024 * 1024), 2)
    try:
        usage = shutil.disk_usage(base)
        out["disk_free_gb"] = round(usage.free / (1024 ** 3), 1)
        out["disk_total_gb"] = round(usage.total / (1024 ** 3), 1)
        out["disk_used_pct"] = int((usage.used / usage.total) * 100)
    except Exception:
        pass
    return out


def _last_backup() -> Optional[dict]:
    d = os.path.join(os.getcwd(), "backups")
    if not os.path.isdir(d):
        return None
    files = [f for f in os.listdir(d) if f.endswith(".zip")]
    if not files:
        return None
    files.sort(reverse=True)
    last = files[0]
    p = os.path.join(d, last)
    return {
        "filename": last,
        "mtime": datetime.fromtimestamp(os.path.getmtime(p)),
        "size_mb": round(os.path.getsize(p) / (1024 * 1024), 2),
        "count": len(files),
    }


@router.get("/", response_class=HTMLResponse)
async def admin_overview(
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    # KPIs membres
    total_members = (await db.execute(select(func.count(Member.id)))).scalar() or 0
    active_members = (await db.execute(
        select(func.count(Member.id)).where(Member.status == MemberStatus.ACTIVE)
    )).scalar() or 0

    # KPIs utilisateurs (comptes login)
    total_users = (await db.execute(select(func.count(User.id)))).scalar() or 0
    active_users = (await db.execute(
        select(func.count(User.id)).where(User.is_active == True)  # noqa: E712
    )).scalar() or 0
    admin_count = (await db.execute(
        select(func.count(User.id)).where(User.is_admin == True)   # noqa: E712
    )).scalar() or 0
    # Comptes jamais utilisés
    never_logged = (await db.execute(
        select(func.count(User.id)).where(User.last_login_at.is_(None))
    )).scalar() or 0
    # Comptes inactifs > 90 j
    threshold = datetime.utcnow() - timedelta(days=90)
    stale_users = (await db.execute(
        select(func.count(User.id)).where(
            User.last_login_at.isnot(None),
            User.last_login_at < threshold,
        )
    )).scalar() or 0

    # Tenues à venir
    today = date.today()
    upcoming_meetings = (await db.execute(
        select(func.count(Meeting.id)).where(Meeting.meeting_date >= today)
    )).scalar() or 0

    # Tâches en retard (global)
    overdue_tasks = (await db.execute(
        select(func.count(Task.id)).where(
            Task.due_date < today,
            Task.status != TaskStatus.DONE,
            Task.status != TaskStatus.CANCELLED,
        )
    )).scalar() or 0

    # Disque + dernier backup
    disk = _disk_usage_db()
    last_backup = _last_backup()
    backup_old = False
    if last_backup:
        backup_old = (datetime.utcnow() - last_backup["mtime"]).days > 8

    # Audit récent
    recent_audit = (await db.execute(
        select(AuditLog).order_by(desc(AuditLog.created_at)).limit(10)
    )).scalars().all()

    # Sparkline activité 30 jours (audit_logs/jour)
    from sqlalchemy import text as sa_text
    spark_rows = await db.execute(sa_text(
        "SELECT DATE(created_at) AS d, COUNT(*) AS c FROM audit_logs "
        "WHERE created_at >= date('now', '-30 days') "
        "GROUP BY DATE(created_at) ORDER BY d"
    ))
    counts_by_day = {row[0]: row[1] for row in spark_rows.fetchall()}
    spark_points = []
    for i in range(29, -1, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        spark_points.append({"day": d, "count": counts_by_day.get(d, 0)})
    spark_max = max((p["count"] for p in spark_points), default=1) or 1
    actor_ids = {a.actor_id for a in recent_audit if a.actor_id}
    actors: dict[int, Member] = {}
    if actor_ids:
        for m in (await db.execute(select(Member).where(Member.id.in_(actor_ids)))).scalars().all():
            actors[m.id] = m

    # Alertes
    alerts = []
    if disk.get("disk_used_pct", 0) > 90:
        alerts.append(("rose", "Espace disque", f"{disk['disk_used_pct']}% utilisé"))
    if not last_backup:
        alerts.append(("amber", "Sauvegarde", "Aucune sauvegarde trouvée"))
    elif backup_old:
        days = (datetime.utcnow() - last_backup["mtime"]).days
        alerts.append(("amber", "Sauvegarde", f"Dernière il y a {days} jours"))
    if stale_users > 5:
        alerts.append(("blue", "Utilisateurs", f"{stale_users} comptes inactifs > 90 j"))

    return templates.TemplateResponse(request, "pages/admin/overview.html", {
        "current_user": user,
        "current_member": member,
        "kpi": {
            "total_members": total_members,
            "active_members": active_members,
            "total_users": total_users,
            "active_users": active_users,
            "admin_count": admin_count,
            "never_logged": never_logged,
            "stale_users": stale_users,
            "upcoming_meetings": upcoming_meetings,
            "overdue_tasks": overdue_tasks,
        },
        "disk": disk,
        "last_backup": last_backup,
        "recent_audit": recent_audit,
        "actors": actors,
        "alerts": alerts,
        "spark_points": spark_points,
        "spark_max": spark_max,
        "active_tab": "overview",
    })


# ─────────────────────────────────────────────────────────────────────────────
#  Console utilisateurs
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/users", response_class=HTMLResponse)
async def admin_users(
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    q: str = "",
    f_status: str = "",
):
    user, member = ctx
    stmt = (
        select(User, Member)
        .join(Member, Member.id == User.member_id)
        .order_by(Member.last_name, Member.first_name)
    )
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(
            Member.last_name.ilike(like),
            Member.first_name.ilike(like),
            User.login.ilike(like),
        ))
    if f_status == "active":
        stmt = stmt.where(User.is_active == True)  # noqa: E712
    elif f_status == "disabled":
        stmt = stmt.where(User.is_active == False)  # noqa: E712
    elif f_status == "admin":
        stmt = stmt.where(User.is_admin == True)  # noqa: E712
    elif f_status == "never":
        stmt = stmt.where(User.last_login_at.is_(None))
    elif f_status == "stale":
        thr = datetime.utcnow() - timedelta(days=90)
        stmt = stmt.where(User.last_login_at < thr)

    rows = (await db.execute(stmt)).all()

    return templates.TemplateResponse(request, "pages/admin/users.html", {
        "current_user": user,
        "current_member": member,
        "rows": rows,
        "q": q,
        "f_status": f_status,
        "today": datetime.utcnow(),
        "active_tab": "users",
    })


@router.post("/users/{user_id}/toggle-active")
async def admin_user_toggle_active(
    user_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    actor_user, actor_member = ctx
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(404)
    if u.id == actor_user.id:
        raise HTTPException(400, "Vous ne pouvez pas désactiver votre propre compte")
    u.is_active = not u.is_active
    await log_audit(
        db, actor_id=actor_member.id,
        action="USER_TOGGLE_ACTIVE",
        target_type="user", target_id=u.id, target_label=u.login,
        details=f"is_active → {u.is_active}",
        request=request,
    )
    await db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{user_id}/toggle-admin")
async def admin_user_toggle_admin(
    user_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    actor_user, actor_member = ctx
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(404)
    if u.id == actor_user.id:
        raise HTTPException(400, "Vous ne pouvez pas retirer vos propres droits admin")
    u.is_admin = not u.is_admin
    await log_audit(
        db, actor_id=actor_member.id,
        action="USER_TOGGLE_ADMIN",
        target_type="user", target_id=u.id, target_label=u.login,
        details=f"is_admin → {u.is_admin}",
        request=request,
    )
    await db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{user_id}/force-reset")
async def admin_user_force_reset(
    user_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Génère un token de réinitialisation valide 24h et retourne le lien à transmettre."""
    import secrets
    actor_user, actor_member = ctx
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(404)
    token = secrets.token_urlsafe(32)
    u.reset_token = token
    u.reset_token_expires = datetime.utcnow() + timedelta(hours=24)
    await log_audit(
        db, actor_id=actor_member.id,
        action="USER_RESET_PASSWORD",
        target_type="user", target_id=u.id, target_label=u.login,
        details="token généré (24h)",
        request=request,
    )
    await db.commit()
    # Retourne le lien dans un flash via la query string (le template l'affiche)
    return RedirectResponse(
        url=f"/admin/users?reset_for={u.id}&reset_token={token}", status_code=303
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Journal d'audit
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/audit", response_class=HTMLResponse)
async def admin_audit(
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    q: str = "",
    action: str = "",
    actor_id: str = "",
    days: int = 30,
    page: int = 1,
):
    user, member = ctx
    page_size = 50
    since = datetime.utcnow() - timedelta(days=max(1, min(days, 365)))

    stmt = select(AuditLog).where(AuditLog.created_at >= since)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(
            AuditLog.target_label.ilike(like),
            AuditLog.details.ilike(like),
            AuditLog.action.ilike(like),
        ))
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if actor_id.isdigit():
        stmt = stmt.where(AuditLog.actor_id == int(actor_id))

    # Total pour pagination
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = stmt.order_by(desc(AuditLog.created_at)).offset((page - 1) * page_size).limit(page_size)
    entries = (await db.execute(stmt)).scalars().all()

    # Cache acteurs
    actor_ids = {e.actor_id for e in entries if e.actor_id}
    actors: dict[int, Member] = {}
    if actor_ids:
        for m in (await db.execute(select(Member).where(Member.id.in_(actor_ids)))).scalars().all():
            actors[m.id] = m

    # Actions distinctes pour le filtre
    actions_avail = [a for a, in (await db.execute(
        select(AuditLog.action).distinct().order_by(AuditLog.action)
    )).all()]

    # Membres ayant produit des logs (pour le filtre acteur)
    actor_pick_ids = [a for a, in (await db.execute(
        select(AuditLog.actor_id).where(AuditLog.actor_id.isnot(None)).distinct()
    )).all()]
    actors_pick = []
    if actor_pick_ids:
        actors_pick = (await db.execute(
            select(Member).where(Member.id.in_(actor_pick_ids))
            .order_by(Member.last_name)
        )).scalars().all()

    return templates.TemplateResponse(request, "pages/admin/audit.html", {
        "current_user": user,
        "current_member": member,
        "entries": entries,
        "actors": actors,
        "actions_avail": actions_avail,
        "actors_pick": actors_pick,
        "q": q,
        "action": action,
        "actor_id": actor_id,
        "days": days,
        "page": page,
        "total": total,
        "page_size": page_size,
        "active_tab": "audit",
    })


@router.get("/data", response_class=HTMLResponse)
async def admin_data(
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx

    # Liste des backups
    backups = []
    d = os.path.join(os.getcwd(), "backups")
    if os.path.isdir(d):
        for f in sorted(os.listdir(d), reverse=True):
            if not f.endswith(".zip"):
                continue
            p = os.path.join(d, f)
            backups.append({
                "filename": f,
                "mtime": datetime.fromtimestamp(os.path.getmtime(p)),
                "size_mb": round(os.path.getsize(p) / (1024 * 1024), 2),
            })

    # Taille des principales tables (estimation par count)
    table_sizes = []
    tables_of_interest = [
        ("Membres",        "members"),
        ("Utilisateurs",   "users"),
        ("Documents",      "documents"),
        ("Tâches projet",  "tasks"),
        ("Messages chat",  "chat_messages"),
        ("Messagerie",     "messages"),
        ("Actualités",     "news_articles"),
        ("Tenues",         "meetings"),
        ("Audit",          "audit_logs"),
        ("Notifications",  "notifications"),
    ]
    from sqlalchemy import text as sa_text
    for lbl, tbl in tables_of_interest:
        try:
            r = await db.execute(sa_text(f"SELECT COUNT(*) FROM {tbl}"))
            table_sizes.append({"label": lbl, "table": tbl, "count": r.scalar() or 0})
        except Exception:
            pass
    table_sizes.sort(key=lambda x: x["count"], reverse=True)

    # Membres pour le dropdown RGPD
    all_members = (await db.execute(
        select(Member).order_by(Member.last_name, Member.first_name)
    )).scalars().all()

    disk = _disk_usage_db()

    return templates.TemplateResponse(request, "pages/admin/data.html", {
        "current_user": user,
        "current_member": member,
        "backups": backups,
        "table_sizes": table_sizes,
        "all_members": all_members,
        "disk": disk,
        "active_tab": "data",
    })


@router.get("/data/backup/{filename}/download")
async def admin_backup_download(
    filename: str,
    ctx: Annotated[tuple, Depends(require_admin)],
):
    """Télécharge un fichier de backup."""
    from fastapi.responses import FileResponse
    # Anti-traversal : on n'accepte que les noms simples
    if "/" in filename or "\\" in filename or ".." in filename or not filename.endswith(".zip"):
        raise HTTPException(400, "Nom de fichier invalide")
    path = os.path.join(os.getcwd(), "backups", filename)
    if not os.path.isfile(path):
        raise HTTPException(404)
    return FileResponse(path, media_type="application/zip", filename=filename)


@router.post("/data/backup/now")
async def admin_backup_now(
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Déclenche une sauvegarde immédiate (sans envoi email)."""
    actor_user, actor_member = ctx
    from app.services.backup import run_backup
    try:
        result = await run_backup(to_email=None)
        await log_audit(
            db, actor_id=actor_member.id, action="BACKUP_MANUAL",
            target_label=result.get("filename", "?") if isinstance(result, dict) else None,
            details=str(result), request=request, commit=True,
        )
    except Exception as e:
        await log_audit(
            db, actor_id=actor_member.id, action="BACKUP_FAIL",
            details=str(e)[:500], request=request, commit=True,
        )
    return RedirectResponse(url="/admin/data", status_code=303)


@router.post("/data/vacuum")
async def admin_db_vacuum(
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """VACUUM SQLite — défragmente et compacte le fichier."""
    from sqlalchemy import text as sa_text
    actor_user, actor_member = ctx
    try:
        await db.execute(sa_text("VACUUM"))
        await db.commit()
        await log_audit(
            db, actor_id=actor_member.id, action="DB_VACUUM",
            details="VACUUM exécuté avec succès", request=request, commit=True,
        )
    except Exception as e:
        await log_audit(
            db, actor_id=actor_member.id, action="DB_VACUUM_FAIL",
            details=str(e)[:500], request=request, commit=True,
        )
    return RedirectResponse(url="/admin/data", status_code=303)


@router.post("/data/purge-notifications")
async def admin_purge_notifications(
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    older_than_days: int = Form(60),
):
    """Purge des notifications lues plus anciennes que N jours."""
    from app.models.system import Notification
    actor_user, actor_member = ctx
    cutoff = datetime.utcnow() - timedelta(days=max(7, older_than_days))
    r = await db.execute(sa_delete(Notification).where(
        Notification.created_at < cutoff,
        Notification.read_at.isnot(None),
    ))
    await log_audit(
        db, actor_id=actor_member.id, action="PURGE_NOTIFICATIONS",
        target_label=f"avant {cutoff.date()}",
        details=f"{r.rowcount} notification(s) supprimée(s)",
        request=request,
    )
    await db.commit()
    return RedirectResponse(url="/admin/data", status_code=303)


@router.get("/data/rgpd-export/{member_id}")
async def admin_rgpd_export(
    member_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Export RGPD : zip avec un JSON contenant toutes les données du membre."""
    from fastapi.responses import StreamingResponse
    from app.services.rgpd import build_member_export_zip

    actor_user, actor_member = ctx
    m = (await db.execute(select(Member).where(Member.id == member_id))).scalar_one_or_none()
    if not m:
        raise HTTPException(404)

    buf = await build_member_export_zip(
        db, m, requested_by=f"{actor_member.last_name} {actor_member.first_name} (admin)"
    )

    await log_audit(
        db, actor_id=actor_member.id, action="RGPD_EXPORT",
        target_type="member", target_id=m.id,
        target_label=f"{m.last_name} {m.first_name}",
        request=request, commit=True,
    )

    fname = f"rgpd-{m.last_name.lower()}-{m.first_name.lower()}-{datetime.utcnow().strftime('%Y%m%d')}.zip"
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/comm", response_class=HTMLResponse)
async def admin_comm(
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    f_status: str = "",
    days: int = 30,
    page: int = 1,
):
    user, member = ctx
    from app.models.system import EmailLog, EmailStatus
    page_size = 50
    since = datetime.utcnow() - timedelta(days=max(1, min(days, 365)))

    stmt = select(EmailLog).where(EmailLog.created_at >= since)
    if f_status in EmailStatus.__members__:
        stmt = stmt.where(EmailLog.status == EmailStatus(f_status))

    total = (await db.execute(
        select(func.count()).select_from(stmt.subquery())
    )).scalar() or 0
    sent_count = (await db.execute(
        select(func.count(EmailLog.id)).where(
            EmailLog.created_at >= since, EmailLog.status == EmailStatus.SENT
        )
    )).scalar() or 0
    failed_count = (await db.execute(
        select(func.count(EmailLog.id)).where(
            EmailLog.created_at >= since, EmailLog.status == EmailStatus.FAILED
        )
    )).scalar() or 0

    stmt = stmt.order_by(desc(EmailLog.created_at)).offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()

    return templates.TemplateResponse(request, "pages/admin/comm.html", {
        "current_user": user,
        "current_member": member,
        "rows": rows,
        "total": total,
        "sent_count": sent_count,
        "failed_count": failed_count,
        "days": days,
        "f_status": f_status,
        "page": page,
        "page_size": page_size,
        "active_tab": "comm",
    })


@router.get("/sessions", response_class=HTMLResponse)
async def admin_sessions(
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Sessions actives — qui est connecté et depuis quand."""
    user, member = ctx
    from app.models.system import UserSession
    from app.models.identity import User as UserModel
    now = datetime.utcnow()
    # Nettoie les sessions expirées
    await db.execute(
        sa_delete(UserSession).where(
            UserSession.expires_at.isnot(None),
            UserSession.expires_at < now,
        )
    )
    await db.commit()

    sessions_r = await db.execute(
        select(UserSession).order_by(desc(UserSession.last_seen_at))
    )
    sessions = sessions_r.scalars().all()
    # Cache users
    uids = {s.user_id for s in sessions}
    ucache: dict[int, UserModel] = {}
    mcache2: dict[int, Member] = {}
    if uids:
        for u in (await db.execute(select(UserModel).where(UserModel.id.in_(uids)))).scalars().all():
            ucache[u.id] = u
        mids = {u.member_id for u in ucache.values()}
        for m in (await db.execute(select(Member).where(Member.id.in_(mids)))).scalars().all():
            mcache2[m.id] = m

    return templates.TemplateResponse(request, "pages/admin/sessions.html", {
        "current_user": user, "current_member": member,
        "sessions": sessions, "ucache": ucache, "mcache": mcache2,
        "now": now, "active_tab": "users",
    })


@router.post("/sessions/{session_id}/revoke")
async def admin_session_revoke(
    session_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Révoque une session (force la déconnexion de l'utilisateur)."""
    actor_user, actor_member = ctx
    from app.models.system import UserSession
    s = await db.get(UserSession, session_id)
    if s:
        await db.delete(s)
        await log_audit(
            db, actor_id=actor_member.id, action="SESSION_REVOKE",
            target_type="user", target_id=s.user_id,
            target_label=f"JTI {s.jti[:8]}...", request=request,
        )
        await db.commit()
    return RedirectResponse(url="/admin/sessions", status_code=303)


@router.post("/sessions/revoke-all/{user_id}")
async def admin_session_revoke_all(
    user_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Révoque toutes les sessions d'un utilisateur."""
    from app.models.system import UserSession
    actor_user, actor_member = ctx
    await db.execute(sa_delete(UserSession).where(UserSession.user_id == user_id))
    await log_audit(
        db, actor_id=actor_member.id, action="SESSION_REVOKE_ALL",
        target_type="user", target_id=user_id, request=request,
    )
    await db.commit()
    return RedirectResponse(url="/admin/sessions", status_code=303)


@router.get("/data/backup/{filename}/inspect", response_class=HTMLResponse)
async def admin_backup_inspect(
    filename: str,
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
):
    """Inspecte un backup ZIP."""
    import zipfile, os
    user, member = ctx
    if "/" in filename or ".." in filename or not filename.endswith(".zip"):
        raise HTTPException(400)
    path = os.path.join(os.getcwd(), "backups", filename)
    if not os.path.isfile(path):
        raise HTTPException(404)
    try:
        with zipfile.ZipFile(path) as zf:
            infos = [{"name": i.filename, "size": i.file_size, "compressed": i.compress_size}
                     for i in zf.infolist()]
        total = sum(i["size"] for i in infos)
    except Exception as e:
        raise HTTPException(500, f"ZIP invalide : {e}")
    return templates.TemplateResponse(request, "pages/admin/backup_inspect.html", {
        "current_user": user, "current_member": member,
        "filename": filename, "infos": infos,
        "total_uncompressed": total, "active_tab": "data",
    })


@router.post("/data/backup/{filename}/restore")
async def admin_backup_restore(
    filename: str,
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    confirm: str = Form(""),
):
    """Restaure la DB depuis un backup — DESTRUCTIF, double confirmation requise."""
    import zipfile, os, shutil
    from datetime import datetime as _dt
    user, member = ctx
    if confirm != "RESTORE":
        raise HTTPException(400, "Confirmation requise (confirm=RESTORE)")
    if "/" in filename or ".." in filename or not filename.endswith(".zip"):
        raise HTTPException(400)
    path = os.path.join(os.getcwd(), "backups", filename)
    if not os.path.isfile(path):
        raise HTTPException(404)
    with zipfile.ZipFile(path) as zf:
        db_files = [n for n in zf.namelist() if n.endswith(".db")]
        if not db_files:
            raise HTTPException(400, "Aucun fichier .db dans ce backup")
    # Sauvegarde auto avant restore
    try:
        from app.services.backup import run_backup
        await run_backup(to_email=None)
    except Exception:
        pass
    from app.config import get_settings
    s = get_settings()
    db_path = s.database_url.replace("sqlite+aiosqlite:///./", "").replace("sqlite:///./", "")
    db_abs = os.path.abspath(db_path)
    stamp = _dt.utcnow().strftime("%Y%m%d_%H%M%S")
    bak_path = db_abs + f".before_restore_{stamp}"
    shutil.copy2(db_abs, bak_path)
    with zipfile.ZipFile(path) as zf:
        data = zf.read(db_files[0])
    with open(db_abs, "wb") as f:
        f.write(data)
    await log_audit(db, actor_id=member.id, action="BACKUP_RESTORE",
                    target_label=filename,
                    details=f"Copie de sécurité : {os.path.basename(bak_path)}",
                    request=request, commit=True)
    return templates.TemplateResponse(request, "pages/admin/restore_success.html", {
        "current_user": user, "current_member": member,
        "filename": filename, "backup_copy": bak_path, "active_tab": "data",
    })


@router.get("/permissions", response_class=HTMLResponse)
async def admin_permissions(
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Gestion des permissions fines par module."""
    user, member = ctx
    from app.services.permissions import ALL_PERMISSIONS
    from app.models.system import ModulePermission
    from app.models.identity import User as UserModel

    # Tous les utilisateurs actifs (non admin)
    users_r = await db.execute(
        select(UserModel, Member)
        .join(Member, Member.id == UserModel.member_id)
        .where(UserModel.is_active == True)  # noqa: E712
        .order_by(Member.last_name, Member.first_name)
    )
    user_rows = list(users_r.all())

    # Permissions actuelles — liste pour compatibilité Jinja2 (set() non disponible)
    perms_r = await db.execute(select(ModulePermission))
    perms_by_user: dict[int, list] = {}
    for p in perms_r.scalars().all():
        if p.user_id not in perms_by_user:
            perms_by_user[p.user_id] = []
        if p.permission not in perms_by_user[p.user_id]:
            perms_by_user[p.user_id].append(p.permission)

    return templates.TemplateResponse(request, "pages/admin/permissions.html", {
        "current_user": user, "current_member": member,
        "user_rows": user_rows,
        "all_permissions": ALL_PERMISSIONS,
        "perms_by_user": perms_by_user,
        "active_tab": "users",
    })


@router.post("/permissions/{user_id}/grant")
async def admin_permission_grant(
    user_id: int, request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    permission: str = Form(...),
):
    actor_user, actor_member = ctx
    from app.services.permissions import ALL_PERMISSIONS, grant_permission
    if permission not in ALL_PERMISSIONS:
        raise HTTPException(400, "Permission inconnue")
    await grant_permission(db, user_id, permission, granted_by_id=actor_member.id)
    await log_audit(db, actor_id=actor_member.id, action="PERM_GRANT",
                    target_type="user", target_id=user_id, target_label=permission,
                    request=request, commit=True)
    return RedirectResponse(url="/admin/permissions", status_code=303)


@router.post("/permissions/{user_id}/revoke/{permission}")
async def admin_permission_revoke(
    user_id: int, permission: str,
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    actor_user, actor_member = ctx
    from app.services.permissions import revoke_permission
    await revoke_permission(db, user_id, permission)
    await log_audit(db, actor_id=actor_member.id, action="PERM_REVOKE",
                    target_type="user", target_id=user_id, target_label=permission,
                    request=request, commit=True)
    return RedirectResponse(url="/admin/permissions", status_code=303)


@router.get("/email-templates", response_class=HTMLResponse)
async def admin_email_templates(
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    from app.services.email_templates import TEMPLATE_KEYS, get_template
    templates_data = []
    for key in TEMPLATE_KEYS:
        templates_data.append(await get_template(key, db=db))
    return templates.TemplateResponse(request, "pages/admin/email_templates.html", {
        "current_user": user, "current_member": member,
        "templates_data": templates_data, "active_tab": "comm",
    })


@router.post("/email-templates/{key}/save")
async def admin_email_template_save(
    key: str,
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    subject: str = Form(""),
    body_html: str = Form(""),
    body_text: str = Form(""),
):
    actor_user, actor_member = ctx
    from app.services.email_templates import TEMPLATE_KEYS, save_template
    if key not in TEMPLATE_KEYS:
        raise HTTPException(400, "Template inconnu")
    await save_template(db, key, subject, body_html, body_text,
                        actor_id=actor_member.id)
    await log_audit(
        db, actor_id=actor_member.id, action="EMAIL_TEMPLATE_SAVE",
        target_label=key, details=f"sujet={subject[:60]}", request=request, commit=True,
    )
    return RedirectResponse(url=f"/admin/email-templates?_msg=saved&key={key}", status_code=303)


@router.post("/email-templates/{key}/reset")
async def admin_email_template_reset(
    key: str,
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Remet le template à son état par défaut (supprime les overrides)."""
    actor_user, actor_member = ctx
    from app.services.settings_store import set_setting
    await set_setting(db, key, None, actor_id=actor_member.id)
    await log_audit(
        db, actor_id=actor_member.id, action="EMAIL_TEMPLATE_RESET",
        target_label=key, request=request, commit=True,
    )
    return RedirectResponse(url=f"/admin/email-templates?_msg=reset&key={key}", status_code=303)


@router.get("/confidentiality", response_class=HTMLResponse)
async def admin_confidentiality(
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Réglages confidentialité — tous opt-in, désactivables en 1 clic."""
    user, member = ctx
    from app.services.confidentiality import get_config
    from app.models.documents import DocFolder, DocSpace

    cfg = await get_config(db=db)
    # Liste des dossiers GED pour le sélecteur whitelist
    fr = await db.execute(
        select(DocFolder, DocSpace.name)
        .join(DocSpace, DocSpace.id == DocFolder.space_id, isouter=True)
        .order_by(DocSpace.name, DocFolder.name)
    )
    folders = []
    for folder, space_name in fr.all():
        folders.append({
            "id": folder.id,
            "label": f"{space_name or '?'} / {folder.name}",
        })

    return templates.TemplateResponse(request, "pages/admin/confidentiality.html", {
        "current_user": user, "current_member": member,
        "cfg": cfg, "folders": folders,
        "active_tab": "data",
    })


@router.post("/confidentiality")
async def admin_confidentiality_save(
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    pj_whitelist_enabled: Annotated[str, Form()] = "",
    pj_allowed_folder_ids: Annotated[list[str], Form()] = None,
    audit_sensitive_views: Annotated[str, Form()] = "",
    show_confidentiality_banner: Annotated[str, Form()] = "",
):
    actor_user, actor_member = ctx
    from app.services.confidentiality import save_config

    pj_on = pj_whitelist_enabled in ("1", "on", "true")
    folder_ids = [int(f) for f in (pj_allowed_folder_ids or []) if f.isdigit()]
    audit_on = audit_sensitive_views in ("1", "on", "true")
    banner_on = show_confidentiality_banner in ("1", "on", "true")

    await save_config(
        db,
        pj_whitelist_enabled=pj_on,
        pj_allowed_folder_ids=folder_ids,
        audit_sensitive_views=audit_on,
        show_confidentiality_banner=banner_on,
        actor_id=actor_member.id,
    )

    await log_audit(
        db, actor_id=actor_member.id,
        action="CONFIDENTIALITY_UPDATE",
        details=(
            f"pj_whitelist={pj_on} ({len(folder_ids)} dossiers) · "
            f"audit_views={audit_on} · banner={banner_on}"
        ),
        request=request, commit=True,
    )
    return RedirectResponse(url="/admin/confidentiality?_msg=saved", status_code=303)


@router.get("/banner", response_class=HTMLResponse)
async def admin_banner(
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Configuration de la bannière de maintenance globale."""
    user, member = ctx
    from app.services.settings_store import get_setting
    banner = await get_setting("maintenance_banner", db=db) or {}
    maintenance_mode = bool(await get_setting("maintenance_mode", db=db))
    maintenance_message = await get_setting("maintenance_message", db=db) or ""
    return templates.TemplateResponse(request, "pages/admin/banner.html", {
        "current_user": user,
        "current_member": member,
        "banner": banner,
        "maintenance_mode": maintenance_mode,
        "maintenance_message": maintenance_message,
        "active_tab": "overview",
    })


@router.get("/invitations", response_class=HTMLResponse)
async def admin_invitations(
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Accès membres — envoie par email (compte + lien + guide utilisateur en PJ)
    à tous les membres actifs, et permet de vérifier l'état des envois."""
    from app.services.member_access import last_send_status
    user, member = ctx
    all_active = (await db.execute(
        select(Member).where(Member.status == MemberStatus.ACTIVE)
        .order_by(Member.last_name, Member.first_name)
    )).scalars().all()

    accounts = {
        u.member_id: u for u in (await db.execute(
            select(User).where(User.member_id.in_([m.id for m in all_active]))
        )).scalars().all()
    } if all_active else {}

    emails = [m.email for m in all_active if m.email]
    send_status = await last_send_status(db, emails)

    rows = [{
        "member": m,
        "has_account": m.id in accounts,
        "never_logged": (m.id not in accounts) or accounts[m.id].last_login_at is None,
        "last_log": send_status.get(m.email),
    } for m in all_active]

    return templates.TemplateResponse(request, "pages/admin/invitations.html", {
        "current_user": user,
        "current_member": member,
        "rows": rows,
        "active_tab": "users",
    })


@router.post("/invitations/{member_id}/send")
async def admin_invitation_send(
    member_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Envoie (ou renvoie) l'accès à ce membre : compte + lien + guide en PJ."""
    from app.services.member_access import send_access_email, build_guide_pdf
    from app.config import get_settings
    actor_user, actor_member = ctx
    m = (await db.execute(select(Member).where(Member.id == member_id))).scalar_one_or_none()
    if not m:
        raise HTTPException(404)
    if not m.email:
        return RedirectResponse(url="/admin/invitations?_msg=no_email", status_code=303)

    settings = get_settings()
    base_url = str(request.base_url).rstrip("/")
    guide_pdf = build_guide_pdf(settings.lodge_name, base_url)
    ok, err = await send_access_email(db, m, base_url, guide_pdf)

    await log_audit(
        db, actor_id=actor_member.id, action="ACCESS_SEND",
        target_type="member", target_id=m.id,
        target_label=f"{m.last_name} {m.first_name}",
        details=("OK" if ok else f"FAIL: {err}"), request=request, commit=True,
    )
    return RedirectResponse(
        url=f"/admin/invitations?_msg={'sent' if ok else 'fail'}", status_code=303
    )


@router.post("/invitations/send-bulk")
async def admin_invitation_send_bulk(
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    member_ids: Annotated[list[int], Form()] = [],
):
    """Déclenche l'envoi des accès aux membres sélectionnés, en tâche de fond."""
    from app.services.member_access import launch_bulk_access_send
    actor_user, actor_member = ctx
    if not member_ids:
        return RedirectResponse(url="/admin/invitations?_msg=none_selected", status_code=303)
    await log_audit(
        db, actor_id=actor_member.id, action="ACCESS_SEND_ALL",
        details=f"{len(member_ids)} membre(s) sélectionné(s)", request=request, commit=True,
    )
    launch_bulk_access_send(member_ids=member_ids)
    return RedirectResponse(url="/admin/invitations?_msg=started", status_code=303)


@router.post("/banner")
async def admin_banner_save(
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    enabled: str = Form(""),
    message: str = Form(""),
    level: str = Form("info"),
):
    actor_user, actor_member = ctx
    from app.services.settings_store import set_setting
    is_on = enabled in ("1", "true", "on")
    if level not in ("info", "warning", "danger"):
        level = "info"
    payload = {
        "enabled": is_on,
        "message": (message or "").strip()[:500],
        "level": level,
    }
    await set_setting(db, "maintenance_banner", payload, actor_id=actor_member.id)
    await log_audit(
        db, actor_id=actor_member.id, action="BANNER_UPDATE",
        details=f"enabled={is_on} level={level} msg={payload['message'][:80]}",
        request=request, commit=True,
    )
    return RedirectResponse(url="/admin/banner", status_code=303)


@router.post("/maintenance")
async def admin_maintenance_toggle(
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    action: str = Form(...),
    message: str = Form(""),
):
    actor_user, actor_member = ctx
    from app.services.settings_store import set_setting
    enable = action == "enable"
    await set_setting(db, "maintenance_mode", enable, actor_id=actor_member.id)
    await set_setting(db, "maintenance_message", (message or "").strip()[:300], actor_id=actor_member.id)
    await log_audit(
        db, actor_id=actor_member.id, action="MAINTENANCE_MODE",
        details=f"mode={'ON' if enable else 'OFF'}",
        request=request, commit=True,
    )
    return RedirectResponse(url="/admin/banner", status_code=303)


@router.post("/comm/test-email")
async def admin_comm_test_email(
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    to: str = Form(...),
):
    """Envoie un email de test au destinataire indiqué."""
    actor_user, actor_member = ctx
    from app.services.email import _send_raw
    ok, err = await _send_raw(
        to=to.strip(),
        subject="[Portail Socrate] Test email",
        html="<p>Cet email est un <strong>test</strong> émis depuis la console d'administration.</p>",
        text="Cet email est un test émis depuis la console d'administration.",
    )
    await log_audit(
        db, actor_id=actor_member.id,
        action="EMAIL_TEST",
        target_label=to.strip(),
        details=("OK" if ok else f"FAIL: {err}"),
        request=request, commit=True,
    )
    return RedirectResponse(url=f"/admin/comm?_msg={'ok' if ok else 'fail'}", status_code=303)


@router.get("/config", response_class=HTMLResponse)
async def admin_config(
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Référentiel des nomenclatures (enums) + libellés personnalisables."""
    user, member = ctx
    # Charger les overrides existants
    from app.models.system import LabelOverride
    rows = (await db.execute(select(LabelOverride))).scalars().all()
    overrides: dict[str, dict[str, str]] = {}
    for r in rows:
        overrides.setdefault(r.enum_class, {})[r.enum_key] = r.label
    from app.models.identity import (
        MasonicGrade, LodgeFunction, MemberStatus, MembershipType,
        ResponsibilityType,
    )
    from app.models.groups import GroupType
    from app.models.meetings import (
        AttendanceStatus, VisitorStatus, MeetingType, MeetingGrade,
    )

    referentials = [
        ("Grades maçonniques", "ti-hierarchy", MasonicGrade, "Modification : code Python — refactor à venir vers table DB"),
        ("Fonctions de loge",  "ti-crown",     LodgeFunction, ""),
        ("Statuts de membre",  "ti-user-circle", MemberStatus, ""),
        ("Types d'affiliation","ti-id-badge",  MembershipType, ""),
        ("Responsabilités",    "ti-briefcase", ResponsibilityType, ""),
        ("Types de groupe",    "ti-users-group", GroupType, ""),
        ("Types de tenue",     "ti-calendar-event", MeetingType, ""),
        ("Grade des tenues",   "ti-stars",     MeetingGrade, ""),
        ("Statuts de présence","ti-check",     AttendanceStatus, ""),
        ("Statuts visiteurs",  "ti-friends",   VisitorStatus, ""),
    ]

    # Liste des groupes (donnée éditable)
    from app.models.groups import LodgeGroup
    groups = (await db.execute(
        select(LodgeGroup).order_by(LodgeGroup.name)
    )).scalars().all()

    return templates.TemplateResponse(request, "pages/admin/config.html", {
        "current_user": user,
        "current_member": member,
        "referentials": referentials,
        "groups": groups,
        "overrides": overrides,
        "active_tab": "config",
    })


@router.post("/config/labels")
async def admin_config_labels_save(
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Sauvegarde des libellés personnalisés. Les inputs ont des `name`
    de la forme `label[ClassName][KEY]`."""
    actor_user, actor_member = ctx
    form = await request.form()
    from app.services.labels import set_label

    changes = 0
    for k, v in form.multi_items():
        if not k.startswith("label[") or not k.endswith("]"):
            continue
        # k = "label[MasonicGrade][APPRENTI]"
        inner = k[len("label["):-1]  # "MasonicGrade][APPRENTI"
        parts = inner.split("][")
        if len(parts) != 2:
            continue
        cls_name, key_name = parts
        await set_label(db, cls_name, key_name, (v or "").strip() or None,
                        actor_id=actor_member.id)
        changes += 1

    await log_audit(
        db, actor_id=actor_member.id, action="LABELS_UPDATE",
        details=f"{changes} libellé(s) sauvegardés",
        request=request, commit=True,
    )
    return RedirectResponse(url="/admin/config?_msg=saved", status_code=303)


@router.post("/audit/purge")
async def admin_audit_purge(
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    older_than_days: int = Form(365),
):
    """Purge des entrées plus vieilles que N jours (défaut 1 an)."""
    actor_user, actor_member = ctx
    cutoff = datetime.utcnow() - timedelta(days=max(30, older_than_days))
    r = await db.execute(sa_delete(AuditLog).where(AuditLog.created_at < cutoff))
    await log_audit(
        db, actor_id=actor_member.id,
        action="AUDIT_PURGE",
        target_label=f"avant {cutoff.date()}",
        details=f"{r.rowcount} entrées supprimées",
        request=request,
    )
    await db.commit()
    return RedirectResponse(url="/admin/audit", status_code=303)


# ── Analytics interne (pages vues, provenance, appareil, durée) ────────────

@router.get("/analytics", response_class=HTMLResponse)
async def admin_analytics(
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    days: int = 30,
):
    """Analytique interne (équivalent maison, léger, sans JS ni cookie
    tiers) : pages les plus vues, provenance, appareil, durée de session
    estimée. Alimenté par le middleware analytics_middleware (app/main.py)."""
    from app.models.analytics import PageView

    days = days if days in (7, 30, 90) else 30
    since = datetime.utcnow() - timedelta(days=days)

    # Exclure les visites des administrateurs
    from app.models.identity import User as _User
    admin_ids_r = await db.execute(
        select(_User.member_id).where(_User.is_admin == True, _User.member_id.isnot(None))
    )
    admin_member_ids = [r[0] for r in admin_ids_r.all()]

    base_filter = PageView.created_at >= since
    if admin_member_ids:
        base_filter = base_filter & PageView.member_id.not_in(admin_member_ids)

    total_views = (await db.execute(
        select(func.count(PageView.id)).where(base_filter)
    )).scalar() or 0

    unique_sessions = (await db.execute(
        select(func.count(func.distinct(PageView.session_id))).where(base_filter, PageView.session_id.isnot(None))
    )).scalar() or 0

    unique_members = (await db.execute(
        select(func.count(func.distinct(PageView.member_id))).where(base_filter, PageView.member_id.isnot(None))
    )).scalar() or 0

    # ── Pages les plus vues (chemin exact) ──────────────────────────────────
    top_pages_r = await db.execute(
        select(PageView.path, func.count(PageView.id).label("n"))
        .where(base_filter)
        .group_by(PageView.path)
        .order_by(desc("n"))
        .limit(15)
    )
    top_pages = [{"path": p, "count": n} for p, n in top_pages_r.all()]
    max_page_count = max((r["count"] for r in top_pages), default=1)

    # ── Sections (premier segment du chemin) ────────────────────────────────
    rows_by_path = (await db.execute(
        select(PageView.path, func.count(PageView.id)).where(base_filter).group_by(PageView.path)
    )).all()
    section_counts: dict[str, int] = {}
    for path, n in rows_by_path:
        seg = path.strip("/").split("/")[0] if path.strip("/") else "accueil"
        section_counts[seg] = section_counts.get(seg, 0) + n
    top_sections = sorted(
        [{"section": s, "count": c} for s, c in section_counts.items()],
        key=lambda r: r["count"], reverse=True,
    )[:10]
    max_section_count = max((r["count"] for r in top_sections), default=1)

    # ── Appareils ────────────────────────────────────────────────────────────
    device_r = await db.execute(
        select(PageView.device, func.count(PageView.id)).where(base_filter).group_by(PageView.device)
    )
    device_rows = device_r.all()
    device_total = sum(n for _, n in device_rows) or 1
    devices = sorted(
        [{"device": d, "count": n, "pct": round(n * 100 / device_total)} for d, n in device_rows],
        key=lambda r: r["count"], reverse=True,
    )

    # ── Provenance ───────────────────────────────────────────────────────────
    ref_r = await db.execute(
        select(PageView.referrer_host, func.count(PageView.id)).where(base_filter).group_by(PageView.referrer_host)
    )
    ref_rows = ref_r.all()
    ref_total = sum(n for _, n in ref_rows) or 1
    referrers = sorted(
        [{"host": h, "count": n, "pct": round(n * 100 / ref_total)} for h, n in ref_rows],
        key=lambda r: r["count"], reverse=True,
    )[:10]

    # ── Durée de session estimée (écart 1ère/dernière vue par session) ───────
    session_r = await db.execute(
        select(
            PageView.session_id,
            func.min(PageView.created_at), func.max(PageView.created_at), func.count(PageView.id),
        )
        .where(base_filter, PageView.session_id.isnot(None))
        .group_by(PageView.session_id)
    )
    durations_sec: list[float] = []
    single_page_sessions = 0
    for _sid, first_ts, last_ts, n in session_r.all():
        if n <= 1:
            single_page_sessions += 1
            continue
        durations_sec.append((last_ts - first_ts).total_seconds())
    avg_duration_min = round(sum(durations_sec) / len(durations_sec) / 60, 1) if durations_sec else None
    multi_page_sessions = len(durations_sec)

    # ── Timeline (vues par jour) ──────────────────────────────────────────────
    # func.date() (fonction SQLite) plutôt que cast(..., Date) : un CAST SQL
    # standard sur une colonne DATETIME stockée en texte ne la retronque pas
    # en date, et le processeur Date de SQLAlchemy plante ensuite en tentant
    # de parser une valeur qui n'est pas une chaîne ISO simple.
    day_col = func.date(PageView.created_at)
    day_r = await db.execute(
        select(day_col, func.count(PageView.id))
        .where(base_filter)
        .group_by(day_col)
        .order_by(day_col)
    )
    daily = [{"day": d, "count": n} for d, n in day_r.all()]
    max_daily = max((r["count"] for r in daily), default=1)

    return templates.TemplateResponse(request, "pages/admin/analytics.html", {
        "current_user": ctx[0],
        "current_member": ctx[1],
        "active_tab": "analytics",
        "days": days,
        "total_views": total_views,
        "unique_sessions": unique_sessions,
        "unique_members": unique_members,
        "top_pages": top_pages,
        "max_page_count": max_page_count,
        "top_sections": top_sections,
        "max_section_count": max_section_count,
        "devices": devices,
        "referrers": referrers,
        "avg_duration_min": avg_duration_min,
        "multi_page_sessions": multi_page_sessions,
        "single_page_sessions": single_page_sessions,
        "daily": daily,
        "max_daily": max_daily,
    })
