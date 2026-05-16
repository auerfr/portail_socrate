"""
Script de tâches planifiées — Portail Socrate
À lancer comme always-on task sur PythonAnywhere :
  /home/portailsocrate/.virtualenvs/socrate-env/bin/python /home/portailsocrate/portail-socrate/run_scheduler.py

Regroupe en un seul processus asyncio :
  - Anniversaires maçonniques (email J-1, tous les jours à 7h)
  - Rappels cotisations (push, tous les jours à 9h)
  - Rappels tâches projets (push, tous les jours à 8h)
  - Scheduler mailing (envoi campagnes programmées, toutes les 60s)
"""
import sys
import os
import asyncio
import logging

# ── Chemin projet ────────────────────────────────────────────────────────────
sys.path.insert(0, '/home/portailsocrate/portail-socrate')
os.chdir('/home/portailsocrate/portail-socrate')

# ── Variables d'environnement ────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv('/home/portailsocrate/portail-socrate/.env')

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger('scheduler')

# ── Initialisation base de données ───────────────────────────────────────────
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.config import get_settings
from app import database as _db

_s = get_settings()
_db.engine = create_async_engine(
    _s.database_url, poolclass=NullPool,
    connect_args={"check_same_thread": False, "timeout": 30},
)
_db.AsyncSessionLocal = async_sessionmaker(
    _db.engine, class_=AsyncSession, expire_on_commit=False
)

# ── Callbacks pour le service anniversaires ───────────────────────────────────
async def _get_active_members():
    from sqlalchemy import select
    from app.models.identity import Member, MemberStatus
    async with _db.AsyncSessionLocal() as session:
        r = await session.execute(
            select(Member).where(Member.status == MemberStatus.ACTIVE)
        )
        return r.scalars().all()

async def _get_lodge_name():
    from sqlalchemy import select
    from app.models.lodge import LodgeSettings
    async with _db.AsyncSessionLocal() as session:
        r = await session.execute(select(LodgeSettings).limit(1))
        lodge = r.scalar_one_or_none()
        return lodge.name if lodge else "Socrate Raison et Progrès"

# ── Boucle principale ─────────────────────────────────────────────────────────
async def _heartbeat():
    """Log un signe de vie toutes les heures."""
    while True:
        await asyncio.sleep(3600)
        logger.info("♥ Scheduler actif — anniversaires / cotisations / projets / mailing")


async def main():
    logger.info("Démarrage du scheduler Portail Socrate")
    logger.info("  → Anniversaires maçonniques  : tous les jours à 07h00")
    logger.info("  → Rappels projets            : tous les jours à 08h00")
    logger.info("  → Rappels cotisations        : tous les jours à 09h00")
    logger.info("  → Mailing scheduler          : toutes les 60 secondes")

    from app.services.anniversaires import daily_anniversary_loop
    from app.services.contribution_reminders import daily_contribution_reminder_loop
    from app.services.projects_reminders import daily_task_reminder_loop
    from app.services.mailing_scheduler import mailing_scheduler_loop
    from app.services.planche_importer import planche_import_loop

    await asyncio.gather(
        daily_anniversary_loop(_get_active_members, _get_lodge_name),
        daily_contribution_reminder_loop(),
        daily_task_reminder_loop(),
        mailing_scheduler_loop(),
        planche_import_loop(),
        _heartbeat(),
        return_exceptions=True,   # une tâche qui plante n'arrête pas les autres
    )

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Scheduler arrêté")
