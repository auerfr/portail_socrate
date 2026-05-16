"""
Backup quotidien — Portail Socrate
Tâche planifiée PythonAnywhere (Daily à 3h00) :
  /home/portailsocrate/.virtualenvs/socrate-env/bin/python /home/portailsocrate/portail-socrate/run_backup.py
"""
import sys, os
sys.path.insert(0, '/home/portailsocrate/portail-socrate')
os.chdir('/home/portailsocrate/portail-socrate')

from dotenv import load_dotenv
load_dotenv('/home/portailsocrate/portail-socrate/.env')

from app.services.backup import create_backup_zip
from datetime import datetime

try:
    zip_path = create_backup_zip()
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] Backup OK → {zip_path}")
except Exception as e:
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] Backup ERREUR : {e}")
    raise
