"""Service d'envoi d'emails via SMTP (aiosmtplib)."""
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import aiosmtplib

from app.config import get_settings

logger = logging.getLogger(__name__)


async def _send_raw(to: str, subject: str, html: str, text: str) -> bool:
    """Envoie un email brut. Retourne True si succès."""
    settings = get_settings()

    if not settings.smtp_host or not settings.smtp_user or not settings.smtp_pass:
        logger.warning("SMTP non configuré — email ignoré (to=%s subject=%s)", to, subject)
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{settings.lodge_name} <{settings.smtp_from}>"
    msg["To"]      = to
    msg["X-Mailer"] = "Portail Socrate"

    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html",  "utf-8"))

    try:
        use_ssl  = settings.smtp_secure == "ssl"
        use_tls  = settings.smtp_secure == "tls"

        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user,
            password=settings.smtp_pass,
            use_tls=use_ssl,       # SSL direct (port 465)
            start_tls=use_tls,     # STARTTLS (port 587)
            timeout=15,            # 15 secondes max
        )
        logger.info("Email envoyé → %s [%s]", to, subject)
        return True, None

    except Exception as exc:
        logger.error("Échec envoi email → %s : %s", to, exc)
        return False, str(exc)


# ── Templates email ────────────────────────────────────────────────────────

def _new_message_html(sender_name: str, subject: str, body_preview: str, portal_url: str, msg_url: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="fr">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 16px;">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08);">

        <!-- Header -->
        <tr>
          <td style="background:linear-gradient(135deg,#2340b0,#3b5bdb);padding:28px 32px;">
            <p style="margin:0;color:rgba(255,255,255,.7);font-size:12px;letter-spacing:.05em;text-transform:uppercase;">
              {portal_url}
            </p>
            <h1 style="margin:8px 0 0;color:#ffffff;font-size:22px;font-weight:700;">
              Nouveau message reçu
            </h1>
          </td>
        </tr>

        <!-- Corps -->
        <tr>
          <td style="padding:28px 32px;">
            <p style="margin:0 0 8px;font-size:14px;color:#6b7280;">De la part de</p>
            <p style="margin:0 0 20px;font-size:18px;font-weight:600;color:#111827;">{sender_name}</p>

            <p style="margin:0 0 8px;font-size:14px;color:#6b7280;">Objet</p>
            <p style="margin:0 0 20px;font-size:16px;font-weight:600;color:#1e3a8a;">{subject}</p>

            <!-- Aperçu du message -->
            <div style="background:#f8faff;border-left:4px solid #3b5bdb;border-radius:0 8px 8px 0;padding:16px 20px;margin-bottom:28px;">
              <p style="margin:0;font-size:14px;color:#374151;line-height:1.6;">{body_preview}</p>
            </div>

            <!-- CTA -->
            <table cellpadding="0" cellspacing="0">
              <tr>
                <td style="background:#2340b0;border-radius:8px;">
                  <a href="{msg_url}"
                     style="display:inline-block;padding:14px 28px;color:#ffffff;font-size:15px;font-weight:600;text-decoration:none;">
                    Lire et répondre sur le portail →
                  </a>
                </td>
              </tr>
            </table>

            <p style="margin:20px 0 0;font-size:12px;color:#9ca3af;">
              Vous recevez cet email car vous êtes membre du portail de la loge.<br>
              Répondez directement sur le portail — ne répondez pas à cet email.
            </p>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="background:#f9fafb;border-top:1px solid #f3f4f6;padding:16px 32px;text-align:center;">
            <p style="margin:0;font-size:11px;color:#9ca3af;">
              Portail interne — Loge Socrate Raison et Progrès &nbsp;·&nbsp;
              <a href="{portal_url}/settings" style="color:#6b7280;text-decoration:none;">Gérer mes notifications</a>
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _new_message_text(sender_name: str, subject: str, body_preview: str, msg_url: str) -> str:
    return f"""Nouveau message sur le portail de la loge
==========================================

De : {sender_name}
Objet : {subject}

--- Aperçu ---
{body_preview}
--------------

Consultez et répondez sur le portail :
{msg_url}

---
Ne répondez pas à cet email — utilisez le portail pour répondre.
"""


# ── Fonctions publiques ────────────────────────────────────────────────────

async def notify_new_message(
    recipient_email: str,
    sender_name: str,
    subject: str,
    body: str,
    message_id: int,
    portal_base_url: str = "https://portail.amisdesocrate.fr",
) -> bool:
    """Envoie une notification email pour un nouveau message interne."""
    msg_url = f"{portal_base_url}/messages/{message_id}"
    preview = body[:200].strip()
    if len(body) > 200:
        preview += "…"

    html = _new_message_html(
        sender_name=sender_name,
        subject=subject,
        body_preview=preview,
        portal_url=portal_base_url,
        msg_url=msg_url,
    )
    text = _new_message_text(
        sender_name=sender_name,
        subject=subject,
        body_preview=preview,
        msg_url=msg_url,
    )

    ok, _ = await _send_raw(
        to=recipient_email,
        subject=f"[Portail Loge] Nouveau message : {subject}",
        html=html,
        text=text,
    )
    return ok
