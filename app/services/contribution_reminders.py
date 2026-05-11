"""Rappels J-3 avant clôture de l'appel à tranche.

Push notification quotidienne aux FF/SS qui n'ont pas encore choisi leur
tranche, à partir de J-3 avant la date de clôture (`tier_selection_closes_at`).
"""
import asyncio
from datetime import datetime, date, timedelta

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.finance import (
    ContributionConfig, MemberContribution,
)
from app.models.identity import Member, MemberStatus


async def _run_once() -> int:
    """Envoie les rappels nécessaires aujourd'hui. Retourne le nombre envoyé."""
    today = date.today()
    sent = 0

    async with AsyncSessionLocal() as s:
        # Configs avec appel ouvert + fenêtre fermant dans ≤ 3 jours
        r = await s.execute(
            select(ContributionConfig).where(
                ContributionConfig.tier_selection_open == True,  # noqa: E712
                ContributionConfig.tier_selection_closes_at.isnot(None),
            )
        )
        configs = r.scalars().all()

        try:
            from app.services.push import send_push_to_member
        except Exception:
            send_push_to_member = None

        for cfg in configs:
            closes = cfg.tier_selection_closes_at
            if not closes:
                continue
            days_left = (closes - today).days
            if days_left < 0 or days_left > 3:
                continue  # hors fenêtre de rappel

            # Membres actifs sans contribution pour cette année
            non_repondants = (await s.execute(
                select(Member)
                .where(Member.status == MemberStatus.ACTIVE)
                .where(~Member.id.in_(
                    select(MemberContribution.member_id).where(
                        MemberContribution.masonic_year_id == cfg.masonic_year_id
                    )
                ))
            )).scalars().all()

            if not send_push_to_member:
                continue

            urgency = (
                "⏰" if days_left > 1
                else "🚨" if days_left == 1
                else "⌛"
            )
            for m in non_repondants:
                try:
                    msg_days = (
                        f"plus que {days_left} jour{'s' if days_left > 1 else ''}"
                        if days_left > 0 else "dernier jour"
                    )
                    await send_push_to_member(
                        s, m.id,
                        f"{urgency} Appel à tranche : {msg_days}",
                        f"Clôture le {closes.strftime('%d/%m/%Y')} — choisissez votre tranche "
                        f"depuis « Ma cotisation ».",
                        "/finance/cotisations",
                    )
                    sent += 1
                except Exception:
                    pass

    return sent


async def daily_contribution_reminder_loop():
    """Lance _run_once une fois par jour à ~09h00."""
    while True:
        try:
            now = datetime.now()
            target = now.replace(hour=9, minute=0, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            wait_s = (target - now).total_seconds()
            await asyncio.sleep(max(60, wait_s))
            await _run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(3600)
