"""
Backup quotidien — Portail Socrate
Tâche planifiée PythonAnywhere (Daily à 3h00) :
  /home/portailsocrate/.virtualenvs/socrate-env/bin/python /home/portailsocrate/portail-socrate/run_backup.py

Crée le ZIP (base + uploads) ET l'envoie par e-mail à lodge_settings.admin_email
— jusqu'au 02/10/2026 ce script ne faisait que le ZIP local, sans aucune copie
hors-site : aucun filet de sécurité en cas de panne du serveur lui-même.
"""
import asyncio
import sys, os
sys.path.insert(0, '/home/portailsocrate/portail-socrate')
os.chdir('/home/portailsocrate/portail-socrate')

from dotenv import load_dotenv
load_dotenv('/home/portailsocrate/portail-socrate/.env')

import app.main  # noqa: F401 — enregistre tous les modèles avant toute requête DB
from app.database import AsyncSessionLocal
from app.services.backup import run_backup
from sqlalchemy import text
from datetime import datetime


async def main():
    async with AsyncSessionLocal() as db:
        row = (await db.execute(text("SELECT admin_email FROM lodge_settings LIMIT 1"))).fetchone()
        admin_email = row[0] if row and row[0] else None

    result = await run_backup(to_email=admin_email)
    sent = "envoyé" if result["sent"] else ("pas de destinataire configuré" if not admin_email else "ÉCHEC envoi")
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] Backup OK → {result['zip']} — e-mail : {sent}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"[{datetime.now():%Y-%m-%d %H:%M}] Backup ERREUR : {e}")
        raise
