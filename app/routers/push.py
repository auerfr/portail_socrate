"""Router Web Push — abonnement / désabonnement / test."""
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.dependencies import require_auth
from app.models.system import PushSubscription
from app.services.push import send_push_to_member

router = APIRouter(prefix="/push", tags=["push"])


class SubscriptionKeys(BaseModel):
    p256dh: str
    auth: str


class SubscriptionPayload(BaseModel):
    endpoint: str
    keys: SubscriptionKeys


@router.get("/vapid-key")
async def get_vapid_public_key():
    """Renvoie la clé publique VAPID pour le client."""
    s = get_settings()
    if not s.vapid_public_key:
        raise HTTPException(status_code=503, detail="Push non configuré (VAPID_PUBLIC_KEY manquant)")
    return {"public_key": s.vapid_public_key}


@router.post("/subscribe")
async def subscribe(
    payload: SubscriptionPayload,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not member:
        raise HTTPException(status_code=403, detail="Compte sans membre lié")

    # Si déjà abonné avec le même endpoint, on met à jour
    r = await db.execute(select(PushSubscription).where(PushSubscription.endpoint == payload.endpoint))
    existing = r.scalar_one_or_none()
    if existing:
        existing.member_id = member.id
        existing.key_p256dh = payload.keys.p256dh
        existing.key_auth = payload.keys.auth
    else:
        sub = PushSubscription(
            member_id=member.id,
            endpoint=payload.endpoint,
            key_p256dh=payload.keys.p256dh,
            key_auth=payload.keys.auth,
        )
        db.add(sub)
    await db.commit()
    return {"ok": True}


@router.post("/unsubscribe")
async def unsubscribe(
    payload: dict,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    endpoint = payload.get("endpoint")
    if not endpoint:
        raise HTTPException(status_code=400, detail="endpoint requis")
    await db.execute(delete(PushSubscription).where(PushSubscription.endpoint == endpoint))
    await db.commit()
    return {"ok": True}


@router.post("/test")
async def test_push(
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Envoie une notification de test au membre courant."""
    user, member = ctx
    if not member:
        raise HTTPException(status_code=403, detail="Compte sans membre lié")
    sent = await send_push_to_member(
        db, member.id,
        title="🔔 Test de notification",
        body=f"Bonjour {member.first_name}, vos notifications push fonctionnent !",
        url="/",
    )
    return {"ok": True, "sent": sent}


@router.get("/status")
async def status(
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Liste les abonnements push du membre courant."""
    user, member = ctx
    if not member:
        return {"configured": False, "subscriptions": 0}
    s = get_settings()
    r = await db.execute(select(PushSubscription).where(PushSubscription.member_id == member.id))
    subs = r.scalars().all()
    return {
        "configured": bool(s.vapid_public_key),
        "subscriptions": len(subs),
    }
