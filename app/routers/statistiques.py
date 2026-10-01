"""Router Statistiques de la loge — ouvert à tous les membres (agrégats anonymes)."""
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_auth
from app.services.lodge_stats import compute_lodge_statistics

router = APIRouter(prefix="/statistiques", tags=["statistiques"])
from app.template_engine import templates


@router.get("/", response_class=HTMLResponse)
async def statistiques_page(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    stats = await compute_lodge_statistics(db)
    return templates.TemplateResponse(request, "pages/statistiques/index.html", {
        "current_user": user,
        "current_member": member,
        **stats,
    })
