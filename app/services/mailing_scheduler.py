"""Boucle de planification des envois différés."""
import asyncio
from datetime import datetime, timedelta

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.mailing import MailingCampaign, CampaignStatus


async def mailing_scheduler_loop():
    """Vérifie toutes les 60 s s'il y a des campagnes à envoyer."""
    while True:
        try:
            await asyncio.sleep(60)
            now = datetime.utcnow()
            async with AsyncSessionLocal() as s:
                r = await s.execute(
                    select(MailingCampaign).where(
                        MailingCampaign.status == CampaignStatus.DRAFT,
                        MailingCampaign.scheduled_at.isnot(None),
                        MailingCampaign.scheduled_at <= now,
                    )
                )
                due = r.scalars().all()
                for camp in due:
                    from app.services.mailing import launch_send_task
                    camp.scheduled_at = None  # évite double déclenchement
                    await s.commit()
                    launch_send_task(camp.id)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(60)
