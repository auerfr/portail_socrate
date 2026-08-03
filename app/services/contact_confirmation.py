"""Confirmation annuelle des correspondants externes (F∴/S∴ passant·e·s
réguliers, loges amies…) : email avec lien unique pour mettre à jour ses
informations (ce qui vaut confirmation) ou demander sa désinscription.
Déclenché manuellement par un admin, cf. app/routers/settings.py.
"""
import asyncio
import hashlib
import hmac
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, update

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models.lodge import ExternalContact
from app.services.email import _send_raw, create_pending_log

logger = logging.getLogger(__name__)

LODGE_FULL_NAME = "Socrate Raison et Progrès à l'orient de Pont à Mousson du GODF"
LODGE_SHORT_NAME = "Socrate Raison et Progrès"
SIGNATURE = "Les frères et Sœurs de Socrate Raison et Progrès"

CONFIRMATION_EMAIL_SUBJECT = "Souhaitez-vous continuer à recevoir nos programmes ?"
CONFIRMATION_EMAIL_DELAY_MS = 300

_RUNNING_TASKS: set = set()


# ── Tokens de confirmation ──────────────────────────────────────────────────

def _cc_secret() -> bytes:
    return (get_settings().secret_key or "fallback-secret").encode("utf-8")


def make_cc_token(contact_id: int, kind: str) -> str:
    """kind = 'update' | 'remove'."""
    payload = f"cc.{contact_id}.{kind}"
    sig = hmac.new(_cc_secret(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{contact_id}.{kind}.{sig}"


def verify_cc_token(token: str) -> Optional[tuple[int, str]]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        contact_id_s, kind, sig = parts
        if kind not in ("confirm", "update", "remove"):
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


def _confirmation_email_content(contact: ExternalContact, portal_url: str) -> tuple[str, str]:
    update_url = f"{portal_url}/contacts/update/{make_cc_token(contact.id, 'update')}"
    remove_url = f"{portal_url}/contacts/remove/{make_cc_token(contact.id, 'remove')}"
    prenom = contact.first_name or contact.name.split()[0] if contact.name else "Chère/Cher F∴/S∴"

    text = f"""Bonjour {prenom},

Vous figurez dans notre liste de correspondants de la loge {LODGE_FULL_NAME} et recevez nos programmes à cette adresse ({contact.email}).

Pour mettre à jour vos informations (nom, loge, obédience…) ou simplement confirmer que vous souhaitez continuer à recevoir nos communications, cliquez ici :
{update_url}

Vous avez le droit de demander à être retiré·e de notre liste à tout moment. Pour cela, utilisez ce lien :
{remove_url}

🔒 Vos coordonnées sont strictement confidentielles, utilisées uniquement pour l'envoi de nos programmes. Elles ne sont ni partagées, ni cédées à des tiers. Nos communications vous sont adressées individuellement.

Sans action de votre part, vous continuerez à recevoir nos programmes.

Fraternellement,
{SIGNATURE}"""

    html = f"""
<div style="font-family:Arial,sans-serif;max-width:580px;margin:0 auto;padding:24px;color:#222;">
  <p style="font-size:15px;line-height:1.6;">Bonjour {prenom},</p>
  <p style="font-size:15px;line-height:1.6;">
    Vous figurez dans notre liste de correspondants de la loge
    <strong>{LODGE_FULL_NAME}</strong><br>
    et recevez nos programmes à cette adresse : <strong>{contact.email}</strong>.
  </p>
  <p style="font-size:15px;line-height:1.6;">
    Pour mettre à jour vos informations (nom, loge, obédience…) ou confirmer
    que vous souhaitez continuer à recevoir nos communications :
  </p>
  <p style="margin:20px 0;">
    <a href="{update_url}"
       style="background:#1a5252;color:#fff;padding:12px 24px;border-radius:6px;
              text-decoration:none;display:inline-block;font-size:14px;font-weight:600;">
      Mettre à jour mes informations →
    </a>
  </p>
  <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:6px;padding:12px 16px;margin:20px 0;">
    <p style="font-size:13px;color:#166534;line-height:1.6;margin:0;">
      🔒 <strong>Confidentialité</strong> — Vos coordonnées sont strictement confidentielles
      et utilisées uniquement pour l'envoi de nos programmes.
      Elles ne sont ni partagées ni cédées à des tiers.
      Nos communications vous sont adressées individuellement.
    </p>
  </div>
  <hr style="border:none;border-top:1px solid #e5e7eb;margin:20px 0;">
  <p style="font-size:13px;color:#6b7280;line-height:1.6;">
    Vous avez le droit de demander à être retiré·e de notre liste à tout moment.<br>
    Pour cela, utilisez ce lien :
    <a href="{remove_url}" style="color:#6b7280;">Me désinscrire de la liste</a>
  </p>
  <p style="font-size:13px;color:#6b7280;">
    Sans action de votre part, vous continuerez à recevoir nos programmes.
  </p>
  <hr style="border:none;border-top:1px solid #e5e7eb;margin:20px 0;">
  <p style="font-size:13px;color:#888;">Fraternellement,<br><strong>{SIGNATURE}</strong></p>
</div>"""

    return html, text


async def send_confirmation_email(db, contact: ExternalContact, portal_url: str) -> tuple[bool, Optional[str]]:
    settings = get_settings()
    subject = f"[{LODGE_SHORT_NAME}] {CONFIRMATION_EMAIL_SUBJECT}"
    log_id = await create_pending_log(contact.email, subject)
    html, text = _confirmation_email_content(contact, portal_url)
    ok, err = await _send_raw(to=contact.email, subject=subject, html=html, text=text, log_id=log_id)
    return ok, err


async def run_confirmation_campaign(portal_url: Optional[str] = None) -> None:
    """Envoie l'email de confirmation à tous les correspondants externes actifs."""
    settings = get_settings()
    base_url = (portal_url or settings.portal_url or "https://portail.amisdesocrate.fr").rstrip("/")

    async with AsyncSessionLocal() as db:
        contacts = (await db.execute(
            select(ExternalContact)
            .where(
                ExternalContact.is_active == True,  # noqa: E712
                ExternalContact.removal_requested_at.is_(None),
            )
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
            ExternalContact.removal_requested_at.is_(None),
            (ExternalContact.last_confirmed_at.is_(None)) | (ExternalContact.last_confirmed_at < cutoff),
        )
        .values(is_active=False)
    )
    await db.commit()
    return result.rowcount or 0
