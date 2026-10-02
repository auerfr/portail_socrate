"""Sauvegarde quotidienne hors-site, à lancer via une tâche planifiée
PythonAnywhere (onglet Tasks) — indépendante de l'état du site web.

Le mécanisme intégré à l'application (app/services/backup.py::weekly_backup_loop)
tourne comme tâche de fond du cycle de vie ASGI, démarrée à chaque redémarrage
du site plutôt que sur un vrai calendrier — peu fiable sur cet hébergement
(cf. incident du 02/10/2026). Ce script fait la même chose (ZIP de la base +
uploads, envoi par e-mail à lodge_settings.admin_email) mais depuis une tâche
planifiée indépendante : il tourne même si le site est en panne.

Usage (PythonAnywhere → Tasks → tâche quotidienne) :
    cd ~/portail-socrate && \
    /home/portailsocrate/.virtualenvs/socrate-env/bin/python scripts/run_backup.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import app.main  # noqa: F401 — enregistre tous les modèles
from app.database import AsyncSessionLocal
from app.services.backup import run_backup
from sqlalchemy import text


async def main():
    async with AsyncSessionLocal() as db:
        row = (await db.execute(text("SELECT admin_email FROM lodge_settings LIMIT 1"))).fetchone()
        admin_email = row[0] if row and row[0] else None

    if not admin_email:
        print("ATTENTION : aucun admin_email configuré dans Paramètres de la loge "
              "— la sauvegarde sera créée localement mais PAS envoyée par e-mail.")

    result = await run_backup(to_email=admin_email)
    print(f"Sauvegarde créée : {result['zip']}")
    print(f"Envoyée par e-mail : {'oui' if result['sent'] else 'non'}"
          + ("" if admin_email else " (pas de destinataire configuré)"))


if __name__ == "__main__":
    asyncio.run(main())
