"""Avertir le(s) Maître(s) des Banquets dès qu'une nouvelle inscription aux
agapes arrive (auto-inscription via le lien public désormais partageable par
tout membre, ou ajout par un officier depuis Émarger) — il a des délais de
préparation (commande traiteur…) et ne doit pas découvrir les chiffres au
dernier moment en ouvrant la page lui-même."""
import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import LodgeFunction, Member, MemberResponsibility, MemberStatus, ResponsibilityType
from app.models.meetings import Meeting
from app.services.email import _send_raw
from app.services.push import send_push_broadcast

logger = logging.getLogger(__name__)


async def banquet_master_ids(db: AsyncSession, *, exclude_member_id: int | None = None) -> list[int]:
    """Office principal OU cumulé (OFFICE_SECOND) — même règle que
    app.routers.meetings._can_manage_banquet."""
    r1 = await db.execute(
        select(Member.id).where(
            Member.status == MemberStatus.ACTIVE,
            Member.lodge_function == LodgeFunction.MAITRE_BANQUETS,
        )
    )
    r2 = await db.execute(
        select(MemberResponsibility.member_id)
        .join(Member, Member.id == MemberResponsibility.member_id)
        .where(
            Member.status == MemberStatus.ACTIVE,
            MemberResponsibility.type == ResponsibilityType.OFFICE_SECOND,
            MemberResponsibility.lodge_function == LodgeFunction.MAITRE_BANQUETS,
            MemberResponsibility.is_active == True,  # noqa: E712
        )
    )
    ids = {row[0] for row in r1.all()} | {row[0] for row in r2.all()}
    ids.discard(exclude_member_id)
    return list(ids)


def _agape_notify_html(visitor_label: str, meeting_label: str, closes_label: str, banquet_url: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 16px;">
    <tr><td align="center">
      <table width="520" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08);">
        <tr><td style="background:#1a5252;padding:24px 28px;">
          <h1 style="margin:0;color:#fff;font-size:19px;">🍽 Nouvelle inscription aux agapes</h1>
        </td></tr>
        <tr><td style="padding:24px 28px;">
          <p style="margin:0 0 16px;font-size:15px;color:#111827;"><strong>{visitor_label}</strong> vient de s'inscrire aux agapes de la tenue <strong>{meeting_label}</strong>.</p>
          <p style="margin:0 0 20px;font-size:13px;color:#6b7280;">Les inscriptions se clôturent {closes_label} — pensez à vérifier régulièrement le nombre de couverts plutôt qu'en dernière minute.</p>
          <a href="{banquet_url}" style="background:#1a5252;color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none;font-size:14px;font-weight:600;">Voir la liste des agapes →</a>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


async def notify_new_agape_registration(
    db: AsyncSession,
    meeting: Meeting,
    visitor_label: str,
    portal_base_url: str,
    *,
    actor_member_id: int | None = None,
) -> None:
    """Fire-and-forget : ne doit jamais faire échouer l'inscription elle-même."""
    try:
        ids = await banquet_master_ids(db, exclude_member_id=actor_member_id)
        if not ids:
            return

        meeting_label = meeting.meeting_date.strftime("%d/%m/%Y")
        closes_label = (
            f"le {meeting.registration_closes_at.strftime('%d/%m à %Hh%M')}"
            if meeting.registration_closes_at else "la veille de la tenue au plus tard"
        )
        banquet_url = f"{portal_base_url.rstrip('/')}/meetings/{meeting.id}/banquet"

        await send_push_broadcast(
            db, ids, "🍽 Nouvelle inscription agapes",
            f"{visitor_label} — tenue du {meeting_label}",
            f"/meetings/{meeting.id}/banquet",
        )

        r = await db.execute(select(Member.email).where(Member.id.in_(ids), Member.email.isnot(None)))
        emails = [e for (e,) in r.all() if e]
        if emails:
            html = _agape_notify_html(visitor_label, meeting_label, closes_label, banquet_url)
            text = (
                f"{visitor_label} vient de s'inscrire aux agapes de la tenue du {meeting_label}.\n"
                f"Les inscriptions se clôturent {closes_label}.\n{banquet_url}"
            )

            async def _send_all():
                for email in emails:
                    try:
                        await _send_raw(
                            to=email, subject="[Portail Loge] Nouvelle inscription aux agapes",
                            html=html, text=text,
                        )
                    except Exception:
                        logger.exception("Échec notif agape → %s", email)

            asyncio.create_task(_send_all())  # noqa — fire & forget
    except Exception:
        logger.exception("notify_new_agape_registration a échoué (inscription non bloquée)")
