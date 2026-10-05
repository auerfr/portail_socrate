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
    """Prépare la clé privée VAPID stockée en .env pour pywebpush.

    pywebpush.webpush() route une clé passée en simple chaîne vers
    Vapid.from_string(), qui attend le corps base64url BRUT de la clé DER —
    PAS un bloc PEM avec ses en-têtes "-----BEGIN/END EC PRIVATE KEY-----".
    from_string() tente de base64-décoder la chaîne telle quelle (en-têtes
    PEM inclus s'ils sont présents), ce qui produit un DER invalide et
    l'erreur cryptography "ASN.1 parsing error: invalid length" — reproduit
    et confirmé avec une vraie clé générée localement. On retire donc
    systématiquement l'enveloppe PEM et les retours à la ligne, après avoir
    reconstruit ces derniers (le .env stocke la clé sur une seule ligne avec
    des \\n littéraux)."""
    if not key:
        return ""
    key = key.replace("\\n", "\n").strip()
    body_lines = [line.strip() for line in key.splitlines() if line.strip() and "-----" not in line]
    return "".join(body_lines) if body_lines else key


async def send_push_to_subscription(sub: PushSubscription, title: str, body: str, url: str = "/") -> tuple[PushResult, str]:
    """Envoie une notification à un abonnement précis. Renvoie aussi un message
    diagnostique lisible (raison de l'échec le cas échéant) — utile pour
    l'afficher directement à l'utilisateur plutôt que de le renvoyer fouiller
    les logs serveur."""
    s = get_settings()
    if not s.vapid_private_key or not s.vapid_claim_email:
        return PushResult.FAILED, "Clés VAPID non configurées côté serveur."

    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.error("pywebpush non installé")
        return PushResult.FAILED, "Module pywebpush non installé côté serveur."

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
        return PushResult.SENT, "OK"
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None) if exc.response else None
        body_text = ""
        if exc.response is not None:
            try:
                body_text = exc.response.text[:200]
            except Exception:
                pass
        # 404 / 410 : abonnement expiré ou révoqué → demander la suppression
        if status in (404, 410):
            logger.info("Abonnement push expiré (%s), suppression : %s", status, sub.endpoint[:60])
            return PushResult.EXPIRED, f"Abonnement expiré/révoqué (HTTP {status})."
        logger.warning("Échec push %s : %s", sub.endpoint[:60], exc)
        reason = f"Refusé par le service de push (HTTP {status or '?'})"
        if body_text:
            reason += f" : {body_text}"
        return PushResult.FAILED, reason  # garder l'abonnement, c'est peut-être temporaire
    except Exception as exc:
        logger.error("Erreur push inattendue : %s", exc)
        return PushResult.FAILED, f"Erreur inattendue : {exc}"


async def send_push_to_member(db: AsyncSession, member_id: int, title: str, body: str, url: str = "/") -> int:
    """Envoie une notif à tous les abonnements d'un membre. Retourne le nb d'envois réellement acceptés
    par le service de push (un échec non lié à un abonnement expiré ne compte pas comme un envoi)."""
    r = await db.execute(select(PushSubscription).where(PushSubscription.member_id == member_id))
    subs = list(r.scalars().all())
    sent = 0
    dead_ids = []
    for sub in subs:
        result, _reason = await send_push_to_subscription(sub, title, body, url)
        if result is PushResult.SENT:
            sent += 1
        elif result is PushResult.EXPIRED:
            dead_ids.append(sub.id)
    if dead_ids:
        await db.execute(delete(PushSubscription).where(PushSubscription.id.in_(dead_ids)))
        await db.commit()
    return sent


async def send_push_to_member_diagnostic(db: AsyncSession, member_id: int, title: str, body: str, url: str = "/") -> list[dict]:
    """Comme send_push_to_member, mais renvoie le détail par abonnement
    (résultat + raison lisible) au lieu d'un simple compteur — pour le
    bouton "Envoyer un test", où l'utilisateur doit voir la vraie cause
    d'un échec sans avoir à consulter les logs serveur."""
    r = await db.execute(select(PushSubscription).where(PushSubscription.member_id == member_id))
    subs = list(r.scalars().all())
    details = []
    dead_ids = []
    for sub in subs:
        result, reason = await send_push_to_subscription(sub, title, body, url)
        details.append({"result": result.value, "reason": reason})
        if result is PushResult.EXPIRED:
            dead_ids.append(sub.id)
    if dead_ids:
        await db.execute(delete(PushSubscription).where(PushSubscription.id.in_(dead_ids)))
        await db.commit()
    return details


async def send_push_broadcast(db: AsyncSession, member_ids: list[int], title: str, body: str, url: str = "/") -> int:
    """Envoie une notif à plusieurs membres."""
    if not member_ids:
        return 0
    r = await db.execute(select(PushSubscription).where(PushSubscription.member_id.in_(member_ids)))
    subs = list(r.scalars().all())
    sent = 0
    dead_ids = []
    for sub in subs:
        result, _reason = await send_push_to_subscription(sub, title, body, url)
        if result is PushResult.SENT:
            sent += 1
        elif result is PushResult.EXPIRED:
            dead_ids.append(sub.id)
    if dead_ids:
        await db.execute(delete(PushSubscription).where(PushSubscription.id.in_(dead_ids)))
        await db.commit()
    return sent
