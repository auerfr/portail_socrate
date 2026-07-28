"""Confirmation annuelle des correspondants externes (F∴/S∴ passant·e·s
réguliers, loges amies…) : email de confirmation avec lien pour continuer à
recevoir les programmes ou mettre à jour son adresse, et désactivation
(jamais suppression) des non-répondants après un délai — déclenché
manuellement par un admin, cf. app/routers/settings.py.
"""
import asyncio
import hashlib
import hmac
import logging
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote

from sqlalchemy import select, update

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models.lodge import ExternalContact
from app.services.email import _send_raw, create_pending_log

logger = logging.getLogger(__name__)

CONFIRMATION_EMAIL_SUBJECT = "Souhaitez-vous continuer à recevoir nos programmes ?"
CONFIRMATION_EMAIL_DELAY_MS = 300

_RUNNING_TASKS: set = set()


# ── Tokens de confirmation ──────────────────────────────────────────────────
# Namespace "cc." distinct des tokens de app.services.mailing (unsubscribe,
# par liste) et de app.services.member_access (accès portail) — même id
# numérique pouvant exister dans plusieurs tables, on évite toute ambiguïté.

def _cc_secret() -> bytes:
    return (get_settings().secret_key or "fallback-secret").encode("utf-8")


def make_cc_token(contact_id: int, kind: str) -> str:
    """kind = 'confirm' ou 'update'."""
    payload = f"cc.{contact_id}.{kind}"
    sig = hmac.new(_cc_secret(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{contact_id}.{kind}.{sig}"


def verify_cc_token(token: str) -> Optional[tuple[int, str]]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        contact_id_s, kind, sig = parts
        if kind not in ("confirm", "update"):
            return None
        payload = f"cc.{contact_id_s}.{kind}"
        expected = hmac.new(_cc_secret(), payload.encode(), hashlib.sha256).hexdigest()[:16]
        if not hmac.compare_digest(expected, sig):
            return None
        return (int(contact_id_s), kind)
    except (ValueError, AttributeError):
        return None


def launch_confirmation_campaign(portal_url: Optional[str] = None) -> None:
    """Démarre l'envoi en tâche de fond (fire-and-forget)."""
    task = asyncio.ensure_future(run_confirmation_campaign(portal_url))
    _RUNNING_TASKS.add(task)
    task.add_done_callback(_RUNNING_TASKS.discard)


def _confirmation_email_content(contact: ExternalContact, portal_url: str, lodge_name: str) -> tuple[str, str]:
    confirm_url = f"{portal_url}/contacts/confirm/{make_cc_token(contact.id, 'confirm')}"
    update_url = f"{portal_url}/contacts/update-email/{make_cc_token(contact.id, 'update')}"
    prenom = contact.first_name or contact.name

    text = f"""Bonjour {prenom},

Vous recevez actuellement les programmes de la loge {lodge_name} à cette adresse ({contact.email}).

Afin de tenir notre liste à jour, merci de confirmer que vous souhaitez continuer à les recevoir :
{confirm_url}

Si votre adresse email a changé, vous pouvez la mettre à jour ici :
{update_url}

Sans action de votre part, votre adresse reste enregistrée telle quelle pour le moment.

Cordialement,
{lodge_name}"""

    html = f"""<p>Bonjour {prenom},</p>
<p>Vous recevez actuellement les programmes de la loge <strong>{lodge_name}</strong> à cette adresse (<strong>{contact.email}</strong>).</p>
<p>Afin de tenir notre liste à jour, merci de confirmer que vous souhaitez continuer à les recevoir :</p>
<p><a href="{confirm_url}" style="background:#2c7a7b;color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none;display:inline-block;margin:8px 0">
  Je confirme, je continue à recevoir les programmes →
</a></p>
<p>Si votre adresse email a changé :</p>
<p><a href="{update_url}" style="color:#2c7a7b;">Mettre à jour mon adresse email</a></p>
<p style="color:#6b7280;font-size:13px;">Sans action de votre part, votre adresse reste enregistrée telle quelle pour le moment.</p>
<hr><p style="color:#888;font-size:12px">{lodge_name}</p>"""

    return html, text


async def send_confirmation_email(db, contact: ExternalContact, portal_url: str) -> tuple[bool, Optional[str]]:
    settings = get_settings()
    subject = f"[{settings.lodge_name}] {CONFIRMATION_EMAIL_SUBJECT}"
    log_id = await create_pending_log(contact.email, subject)
    html, text = _confirmation_email_content(contact, portal_url, settings.lodge_name)
    ok, err = await _send_raw(to=contact.email, subject=subject, html=html, text=text, log_id=log_id)
    return ok, err


async def run_confirmation_campaign(portal_url: Optional[str] = None) -> None:
    """Envoie l'email de confirmation à tous les correspondants externes actifs."""
    settings = get_settings()
    base_url = (portal_url or settings.portal_url or "https://portail.amisdesocrate.fr").rstrip("/")

    async with AsyncSessionLocal() as db:
        contacts = (await db.execute(
            select(ExternalContact).where(ExternalContact.is_active == True)  # noqa: E712
            .order_by(ExternalContact.name)
        )).scalars().all()

        for contact in contacts:
            try:
                await send_confirmation_email(db, contact, base_url)
            except Exception:
                logger.exception("Échec envoi confirmation → contact %s", contact.id)
            await asyncio.sleep(CONFIRMATION_EMAIL_DELAY_MS / 1000.0)


async def deactivate_unconfirmed(db, older_than_days: int = 60) -> int:
    """Désactive (is_active=False) les contacts actifs jamais confirmés ou
    dont la dernière confirmation date de plus de `older_than_days` jours.
    Ne supprime jamais rien — réversible en un clic depuis Paramètres."""
    cutoff = datetime.utcnow() - timedelta(days=max(1, older_than_days))
    result = await db.execute(
        update(ExternalContact)
        .where(
            ExternalContact.is_active == True,  # noqa: E712
            (ExternalContact.last_confirmed_at.is_(None)) | (ExternalContact.last_confirmed_at < cutoff),
        )
        .values(is_active=False)
    )
    await db.commit()
    return result.rowcount or 0
