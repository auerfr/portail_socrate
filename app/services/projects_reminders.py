"""Rappels J-3 pour les tâches projet — push à l'assigné."""
import asyncio
from datetime import datetime, date, timedelta

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.projects import Task, TaskStatus, Project


async def _run_once():
    """Vérifie les tâches dont l'échéance est dans 3 jours, envoie un push J-3
    une seule fois (champ `reminded_at`)."""
    today = date.today()
    target = today + timedelta(days=3)

    async with AsyncSessionLocal() as s:
        r = await s.execute(
            select(Task).where(
                Task.due_date == target,
                Task.status != TaskStatus.DONE,
                Task.status != TaskStatus.CANCELLED,
                Task.reminded_at.is_(None),
                Task.assigned_to_id.isnot(None),
            )
        )
        tasks = r.scalars().all()
        if not tasks:
            return 0

        # Cache projets
        pids = {t.project_id for t in tasks if t.project_id}
        pcache = {}
        if pids:
            pr = await s.execute(select(Project).where(Project.id.in_(pids)))
            for p in pr.scalars().all():
                pcache[p.id] = p

        try:
            from app.services.push import send_push_to_member
        except Exception:
            send_push_to_member = None

        sent = 0
        for t in tasks:
            if send_push_to_member:
                try:
                    p = pcache.get(t.project_id)
                    await send_push_to_member(
                        s, t.assigned_to_id,
                        f"⏰ J-3 : {t.title}",
                        f"Échéance le {t.due_date.strftime('%d/%m/%Y')}"
                        + (f" · {p.name}" if p else ""),
                        f"/projects/{t.project_id}" if t.project_id else "/projects/",
                    )
                except Exception:
                    pass
            t.reminded_at = datetime.utcnow()
            sent += 1
        await s.commit()
        return sent


async def daily_task_reminder_loop():
    """Lance _run_once une fois par jour à ~08h00."""
    while True:
        try:
            now = datetime.now()
            target = now.replace(hour=8, minute=0, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            wait_s = (target - now).total_seconds()
            await asyncio.sleep(max(60, wait_s))
            await _run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            # En cas d'erreur, on attend une heure et on retente
            await asyncio.sleep(3600)
