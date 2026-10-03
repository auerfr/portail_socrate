"""Service Web Push — envoi de notifications PWA via VAPID."""
import json
import logging
from enum import Enum
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.system import PushSubscription

logger = logging.getLogger(__name__)


class PushResult(Enum):
    """Distingue explicitement un envoi réussi d'un abonnement mort et d'un
    échec de livraison — auparavant ces trois cas étaient confondus dans un
    seul booléen, si bien qu'un rejet du service de push (ex. erreur VAPID
    côté web.push.apple.com) était compté comme un envoi réussi."""
    SENT = "sent"
    EXPIRED = "expired"
    FAILED = "failed"


def _normalize_private_key(key: str) -> str:
    """Le PEM en .env est sur une ligne avec \\n littéraux → reconstruire les sauts de ligne."""
    if not key:
        return ""
    return key.replace("\\n", "\n").strip()


async def send_push_to_subscription(sub: PushSubscription, title: str, body: str, url: str = "/") -> PushResult:
    """Envoie une notification à un abonnement précis."""
    s = get_settings()
    if not s.vapid_private_key or not s.vapid_claim_email:
        return PushResult.FAILED

    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.error("pywebpush non installé")
        return PushResult.FAILED

    subscription_info = {
        "endpoint": sub.endpoint,
        "keys": {"p256dh": sub.key_p256dh, "auth": sub.key_auth},
    }
    payload = json.dumps({"title": title, "body": body, "url": url})
    claims = {"sub": s.vapid_claim_email if s.vapid_claim_email.startswith("mailto:") else f"mailto:{s.vapid_claim_email}"}
    private_key = _normalize_private_key(s.vapid_private_key)

    try:
        webpush(
            subscription_info=subscription_info,
            data=payload,
            vapid_private_key=private_key,
            vapid_claims=claims,
            ttl=86400,
        )
        return PushResult.SENT
    except WebPushException as exc:
        # 404 / 410 : abonnement expiré ou révoqué → demander la suppression
        status = getattr(exc.response, "status_code", None) if exc.response else None
        if status in (404, 410):
            logger.info("Abonnement push expiré (%s), suppression : %s", status, sub.endpoint[:60])
            return PushResult.EXPIRED
        logger.warning("Échec push %s : %s", sub.endpoint[:60], exc)
        return PushResult.FAILED  # garder l'abonnement, c'est peut-être temporaire
    except Exception as exc:
        logger.error("Erreur push inattendue : %s", exc)
        return PushResult.FAILED


async def send_push_to_member(db: AsyncSession, member_id: int, title: str, body: str, url: str = "/") -> int:
    """Envoie une notif à tous les abonnements d'un membre. Retourne le nb d'envois réellement acceptés
    par le service de push (un échec non lié à un abonnement expiré ne compte pas comme un envoi)."""
    r = await db.execute(select(PushSubscription).where(PushSubscription.member_id == member_id))
    subs = list(r.scalars().all())
    sent = 0
    dead_ids = []
    for sub in subs:
        result = await send_push_to_subscription(sub, title, body, url)
        if result is PushResult.SENT:
            sent += 1
        elif result is PushResult.EXPIRED:
            dead_ids.append(sub.id)
    if dead_ids:
        await db.execute(delete(PushSubscription).where(PushSubscription.id.in_(dead_ids)))
        await db.commit()
    return sent


async def send_push_broadcast(db: AsyncSession, member_ids: list[int], title: str, body: str, url: str = "/") -> int:
    """Envoie une notif à plusieurs membres."""
    if not member_ids:
        return 0
    r = await db.execute(select(PushSubscription).where(PushSubscription.member_id.in_(member_ids)))
    subs = list(r.scalars().all())
    sent = 0
    dead_ids = []
    for sub in subs:
        result = await send_push_to_subscription(sub, title, body, url)
        if result is PushResult.SENT:
            sent += 1
        elif result is PushResult.EXPIRED:
            dead_ids.append(sub.id)
    if dead_ids:
        await db.execute(delete(PushSubscription).where(PushSubscription.id.in_(dead_ids)))
        await db.commit()
    return sent
