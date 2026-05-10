"""Router Projets & Tâches — vues Liste / Kanban / Gantt + commentaires, templates, dashboard."""
import io
from datetime import datetime, date, timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, desc, delete, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_auth, can_manage_members
from app.models.identity import Member, MemberStatus
from app.models.groups import LodgeGroup
from app.models.projects import (
    Project, ProjectType, ProjectStatus,
    Task, TaskStatus, TaskPriority,
    TaskComment, TaskDependency, ProjectActivity,
    ProjectTemplate, ProjectTemplateTask,
)
from app.models.forum import ForumSubject

router = APIRouter(prefix="/projects", tags=["projects"])
templates = Jinja2Templates(directory="app/templates")


def _is_manager(user, member) -> bool:
    return bool(getattr(user, "is_admin", False) or can_manage_members(member))


def _parse_date(s: str) -> Optional[date]:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  LISTE des projets
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def projects_index(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    r = await db.execute(
        select(Project).order_by(
            Project.status.asc(), desc(Project.created_at),
        )
    )
    projects = r.scalars().all()

    # Stats par projet — une seule requête grâce à GROUP BY
    sr = await db.execute(
        select(
            Task.project_id,
            func.count(Task.id),
            func.sum(func.iif(Task.status == TaskStatus.DONE, 1, 0)),
        ).group_by(Task.project_id)
    )
    stats: dict[int, dict] = {}
    for pid, total, done in sr.all():
        total = total or 0
        done = done or 0
        stats[pid] = {"total": total, "done": done, "pct": int((done * 100) / total) if total else 0}
    for p in projects:
        stats.setdefault(p.id, {"total": 0, "done": 0, "pct": 0})

    # Templates disponibles
    tpl = (await db.execute(select(ProjectTemplate).order_by(ProjectTemplate.name))).scalars().all()

    return templates.TemplateResponse(request, "pages/projects/index.html", {
        "current_user": user,
        "current_member": member,
        "projects": projects,
        "stats": stats,
        "templates_list": tpl,
        "can_manage": _is_manager(user, member),
    })


# ─────────────────────────────────────────────────────────────────────────────
#  DASHBOARD global — vision tous projets
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    today = date.today()

    # Tâches en retard
    overdue = (await db.execute(
        select(Task, Project)
        .join(Project, Project.id == Task.project_id)
        .where(Task.due_date < today, Task.status != TaskStatus.DONE, Task.status != TaskStatus.CANCELLED)
        .order_by(Task.due_date.asc())
    )).all()

    # Tâches cette semaine
    week_end = today + timedelta(days=7)
    soon = (await db.execute(
        select(Task, Project)
        .join(Project, Project.id == Task.project_id)
        .where(Task.due_date >= today, Task.due_date <= week_end,
               Task.status != TaskStatus.DONE, Task.status != TaskStatus.CANCELLED)
        .order_by(Task.due_date.asc())
    )).all()

    # Charge par membre (tâches actives)
    load = (await db.execute(
        select(Task.assigned_to_id, func.count(Task.id))
        .where(Task.assigned_to_id.isnot(None), Task.status != TaskStatus.DONE, Task.status != TaskStatus.CANCELLED)
        .group_by(Task.assigned_to_id)
        .order_by(desc(func.count(Task.id)))
    )).all()

    member_ids = [mid for mid, _ in load]
    members_cache: dict[int, Member] = {}
    if member_ids:
        mr = await db.execute(select(Member).where(Member.id.in_(member_ids)))
        for m in mr.scalars().all():
            members_cache[m.id] = m

    # Stats globales
    total_tasks = (await db.execute(select(func.count(Task.id)))).scalar() or 0
    done_tasks = (await db.execute(select(func.count(Task.id)).where(Task.status == TaskStatus.DONE))).scalar() or 0
    active_projects = (await db.execute(
        select(func.count(Project.id)).where(Project.status == ProjectStatus.ACTIVE)
    )).scalar() or 0

    return templates.TemplateResponse(request, "pages/projects/dashboard.html", {
        "current_user": user,
        "current_member": member,
        "today": today,
        "overdue": overdue,
        "soon": soon,
        "load": load,
        "members_cache": members_cache,
        "total_tasks": total_tasks,
        "done_tasks": done_tasks,
        "active_projects": active_projects,
    })


# ─────────────────────────────────────────────────────────────────────────────
#  Création projet
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/new")
async def project_create(
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    name: str = Form(...),
    description: str = Form(""),
    color: str = Form("#4a9d8f"),
    type: str = Form("PROJECT"),
    start_date: str = Form(""),
    end_date: str = Form(""),
):
    user, member = ctx
    if not _is_manager(user, member):
        raise HTTPException(403)

    p = Project(
        name=name.strip(),
        description=description.strip() or None,
        color=color.strip() or "#4a9d8f",
        type=ProjectType(type) if type in ProjectType.__members__ else ProjectType.PROJECT,
        status=ProjectStatus.ACTIVE,
        start_date=_parse_date(start_date),
        end_date=_parse_date(end_date),
        owner_id=member.id,
        created_by_id=member.id,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return RedirectResponse(url=f"/projects/{p.id}", status_code=303)


@router.post("/{project_id}/edit")
async def project_edit(
    project_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    name: str = Form(...),
    description: str = Form(""),
    color: str = Form("#4a9d8f"),
    status: str = Form("ACTIVE"),
    start_date: str = Form(""),
    end_date: str = Form(""),
):
    user, member = ctx
    p = (await db.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(404)
    if not (_is_manager(user, member) or p.owner_id == member.id or p.created_by_id == member.id):
        raise HTTPException(403)
    p.name = name.strip()
    p.description = description.strip() or None
    p.color = color.strip() or p.color
    if status in ProjectStatus.__members__:
        p.status = ProjectStatus(status)
    p.start_date = _parse_date(start_date)
    p.end_date = _parse_date(end_date)
    await db.commit()
    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)


@router.post("/{project_id}/delete")
async def project_delete(
    project_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    p = (await db.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(404)
    if not (_is_manager(user, member) or p.owner_id == member.id or p.created_by_id == member.id):
        raise HTTPException(403)
    # Suppression manuelle des tâches puis du projet
    await db.execute(delete(Task).where(Task.project_id == project_id))
    await db.execute(delete(Project).where(Project.id == project_id))
    await db.commit()
    return RedirectResponse(url="/projects/", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
#  Modèles de projets — LISTE (déclaré avant /{project_id} pour éviter le shadowing)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/templates", response_class=HTMLResponse)
async def templates_list(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    tpls = (await db.execute(
        select(ProjectTemplate).options(selectinload(ProjectTemplate.tasks))
        .order_by(ProjectTemplate.name)
    )).scalars().all()
    return templates.TemplateResponse(request, "pages/projects/templates.html", {
        "current_user": user,
        "current_member": member,
        "templates_list": tpls,
        "can_manage": _is_manager(user, member),
    })


# ─────────────────────────────────────────────────────────────────────────────
#  DÉTAIL projet — Liste / Kanban / Gantt
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{project_id}", response_class=HTMLResponse)
async def project_detail(
    project_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    view: str = "kanban",
    zoom: str = "week",
    gantt_from: str = "",
    gantt_to: str = "",
    q: str = "",
    f_status: str = "",
    f_priority: str = "",
    f_assignee: str = "",
):
    user, member = ctx
    p = (await db.execute(
        select(Project).options(selectinload(Project.tasks))
        .where(Project.id == project_id)
    )).scalar_one_or_none()
    if not p:
        raise HTTPException(404)

    # Filtres — on n'affiche dans la vue principale que les tâches racines (pas les sous-tâches)
    stmt = select(Task).where(
        Task.project_id == project_id,
        Task.parent_task_id.is_(None),
    ).order_by(Task.order_position, Task.created_at)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Task.title.ilike(like), Task.description.ilike(like)))
    if f_status and f_status in TaskStatus.__members__:
        stmt = stmt.where(Task.status == TaskStatus(f_status))
    if f_priority and f_priority in TaskPriority.__members__:
        stmt = stmt.where(Task.priority == TaskPriority(f_priority))
    if f_assignee.startswith("m:") and f_assignee[2:].isdigit():
        stmt = stmt.where(Task.assigned_to_id == int(f_assignee[2:]))
    elif f_assignee.startswith("g:") and f_assignee[2:].isdigit():
        stmt = stmt.where(Task.assigned_to_group_id == int(f_assignee[2:]))
    elif f_assignee == "none":
        stmt = stmt.where(Task.assigned_to_id.is_(None), Task.assigned_to_group_id.is_(None))

    tr = await db.execute(stmt)
    tasks = tr.scalars().all()

    # Commentaires + sujets forum liés
    task_ids = [t.id for t in tasks]
    comments_by_task: dict[int, list] = {}
    if task_ids:
        cr = await db.execute(
            select(TaskComment).where(TaskComment.task_id.in_(task_ids))
            .order_by(TaskComment.created_at.asc())
        )
        for c in cr.scalars().all():
            comments_by_task.setdefault(c.task_id, []).append(c)

    forum_ids = {t.forum_subject_id for t in tasks if t.forum_subject_id}
    forum_cache: dict[int, ForumSubject] = {}
    if forum_ids:
        fs = await db.execute(select(ForumSubject).where(ForumSubject.id.in_(forum_ids)))
        for s in fs.scalars().all():
            forum_cache[s.id] = s

    # Auteurs de commentaires
    comment_author_ids = {c.author_id for cs in comments_by_task.values() for c in cs if c.author_id}

    # Sous-tâches et dépendances
    subtasks_by_parent: dict[int, list[Task]] = {}
    deps_by_successor: dict[int, list[int]] = {}    # successor_id -> [predecessor_ids]
    deps_by_predecessor: dict[int, list[int]] = {}  # predecessor_id -> [successor_ids]
    if task_ids:
        # Sous-tâches (parent ∈ tasks de ce projet)
        sr = await db.execute(
            select(Task).where(Task.parent_task_id.in_(task_ids))
            .order_by(Task.order_position, Task.created_at)
        )
        for st in sr.scalars().all():
            subtasks_by_parent.setdefault(st.parent_task_id, []).append(st)
        # Dépendances
        dr = await db.execute(
            select(TaskDependency).where(
                or_(TaskDependency.successor_id.in_(task_ids),
                    TaskDependency.predecessor_id.in_(task_ids))
            )
        )
        for d in dr.scalars().all():
            deps_by_successor.setdefault(d.successor_id, []).append(d.predecessor_id)
            deps_by_predecessor.setdefault(d.predecessor_id, []).append(d.successor_id)

    # Cache des tâches pour afficher les noms des dépendances
    dep_task_ids = set()
    for ids in deps_by_successor.values(): dep_task_ids.update(ids)
    for ids in deps_by_predecessor.values(): dep_task_ids.update(ids)
    tasks_cache: dict[int, Task] = {t.id: t for t in tasks}
    missing = dep_task_ids - set(tasks_cache.keys())
    if missing:
        mr = await db.execute(select(Task).where(Task.id.in_(missing)))
        for t in mr.scalars().all():
            tasks_cache[t.id] = t

    # Activité récente
    activity = (await db.execute(
        select(ProjectActivity).where(ProjectActivity.project_id == project_id)
        .order_by(desc(ProjectActivity.created_at)).limit(20)
    )).scalars().all()
    activity_actor_ids = {a.actor_id for a in activity if a.actor_id}

    # Membres assignés / groupes (cache)
    member_ids = {t.assigned_to_id for t in tasks if t.assigned_to_id} | comment_author_ids | activity_actor_ids
    group_ids = {t.assigned_to_group_id for t in tasks if t.assigned_to_group_id}

    members_cache: dict[int, Member] = {}
    if member_ids:
        mr = await db.execute(select(Member).where(Member.id.in_(member_ids)))
        for m in mr.scalars().all():
            members_cache[m.id] = m

    groups_cache: dict[int, LodgeGroup] = {}
    if group_ids:
        gr = await db.execute(select(LodgeGroup).where(LodgeGroup.id.in_(group_ids)))
        for g in gr.scalars().all():
            groups_cache[g.id] = g

    # Listes pour les sélecteurs (création/édition)
    all_active = (await db.execute(
        select(Member).where(Member.status == MemberStatus.ACTIVE).order_by(Member.last_name, Member.first_name)
    )).scalars().all()
    all_groups = (await db.execute(
        select(LodgeGroup).order_by(LodgeGroup.name)
    )).scalars().all()

    # Données Gantt : période + lignes
    gantt = _compute_gantt(
        tasks, p,
        zoom=zoom if zoom in ("week", "month", "quarter") else "week",
        range_from=_parse_date(gantt_from),
        range_to=_parse_date(gantt_to),
    )

    # Buckets Kanban
    buckets = {s.value: [] for s in TaskStatus}
    for t in tasks:
        buckets[t.status.value if hasattr(t.status, "value") else t.status].append(t)

    # Sujets forum disponibles pour lier
    all_forum_subjects = (await db.execute(
        select(ForumSubject).order_by(desc(ForumSubject.created_at)).limit(200)
    )).scalars().all()

    return templates.TemplateResponse(request, "pages/projects/detail.html", {
        "current_user": user,
        "current_member": member,
        "project": p,
        "tasks": tasks,
        "members_cache": members_cache,
        "groups_cache": groups_cache,
        "all_active": all_active,
        "all_groups": all_groups,
        "all_forum_subjects": all_forum_subjects,
        "forum_cache": forum_cache,
        "comments_by_task": comments_by_task,
        "subtasks_by_parent": subtasks_by_parent,
        "deps_by_successor": deps_by_successor,
        "deps_by_predecessor": deps_by_predecessor,
        "tasks_cache": tasks_cache,
        "activity": activity,
        "view": view if view in ("list", "kanban", "gantt") else "kanban",
        "buckets": buckets,
        "gantt": gantt,
        "TaskStatus": TaskStatus,
        "TaskPriority": TaskPriority,
        "ProjectStatus": ProjectStatus,
        "today": date.today(),
        "filters": {"q": q, "f_status": f_status, "f_priority": f_priority, "f_assignee": f_assignee},
        "can_manage": _is_manager(user, member) or p.owner_id == member.id or p.created_by_id == member.id,
    })


# ─────────────────────────────────────────────────────────────────────────────
#  Helper : log d'activité
# ─────────────────────────────────────────────────────────────────────────────

async def _log(db: AsyncSession, project_id: int, actor_id: Optional[int],
               action: str, target: str = "", details: str = ""):
    db.add(ProjectActivity(
        project_id=project_id, actor_id=actor_id,
        action=action, target=target[:300] if target else None,
        details=details or None,
    ))


def _compute_gantt(
    tasks: list[Task],
    project: Project,
    zoom: str = "week",
    range_from: Optional[date] = None,
    range_to: Optional[date] = None,
):
    """Calcule la fenêtre + les barres + les marqueurs (semaines/mois/trimestres).

    Par défaut la timeline commence à la première tâche (pas au projet),
    sauf si `range_from`/`range_to` sont fournis.
    """
    # ── Fenêtre par défaut : basée sur les TÂCHES en priorité ──
    task_dates: list[date] = []
    for t in tasks:
        if t.start_date:
            task_dates.append(t.start_date)
        if t.due_date:
            task_dates.append(t.due_date)

    if range_from and range_to:
        first, last = range_from, range_to
    elif task_dates:
        first = min(task_dates)
        last = max(task_dates)
    elif project.start_date or project.end_date:
        first = project.start_date or date.today()
        last = project.end_date or (first + timedelta(days=60))
    else:
        first = date.today()
        last = first + timedelta(days=30)

    if first > last:
        first, last = last, first
    if (last - first).days < 14:
        last = first + timedelta(days=14)

    # Marge (sauf si dates explicitement choisies par l'utilisateur)
    if not (range_from and range_to):
        first = first - timedelta(days=2)
        last = last + timedelta(days=2)

    total_days = max(1, (last - first).days + 1)

    # ── Barres ──
    rows = []
    for t in tasks:
        if not t.start_date and not t.due_date:
            continue
        s = t.start_date or t.due_date
        e = t.due_date or t.start_date
        if s > e:
            s, e = e, s
        # Clamp à la fenêtre
        if e < first or s > last:
            continue
        cs = max(s, first)
        ce = min(e, last)
        offset = (cs - first).days
        length = max(1, (ce - cs).days + 1)
        rows.append({
            "task": t,
            "offset_pct": round((offset * 100) / total_days, 3),
            "length_pct": round((length * 100) / total_days, 3),
            "start": s,
            "end": e,
            "clipped_left": s < first,
            "clipped_right": e > last,
        })

    # ── Marqueurs selon zoom ──
    markers = []
    if zoom == "month":
        # 1er de chaque mois
        cur = date(first.year, first.month, 1)
        while cur <= last:
            if cur >= first:
                markers.append({
                    "date": cur,
                    "label": cur.strftime("%b %Y").capitalize(),
                    "offset_pct": round(((cur - first).days * 100) / total_days, 3),
                })
            # mois suivant
            if cur.month == 12:
                cur = date(cur.year + 1, 1, 1)
            else:
                cur = date(cur.year, cur.month + 1, 1)
    elif zoom == "quarter":
        # 1er trimestre contenant first
        q_start_month = ((first.month - 1) // 3) * 3 + 1
        cur = date(first.year, q_start_month, 1)
        while cur <= last:
            if cur >= first:
                q = (cur.month - 1) // 3 + 1
                markers.append({
                    "date": cur,
                    "label": f"T{q} {cur.year}",
                    "offset_pct": round(((cur - first).days * 100) / total_days, 3),
                })
            # +3 mois
            new_m = cur.month + 3
            new_y = cur.year + (new_m - 1) // 12
            new_m = ((new_m - 1) % 12) + 1
            cur = date(new_y, new_m, 1)
    else:
        # Semaines (par défaut) — lundi
        cur = first - timedelta(days=first.weekday())
        if cur < first:
            cur += timedelta(days=7)
        while cur <= last:
            markers.append({
                "date": cur,
                "label": cur.strftime("%d/%m"),
                "offset_pct": round(((cur - first).days * 100) / total_days, 3),
            })
            cur += timedelta(days=7)

    return {
        "first": first,
        "last": last,
        "total_days": total_days,
        "rows": rows,
        "markers": markers,
        "zoom": zoom,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Tâches : création / édition / status / suppression
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_assignee(form_value: str) -> tuple[Optional[int], Optional[int]]:
    """Convertit la valeur du sélecteur (ex 'm:42' ou 'g:7' ou '') en (member_id, group_id)."""
    v = (form_value or "").strip()
    if not v or v == "none":
        return None, None
    if v.startswith("m:") and v[2:].isdigit():
        return int(v[2:]), None
    if v.startswith("g:") and v[2:].isdigit():
        return None, int(v[2:])
    return None, None


@router.post("/{project_id}/tasks/new")
async def task_create(
    project_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    title: str = Form(...),
    description: str = Form(""),
    priority: str = Form("MEDIUM"),
    assignee: str = Form(""),
    start_date: str = Form(""),
    due_date: str = Form(""),
    status: str = Form("TODO"),
    forum_subject_id: str = Form(""),
    is_milestone: str = Form(""),
):
    user, member = ctx
    p = (await db.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(404)

    mid, gid = _resolve_assignee(assignee)
    fsid = int(forum_subject_id) if forum_subject_id and forum_subject_id.isdigit() else None

    t = Task(
        project_id=project_id,
        title=title.strip(),
        description=description.strip() or None,
        status=TaskStatus(status) if status in TaskStatus.__members__ else TaskStatus.TODO,
        priority=TaskPriority(priority) if priority in TaskPriority.__members__ else TaskPriority.MEDIUM,
        assigned_to_id=mid,
        assigned_to_group_id=gid,
        start_date=_parse_date(start_date),
        due_date=_parse_date(due_date),
        forum_subject_id=fsid,
        is_milestone=1 if is_milestone in ("1", "true", "on") else 0,
        created_by_id=member.id,
    )
    db.add(t)
    await _log(db, project_id, member.id, "CREATE_TASK", t.title)
    await db.commit()
    await db.refresh(t)

    # Push notification au membre assigné
    if mid and mid != member.id:
        try:
            from app.services.push import send_push_to_member
            await send_push_to_member(
                db, mid,
                f"📋 Nouvelle tâche : {t.title}",
                f"Projet : {p.name}",
                f"/projects/{project_id}",
            )
        except Exception:
            pass

    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)


@router.post("/tasks/{task_id}/edit")
async def task_edit(
    task_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    title: str = Form(...),
    description: str = Form(""),
    priority: str = Form("MEDIUM"),
    assignee: str = Form(""),
    start_date: str = Form(""),
    due_date: str = Form(""),
    status: str = Form("TODO"),
    progress: int = Form(0),
    forum_subject_id: str = Form(""),
    is_milestone: str = Form(""),
):
    user, member = ctx
    t = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404)

    old_assignee = t.assigned_to_id
    old_status = t.status
    mid, gid = _resolve_assignee(assignee)

    t.title = title.strip()
    t.description = description.strip() or None
    if priority in TaskPriority.__members__:
        t.priority = TaskPriority(priority)
    t.assigned_to_id = mid
    t.assigned_to_group_id = gid
    t.start_date = _parse_date(start_date)
    t.due_date = _parse_date(due_date)
    t.forum_subject_id = int(forum_subject_id) if forum_subject_id and forum_subject_id.isdigit() else None
    t.is_milestone = 1 if is_milestone in ("1", "true", "on") else 0
    if status in TaskStatus.__members__:
        new_status = TaskStatus(status)
        if new_status == TaskStatus.DONE and t.status != TaskStatus.DONE:
            t.completed_at = datetime.utcnow()
        if new_status != TaskStatus.DONE:
            t.completed_at = None
        t.status = new_status
    t.progress = max(0, min(100, int(progress or 0)))
    if old_status != t.status:
        await _log(db, t.project_id, member.id, "STATUS",
                   t.title, f"{old_status.value} → {t.status.value}")
    else:
        await _log(db, t.project_id, member.id, "EDIT_TASK", t.title)
    await db.commit()

    # Notification si nouveau assigné
    if mid and mid != old_assignee and mid != member.id:
        try:
            from app.services.push import send_push_to_member
            await send_push_to_member(
                db, mid,
                f"📋 Tâche assignée : {t.title}",
                "Vous êtes responsable de cette tâche.",
                f"/projects/{t.project_id}",
            )
        except Exception:
            pass

    return RedirectResponse(url=f"/projects/{t.project_id}", status_code=303)


@router.post("/tasks/{task_id}/status")
async def task_quick_status(
    task_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    status: str = Form(...),
):
    """Changement rapide de statut depuis le Kanban."""
    t = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404)
    if status not in TaskStatus.__members__:
        raise HTTPException(400)
    new_status = TaskStatus(status)
    if new_status == TaskStatus.DONE and t.status != TaskStatus.DONE:
        t.completed_at = datetime.utcnow()
        t.progress = 100
    if new_status != TaskStatus.DONE:
        t.completed_at = None
    t.status = new_status
    await db.commit()
    return RedirectResponse(url=f"/projects/{t.project_id}", status_code=303)


@router.post("/tasks/{task_id}/delete")
async def task_delete(
    task_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    t = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404)
    pid = t.project_id
    title = t.title
    # Récupère les sous-tâches récursivement
    to_delete = {task_id}
    frontier = {task_id}
    while frontier:
        sub = await db.execute(select(Task.id).where(Task.parent_task_id.in_(frontier)))
        new_ids = {sid for sid, in sub.all()} - to_delete
        if not new_ids:
            break
        to_delete |= new_ids
        frontier = new_ids
    await db.execute(delete(TaskComment).where(TaskComment.task_id.in_(to_delete)))
    await db.execute(delete(TaskDependency).where(
        or_(TaskDependency.predecessor_id.in_(to_delete),
            TaskDependency.successor_id.in_(to_delete))
    ))
    await db.execute(delete(Task).where(Task.id.in_(to_delete)))
    await _log(db, pid, member.id, "DELETE_TASK", title)
    await db.commit()
    return RedirectResponse(url=f"/projects/{pid}", status_code=303)


@router.post("/tasks/{task_id}/toggle")
async def task_toggle_done(
    task_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Bascule rapide TODO ↔ DONE depuis la liste."""
    user, member = ctx
    t = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404)
    if t.status == TaskStatus.DONE:
        t.status = TaskStatus.TODO
        t.completed_at = None
    else:
        t.status = TaskStatus.DONE
        t.completed_at = datetime.utcnow()
        t.progress = 100
    await _log(db, t.project_id, member.id, "STATUS", t.title, f"→ {t.status.value}")
    await db.commit()
    return RedirectResponse(
        url=request_referer_or(f"/projects/{t.project_id}"), status_code=303
    )


def request_referer_or(default: str) -> str:
    return default  # placeholder ; on garde simple


# ─────────────────────────────────────────────────────────────────────────────
#  Drag-and-drop Kanban : déplacer + réordonner
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/tasks/{task_id}/move")
async def task_move(
    task_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    status: str = Form(...),
    position: int = Form(0),
):
    """Endpoint AJAX pour Sortable.js : change statut + position."""
    user, member = ctx
    t = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404)
    if status not in TaskStatus.__members__:
        raise HTTPException(400, "Bad status")
    new_status = TaskStatus(status)
    old_status = t.status
    if new_status == TaskStatus.DONE and old_status != TaskStatus.DONE:
        t.completed_at = datetime.utcnow()
        t.progress = 100
    if new_status != TaskStatus.DONE:
        t.completed_at = None
    t.status = new_status
    t.order_position = max(0, position)
    if old_status != new_status:
        await _log(db, t.project_id, member.id, "STATUS", t.title,
                   f"{old_status.value} → {new_status.value}")
    await db.commit()
    return Response(status_code=204)


# ─────────────────────────────────────────────────────────────────────────────
#  Commentaires de tâche
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/tasks/{task_id}/comment")
async def task_comment(
    task_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    content: str = Form(...),
):
    user, member = ctx
    t = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404)
    txt = (content or "").strip()
    if not txt:
        return RedirectResponse(url=f"/projects/{t.project_id}", status_code=303)
    db.add(TaskComment(task_id=task_id, author_id=member.id, content=txt))
    await _log(db, t.project_id, member.id, "COMMENT", t.title, txt[:200])
    await db.commit()

    # Notifier l'assigné
    if t.assigned_to_id and t.assigned_to_id != member.id:
        try:
            from app.services.push import send_push_to_member
            await send_push_to_member(
                db, t.assigned_to_id,
                f"💬 Commentaire sur : {t.title}",
                f"{member.first_name} : {txt[:80]}",
                f"/projects/{t.project_id}",
            )
        except Exception:
            pass
    return RedirectResponse(url=f"/projects/{t.project_id}", status_code=303)


@router.post("/tasks/comments/{comment_id}/delete")
async def task_comment_delete(
    comment_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    c = (await db.execute(select(TaskComment).where(TaskComment.id == comment_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404)
    if c.author_id != member.id and not getattr(user, "is_admin", False):
        raise HTTPException(403)
    t = (await db.execute(select(Task).where(Task.id == c.task_id))).scalar_one_or_none()
    pid = t.project_id if t else None
    await db.execute(delete(TaskComment).where(TaskComment.id == comment_id))
    await db.commit()
    return RedirectResponse(url=f"/projects/{pid}" if pid else "/projects/", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
#  Export iCal + PDF
# ─────────────────────────────────────────────────────────────────────────────

def _ical_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


@router.get("/{project_id}/ical")
async def project_ical(
    project_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    p = (await db.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(404)
    tasks = (await db.execute(
        select(Task).where(
            Task.project_id == project_id,
            Task.due_date.isnot(None),
            Task.parent_task_id.is_(None),
        )
    )).scalars().all()

    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0",
        f"PRODID:-//Socrate//Projets {project_id}//FR",
        f"X-WR-CALNAME:{_ical_escape(p.name)}",
    ]
    now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    for t in tasks:
        s = t.start_date or t.due_date
        e = t.due_date or t.start_date
        lines += [
            "BEGIN:VEVENT",
            f"UID:task-{t.id}@socrate",
            f"DTSTAMP:{now}",
            f"DTSTART;VALUE=DATE:{s.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{(e + timedelta(days=1)).strftime('%Y%m%d')}",
            f"SUMMARY:{_ical_escape(t.title)}",
            f"DESCRIPTION:{_ical_escape((t.description or '') + chr(10) + 'Statut: ' + t.status.value)}",
            f"STATUS:{'COMPLETED' if t.status == TaskStatus.DONE else 'CONFIRMED'}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    body = "\r\n".join(lines)
    return Response(
        content=body, media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="projet-{project_id}.ics"'},
    )


@router.get("/{project_id}/export.pdf")
async def project_export_pdf(
    project_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Synthèse PDF : projet + liste des tâches groupées par statut."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
        )
    except Exception:
        raise HTTPException(500, "ReportLab manquant")

    p = (await db.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(404)
    tasks = (await db.execute(
        select(Task).where(
            Task.project_id == project_id,
            Task.parent_task_id.is_(None),  # tâches racines uniquement
        )
        .order_by(Task.status, Task.due_date.asc().nulls_last())
    )).scalars().all()

    # Cache assignés
    mids = {t.assigned_to_id for t in tasks if t.assigned_to_id}
    gids = {t.assigned_to_group_id for t in tasks if t.assigned_to_group_id}
    mcache: dict[int, Member] = {}
    gcache: dict[int, LodgeGroup] = {}
    if mids:
        for m in (await db.execute(select(Member).where(Member.id.in_(mids)))).scalars().all():
            mcache[m.id] = m
    if gids:
        for g in (await db.execute(select(LodgeGroup).where(LodgeGroup.id.in_(gids)))).scalars().all():
            gcache[g.id] = g

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("H1", parent=styles["Heading1"], textColor=colors.HexColor("#2c7a7b"))
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], textColor=colors.HexColor("#236b6b"))
    body_st = styles["BodyText"]

    elems = [Paragraph(p.name, h1)]
    if p.description:
        elems.append(Paragraph(p.description, body_st))
    meta = []
    if p.start_date or p.end_date:
        meta.append(f"Dates : {p.start_date or '—'} → {p.end_date or '—'}")
    meta.append(f"Statut : {p.status.value}")
    meta.append(f"Tâches : {len(tasks)}")
    elems.append(Paragraph(" · ".join(meta), body_st))
    elems.append(Spacer(1, 0.5*cm))

    status_order = [TaskStatus.IN_PROGRESS, TaskStatus.TODO, TaskStatus.DONE, TaskStatus.CANCELLED]
    status_lbl = {TaskStatus.TODO: "À faire", TaskStatus.IN_PROGRESS: "En cours",
                  TaskStatus.DONE: "Terminées", TaskStatus.CANCELLED: "Annulées"}

    for st in status_order:
        bucket = [t for t in tasks if t.status == st]
        if not bucket:
            continue
        elems.append(Paragraph(f"{status_lbl[st]} ({len(bucket)})", h2))
        rows = [["#", "Titre", "Assigné", "Échéance", "%"]]
        for t in bucket:
            assignee = "—"
            if t.assigned_to_id and t.assigned_to_id in mcache:
                m = mcache[t.assigned_to_id]
                assignee = f"{m.first_name} {m.last_name}"
            elif t.assigned_to_group_id and t.assigned_to_group_id in gcache:
                assignee = gcache[t.assigned_to_group_id].name
            rows.append([
                str(t.id), t.title[:60], assignee,
                t.due_date.strftime("%d/%m/%Y") if t.due_date else "—",
                f"{t.progress}%",
            ])
        tbl = Table(rows, colWidths=[1.2*cm, 7*cm, 4.5*cm, 2.8*cm, 1.5*cm])
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#e6f4f1")),
            ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE", (0,0), (-1,-1), 9),
            ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#cccccc")),
            ("VALIGN", (0,0), (-1,-1), "TOP"),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#f7fafa")]),
        ]))
        elems.append(tbl)
        elems.append(Spacer(1, 0.4*cm))

    doc.build(elems)
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="projet-{project_id}.pdf"'},
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Modèles de projets — CRUD
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/templates/new")
async def template_create(
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    name: str = Form(...),
    description: str = Form(""),
    color: str = Form("#4a9d8f"),
    type: str = Form("PROJECT"),
):
    user, member = ctx
    if not _is_manager(user, member):
        raise HTTPException(403)
    tpl = ProjectTemplate(
        name=name.strip(),
        description=description.strip() or None,
        color=color or "#4a9d8f",
        type=ProjectType(type) if type in ProjectType.__members__ else ProjectType.PROJECT,
        created_by_id=member.id,
    )
    db.add(tpl)
    await db.commit()
    await db.refresh(tpl)
    return RedirectResponse(url=f"/projects/templates/{tpl.id}", status_code=303)


@router.get("/templates/{tpl_id}", response_class=HTMLResponse)
async def template_detail(
    tpl_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    tpl = (await db.execute(
        select(ProjectTemplate).options(selectinload(ProjectTemplate.tasks))
        .where(ProjectTemplate.id == tpl_id)
    )).scalar_one_or_none()
    if not tpl:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "pages/projects/template_detail.html", {
        "current_user": user,
        "current_member": member,
        "tpl": tpl,
        "TaskPriority": TaskPriority,
        "can_manage": _is_manager(user, member),
    })


@router.post("/templates/{tpl_id}/tasks/new")
async def template_task_add(
    tpl_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    title: str = Form(...),
    description: str = Form(""),
    priority: str = Form("MEDIUM"),
    offset_start: str = Form(""),
    offset_due: str = Form(""),
):
    user, member = ctx
    if not _is_manager(user, member):
        raise HTTPException(403)
    db.add(ProjectTemplateTask(
        template_id=tpl_id,
        title=title.strip(),
        description=description.strip() or None,
        priority=TaskPriority(priority) if priority in TaskPriority.__members__ else TaskPriority.MEDIUM,
        offset_days_start=int(offset_start) if offset_start.lstrip("-").isdigit() else None,
        offset_days_due=int(offset_due) if offset_due.lstrip("-").isdigit() else None,
    ))
    await db.commit()
    return RedirectResponse(url=f"/projects/templates/{tpl_id}", status_code=303)


@router.post("/templates/{tpl_id}/tasks/{task_id}/delete")
async def template_task_delete(
    tpl_id: int, task_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not _is_manager(user, member):
        raise HTTPException(403)
    await db.execute(delete(ProjectTemplateTask).where(ProjectTemplateTask.id == task_id))
    await db.commit()
    return RedirectResponse(url=f"/projects/templates/{tpl_id}", status_code=303)


@router.post("/templates/{tpl_id}/delete")
async def template_delete(
    tpl_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not _is_manager(user, member):
        raise HTTPException(403)
    await db.execute(delete(ProjectTemplateTask).where(ProjectTemplateTask.template_id == tpl_id))
    await db.execute(delete(ProjectTemplate).where(ProjectTemplate.id == tpl_id))
    await db.commit()
    return RedirectResponse(url="/projects/templates", status_code=303)


@router.post("/templates/{tpl_id}/use")
async def template_instantiate(
    tpl_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    name: str = Form(...),
    start_date: str = Form(""),
):
    """Crée un nouveau projet à partir d'un template."""
    user, member = ctx
    if not _is_manager(user, member):
        raise HTTPException(403)
    tpl = (await db.execute(
        select(ProjectTemplate).options(selectinload(ProjectTemplate.tasks))
        .where(ProjectTemplate.id == tpl_id)
    )).scalar_one_or_none()
    if not tpl:
        raise HTTPException(404)

    base_date = _parse_date(start_date) or date.today()
    p = Project(
        name=name.strip() or tpl.name,
        description=tpl.description,
        color=tpl.color or "#4a9d8f",
        type=tpl.type,
        status=ProjectStatus.ACTIVE,
        start_date=base_date,
        owner_id=member.id,
        created_by_id=member.id,
    )
    db.add(p)
    await db.flush()

    end_offsets = []
    for tt in tpl.tasks:
        sd = base_date + timedelta(days=tt.offset_days_start) if tt.offset_days_start is not None else None
        dd = base_date + timedelta(days=tt.offset_days_due) if tt.offset_days_due is not None else None
        if dd:
            end_offsets.append(dd)
        db.add(Task(
            project_id=p.id,
            title=tt.title,
            description=tt.description,
            status=TaskStatus.TODO,
            priority=tt.priority,
            start_date=sd,
            due_date=dd,
            order_position=tt.order_position,
            created_by_id=member.id,
        ))
    if end_offsets:
        p.end_date = max(end_offsets)
    await _log(db, p.id, member.id, "CREATE_FROM_TEMPLATE", tpl.name)
    await db.commit()
    return RedirectResponse(url=f"/projects/{p.id}", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
#  Sous-tâches & dépendances
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/tasks/{parent_id}/subtask")
async def task_add_subtask(
    parent_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    title: str = Form(...),
):
    user, member = ctx
    parent = (await db.execute(select(Task).where(Task.id == parent_id))).scalar_one_or_none()
    if not parent:
        raise HTTPException(404)
    title = (title or "").strip()
    if not title:
        return RedirectResponse(url=f"/projects/{parent.project_id}", status_code=303)
    db.add(Task(
        project_id=parent.project_id,
        parent_task_id=parent_id,
        title=title,
        status=TaskStatus.TODO,
        priority=parent.priority,
        created_by_id=member.id,
    ))
    await _log(db, parent.project_id, member.id, "ADD_SUBTASK", parent.title, title)
    await db.commit()
    return RedirectResponse(url=f"/projects/{parent.project_id}", status_code=303)


@router.post("/tasks/{task_id}/dependency")
async def task_add_dependency(
    task_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    predecessor_id: int = Form(...),
):
    """Ajoute : `task_id` dépend de `predecessor_id` (predecessor doit finir avant)."""
    user, member = ctx
    if predecessor_id == task_id:
        raise HTTPException(400, "Une tâche ne peut pas dépendre d'elle-même")
    t = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    pred = (await db.execute(select(Task).where(Task.id == predecessor_id))).scalar_one_or_none()
    if not t or not pred:
        raise HTTPException(404)
    # Empêche un cycle direct
    existing = (await db.execute(
        select(TaskDependency).where(
            TaskDependency.predecessor_id == task_id,
            TaskDependency.successor_id == predecessor_id,
        )
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(400, "Cycle de dépendances détecté")
    # Idempotent
    dup = (await db.execute(
        select(TaskDependency).where(
            TaskDependency.predecessor_id == predecessor_id,
            TaskDependency.successor_id == task_id,
        )
    )).scalar_one_or_none()
    if not dup:
        db.add(TaskDependency(predecessor_id=predecessor_id, successor_id=task_id))
        await _log(db, t.project_id, member.id, "DEPENDENCY",
                   t.title, f"après : {pred.title}")
        await db.commit()
    return RedirectResponse(url=f"/projects/{t.project_id}", status_code=303)


@router.post("/tasks/{task_id}/dependency/{predecessor_id}/delete")
async def task_remove_dependency(
    task_id: int, predecessor_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    t = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404)
    await db.execute(delete(TaskDependency).where(
        TaskDependency.successor_id == task_id,
        TaskDependency.predecessor_id == predecessor_id,
    ))
    await db.commit()
    return RedirectResponse(url=f"/projects/{t.project_id}", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
#  Tableau perso : "Mes tâches"
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/me/tasks", response_class=HTMLResponse)
async def my_tasks(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    # Tâches directement assignées
    r1 = await db.execute(
        select(Task, Project)
        .join(Project, Project.id == Task.project_id)
        .where(Task.assigned_to_id == member.id, Task.status != TaskStatus.DONE)
        .order_by(Task.due_date.asc().nulls_last(), Task.priority.desc())
    )
    direct = list(r1.all())

    # Tâches assignées à un groupe dont le membre est membre
    from app.models.groups import GroupMembership
    my_groups = (await db.execute(
        select(GroupMembership.group_id).where(GroupMembership.member_id == member.id)
    )).scalars().all()

    via_group = []
    if my_groups:
        r2 = await db.execute(
            select(Task, Project)
            .join(Project, Project.id == Task.project_id)
            .where(
                Task.assigned_to_group_id.in_(my_groups),
                Task.status != TaskStatus.DONE,
            )
            .order_by(Task.due_date.asc().nulls_last(), Task.priority.desc())
        )
        via_group = list(r2.all())

    return templates.TemplateResponse(request, "pages/projects/my_tasks.html", {
        "current_user": user,
        "current_member": member,
        "direct": direct,
        "via_group": via_group,
        "today": date.today(),
        "TaskStatus": TaskStatus,
        "TaskPriority": TaskPriority,
    })
