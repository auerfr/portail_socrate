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
        select(func.count(Meeting.id)).where(Meeting.date >= today)
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
async def admin_data_stub(
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
):
    user, member = ctx
    return templates.TemplateResponse(request, "pages/admin/_stub.html", {
        "current_user": user, "current_member": member,
        "active_tab": "data",
        "title": "Données & sauvegardes",
        "icon": "ti-database",
        "items": [
            "Restore guidé depuis backup ZIP (sandbox avant écrasement)",
            "Maintenance DB : VACUUM, taille tables, purge anciens logs/notifs",
            "Export RGPD complet d'un membre (JSON + ZIP des fichiers)",
            "Droit à l'oubli : anonymisation cohérente",
        ],
    })


@router.get("/comm", response_class=HTMLResponse)
async def admin_comm_stub(
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
):
    user, member = ctx
    return templates.TemplateResponse(request, "pages/admin/_stub.html", {
        "current_user": user, "current_member": member,
        "active_tab": "comm",
        "title": "Communication & emails",
        "icon": "ti-mail",
        "items": [
            "File d'envoi SMTP : voir échecs, retry manuel, logs détaillés",
            "Templates emails personnalisables (sujet/corps de chaque notification)",
            "Test email depuis chaque template",
            "Tracking : ouvertures (pixel), clics liens",
        ],
    })


@router.get("/config", response_class=HTMLResponse)
async def admin_config_stub(
    request: Request,
    ctx: Annotated[tuple, Depends(require_admin)],
):
    user, member = ctx
    return templates.TemplateResponse(request, "pages/admin/_stub.html", {
        "current_user": user, "current_member": member,
        "active_tab": "config",
        "title": "Configuration métier",
        "icon": "ti-settings",
        "items": [
            "Grades / fonctions : ajouter / renommer dynamiquement",
            "Catégories news / tags éditables",
            "Modèles de PV de tenue (templates multiples)",
            "Calendrier maçonnique : années, jours fériés rituels",
        ],
    })


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
