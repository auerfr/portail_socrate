"""Tableau de bord d'engagement (VM/Secrétaire) — taux d'ouverture des emails
et tendance d'assiduité aux tenues, à partir des données déjà collectées
(EmailLog, Attendance)."""
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_engagement_viewer
from app.models.system import EmailLog, EmailStatus
from app.models.meetings import Attendance, AttendanceStatus, Meeting
from app.models.lodge import MasonicYear
from app.utils.tz import to_paris

router = APIRouter(prefix="/engagement", tags=["engagement"])
from app.template_engine import templates


@router.get("/", response_class=HTMLResponse)
async def engagement_dashboard(
    request: Request,
    ctx: Annotated[tuple, Depends(require_engagement_viewer)],
    db: Annotated[AsyncSession, Depends(get_db)],
    days: int = 90,
):
    user, member = ctx
    days = max(7, min(days, 365))
    since = datetime.now() - timedelta(days=days)

    # ── Engagement email (fenêtre sélectionnée) ─────────────────────────
    r = await db.execute(select(EmailLog).where(EmailLog.created_at >= since))
    logs = r.scalars().all()
    total = len(logs)
    sent = sum(1 for l in logs if l.status == EmailStatus.SENT)
    opened = sum(1 for l in logs if l.opened_at is not None)
    clicked = sum(1 for l in logs if l.clicked_at is not None)
    open_rate = round(opened * 100 / sent) if sent else 0
    click_rate = round(clicked * 100 / sent) if sent else 0

    # tendance mensuelle (6 derniers mois, indépendante de "days")
    r6 = await db.execute(
        select(EmailLog).where(EmailLog.created_at >= datetime.now() - timedelta(days=180))
    )
    logs6 = r6.scalars().all()
    by_month: dict[str, dict[str, int]] = {}
    for l in logs6:
        key = to_paris(l.created_at).strftime("%Y-%m")
        m = by_month.setdefault(key, {"sent": 0, "opened": 0})
        if l.status == EmailStatus.SENT:
            m["sent"] += 1
        if l.opened_at is not None:
            m["opened"] += 1
    email_trend = [{"month": k, **v} for k, v in sorted(by_month.items())]

    # ── Assiduité par année maçonnique (tendance) ───────────────────────
    r = await db.execute(select(MasonicYear).order_by(MasonicYear.start_date.desc()).limit(6))
    years = r.scalars().all()
    attendance_trend = []
    today = datetime.now().date()
    for y in years:
        r2 = await db.execute(
            select(Attendance.status, func.count(Attendance.id))
            .join(Meeting, Meeting.id == Attendance.meeting_id)
            .where(Meeting.masonic_year_id == y.id, Meeting.meeting_date <= today)
            .group_by(Attendance.status)
        )
        counts = {row[0]: row[1] for row in r2.all()}
        present = counts.get(AttendanceStatus.PRESENT, 0)
        total_att = sum(counts.values())
        pct = round(present * 100 / total_att) if total_att else None
        attendance_trend.append({"year": y.label, "present": present, "total": total_att, "pct": pct})
    attendance_trend.reverse()

    return templates.TemplateResponse(request, "pages/engagement/index.html", {
        "current_user": user,
        "current_member": member,
        "days": days,
        "email_total": total, "email_sent": sent, "email_opened": opened, "email_clicked": clicked,
        "open_rate": open_rate, "click_rate": click_rate,
        "email_trend": email_trend,
        "attendance_trend": attendance_trend,
    })
