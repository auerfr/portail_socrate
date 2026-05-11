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
from fastapi.templating import Jinja2Templates
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
templates = Jinja2Templates(directory="app/templates")
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
    import io
    import json
    import zipfile
    from fastapi.responses import StreamingResponse

    actor_user, actor_member = ctx
    m = (await db.execute(select(Member).where(Member.id == member_id))).scalar_one_or_none()
    if not m:
        raise HTTPException(404)

    def _serialize(obj):
        out = {}
        for col in obj.__table__.columns:
            val = getattr(obj, col.name)
            if hasattr(val, "isoformat"):
                val = val.isoformat()
            elif hasattr(val, "value"):
                val = val.value
            out[col.name] = val
        return out

    data = {"member": _serialize(m)}

    # User associé
    u = (await db.execute(select(User).where(User.member_id == m.id))).scalar_one_or_none()
    if u:
        d = _serialize(u)
        d.pop("password_hash", None)  # ne pas exporter le hash
        d.pop("reset_token", None)
        data["user_account"] = d

    # Tenter de joindre quelques relations courantes
    from sqlalchemy import text as sa_text
    for label, sql in [
        ("attendances",   "SELECT * FROM attendances WHERE member_id = :id"),
        ("messages_sent", "SELECT id, subject, body, created_at FROM messages WHERE sender_id = :id"),
        ("news_authored", "SELECT id, title, created_at FROM news_articles WHERE author_id = :id"),
        ("poll_votes",    "SELECT * FROM poll_votes WHERE voter_id = :id"),
        ("tasks_assigned","SELECT id, title, status, due_date FROM tasks WHERE assigned_to_id = :id"),
        ("task_comments", "SELECT id, task_id, content, created_at FROM task_comments WHERE author_id = :id"),
        ("audit_actions", "SELECT id, action, resource_type, target_label, created_at FROM audit_logs WHERE actor_id = :id"),
    ]:
        try:
            r = await db.execute(sa_text(sql), {"id": m.id})
            rows = [dict(row._mapping) for row in r.fetchall()]
            for row in rows:
                for k, v in list(row.items()):
                    if hasattr(v, "isoformat"):
                        row[k] = v.isoformat()
            data[label] = rows
        except Exception as e:
            data[label] = {"_error": str(e)}

    # Construction du ZIP en mémoire
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "donnees.json",
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
        )
        zf.writestr(
            "README.txt",
            "Export RGPD - Portail Socrate\n"
            f"Membre : {m.last_name} {m.first_name} (id={m.id})\n"
            f"Date d'export : {datetime.utcnow().isoformat()}\n"
            f"Demandé par : {actor_member.first_name} {actor_member.last_name}\n\n"
            "Ce ZIP contient l'ensemble des données personnelles associées à ce membre\n"
            "dans la base de la loge, hors fichiers uploadés.\n",
        )
    buf.seek(0)

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
    return templates.TemplateResponse(request, "pages/admin/banner.html", {
        "current_user": user,
        "current_member": member,
        "banner": banner,
        "active_tab": "overview",
    })


@router.get("/invitations", response_class=HTMLResponse)
async def admin_invitations(
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Liste des membres SANS compte utilisateur — permet de leur créer un compte
    + lien de réinitialisation pour qu'ils définissent leur mot de passe."""
    user, member = ctx
    # Membres actifs sans compte User
    r = await db.execute(
        select(Member).outerjoin(User, User.member_id == Member.id)
        .where(User.id.is_(None), Member.status == MemberStatus.ACTIVE)
        .order_by(Member.last_name, Member.first_name)
    )
    members_no_account = r.scalars().all()
    return templates.TemplateResponse(request, "pages/admin/invitations.html", {
        "current_user": user,
        "current_member": member,
        "members_no_account": members_no_account,
        "active_tab": "users",
    })


@router.post("/invitations/{member_id}/create")
async def admin_invitation_create(
    member_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Crée un compte User pour ce membre + génère un lien de reset valide 7j."""
    import secrets
    actor_user, actor_member = ctx
    m = (await db.execute(select(Member).where(Member.id == member_id))).scalar_one_or_none()
    if not m:
        raise HTTPException(404)
    # Compte déjà existant ?
    existing = (await db.execute(select(User).where(User.member_id == m.id))).scalar_one_or_none()
    if existing:
        return RedirectResponse(url="/admin/invitations?_msg=exists", status_code=303)
    if not m.email:
        return RedirectResponse(url="/admin/invitations?_msg=no_email", status_code=303)

    # Génère un login depuis prenom.nom (à défaut : email)
    base_login = f"{m.first_name}.{m.last_name}".lower().replace(" ", "").replace("'", "")
    import unicodedata
    base_login = "".join(
        c for c in unicodedata.normalize("NFKD", base_login)
        if not unicodedata.combining(c)
    )
    # Garantit unicité
    login = base_login
    n = 2
    while (await db.execute(select(User).where(User.login == login))).scalar_one_or_none():
        login = f"{base_login}{n}"
        n += 1

    # Hash d'un mot de passe placeholder (jamais utilisé puisqu'on force reset)
    try:
        from passlib.context import CryptContext
        pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
        placeholder_hash = pwd_ctx.hash(secrets.token_urlsafe(32))
    except Exception:
        # Fallback brut si passlib indispo
        import hashlib
        placeholder_hash = "x" + hashlib.sha256(secrets.token_bytes(32)).hexdigest()

    token = secrets.token_urlsafe(32)
    u = User(
        member_id=m.id,
        login=login,
        password_hash=placeholder_hash,
        is_active=True,
        is_admin=False,
        reset_token=token,
        reset_token_expires=datetime.utcnow() + timedelta(days=7),
    )
    db.add(u)
    await log_audit(
        db, actor_id=actor_member.id, action="INVITATION_CREATE",
        target_type="member", target_id=m.id,
        target_label=f"{m.last_name} {m.first_name}",
        details=f"login={login} token 7j", request=request,
    )
    await db.commit()
    await db.refresh(u)
    return RedirectResponse(
        url=f"/admin/invitations?invited_id={u.id}&invited_token={token}",
        status_code=303,
    )


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
