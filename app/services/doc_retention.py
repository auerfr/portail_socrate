"""Service — suppression automatique des fichiers de la Bibliothèque par dossier.

Un dossier peut définir DocFolder.auto_delete_after_days (ex : le dossier
« Harmonie » où les MP3 déposés n'ont pas vocation à s'accumuler indéfiniment).
Chaque jour :
  1. les documents non encore supprimés de ce dossier, plus vieux que le délai,
     passent à la Corbeille (comme un clic manuel sur « Corbeille ») avec
     Document.auto_deleted = True ;
  2. les documents auto-mis-à-la-corbeille depuis plus de RETENTION_GRACE_DAYS
     sont purgés définitivement (fichier + ligne en base).

Seuls les documents marqués auto_deleted sont concernés par la purge
automatique — un document mis à la Corbeille manuellement par un admin n'est
jamais supprimé tout seul, comme aujourd'hui.
"""
import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select

logger = logging.getLogger(__name__)

RETENTION_GRACE_DAYS = 15  # délai en Corbeille avant purge définitive


async def run_retention_once() -> tuple[int, int]:
    """Applique la politique de rétention une fois. Retourne (mis_en_corbeille, purgés)."""
    from app.database import AsyncSessionLocal
    from app.models.documents import DocFolder, Document

    trashed_count = 0
    purged_count = 0
    now = datetime.now()

    async with AsyncSessionLocal() as db:
        # ── 1. Mise en Corbeille automatique ────────────────────────────────
        folders_r = await db.execute(
            select(DocFolder).where(DocFolder.auto_delete_after_days.is_not(None))
        )
        folders = folders_r.scalars().all()
        for folder in folders:
            if not folder.auto_delete_after_days or folder.auto_delete_after_days <= 0:
                continue
            threshold = now - timedelta(days=folder.auto_delete_after_days)
            docs_r = await db.execute(
                select(Document).where(
                    Document.folder_id == folder.id,
                    Document.deleted_at.is_(None),
                    Document.created_at <= threshold,
                )
            )
            for doc in docs_r.scalars().all():
                doc.deleted_at = now
                doc.auto_deleted = True
                trashed_count += 1
        if trashed_count:
            await db.commit()

        # ── 2. Purge définitive après le délai de grâce ─────────────────────
        purge_threshold = now - timedelta(days=RETENTION_GRACE_DAYS)
        purge_r = await db.execute(
            select(Document).where(
                Document.auto_deleted.is_(True),
                Document.deleted_at.is_not(None),
                Document.deleted_at <= purge_threshold,
            )
        )
        for doc in purge_r.scalars().all():
            if doc.storage_path:
                try:
                    Path(doc.storage_path).unlink(missing_ok=True)
                except Exception:
                    pass
            await db.delete(doc)
            purged_count += 1
        if purged_count:
            await db.commit()

    if trashed_count or purged_count:
        logger.info(
            "Rétention Bibliothèque : %d document(s) mis en Corbeille, %d purgé(s) définitivement",
            trashed_count, purged_count,
        )
    return trashed_count, purged_count


async def daily_retention_loop():
    """Boucle asyncio : chaque jour à ~4h du matin, applique la rétention."""

    async def _sleep_until_4am():
        now = datetime.now()
        next_4 = now.replace(hour=4, minute=0, second=0, microsecond=0)
        if now >= next_4:
            next_4 += timedelta(days=1)
        wait = (next_4 - now).total_seconds()
        logger.info("Prochaine purge rétention Bibliothèque dans %.0f s (%s)", wait, next_4.strftime("%d/%m %H:%M"))
        await asyncio.sleep(wait)

    await asyncio.sleep(45)  # petit délai au démarrage
    while True:
        await _sleep_until_4am()
        try:
            await run_retention_once()
        except Exception as exc:
            logger.error("Erreur boucle rétention Bibliothèque : %s", exc, exc_info=True)
