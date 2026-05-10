"""Router Anniversaires maçonniques."""
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_auth
from app.models.identity import Member, MemberStatus
from app.services.anniversaires import compute_anniversaires, upcoming

router = APIRouter(prefix="/anniversaires", tags=["anniversaires"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def anniversaires_page(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    today = date.today()

    members_r = await db.execute(
        select(Member).where(Member.status == MemberStatus.ACTIVE).order_by(Member.last_name)
    )
    members = members_r.scalars().all()

    all_ann   = compute_anniversaires(members, today)
    upcoming_ = upcoming(members, days=60, today=today)

    # Grouper par mois pour l'affichage
    months: dict[int, list] = {}
    for a in all_ann:
        months.setdefault(a.anniv_date.month, []).append(a)

    return templates.TemplateResponse(request, "pages/anniversaires/index.html", {
        "current_user": user,
        "current_member": member,
        "today": today,
        "upcoming": upcoming_,
        "months": months,
        "all_ann": all_ann,
    })
