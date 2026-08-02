"""Centre de notifications — API pour la cloche (badge + dropdown)."""
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_auth
from app.services.notifications import get_notification_events, get_notifications_count

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/api/list")
async def notifications_list(
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    events = await get_notification_events(db, user, member)
    return JSONResponse({
        "events": [
            {
                "type": e["type"], "icon": e["icon"], "label": e["label"],
                "sub": e["sub"], "url": e["url"], "ts": e["ts_str"], "is_new": e["is_new"],
            }
            for e in events
        ],
    })


@router.get("/api/count")
async def notifications_count(
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    count = await get_notifications_count(db, user, member)
    return JSONResponse({"total": count})


@router.post("/api/mark-seen")
async def notifications_mark_seen(
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    member.notifications_seen_at = datetime.now()
    await db.commit()
    return JSONResponse({"ok": True})
