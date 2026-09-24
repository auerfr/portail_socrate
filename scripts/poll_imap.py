"""Relève la boîte IMAP une fois (planches + transfert personnel) et quitte.

À programmer en tâche planifiée PythonAnywhere (onglet "Tasks"), car le
cycle de vie ASGI (lifespan FastAPI, qui démarre normalement la boucle
continue de 15 min — planche_import_loop) ne se déclenche pas de façon
fiable sur un hébergement web WSGI classique comme PythonAnywhere — même
constat que pour les migrations, cf. scripts/migrate.py. Sans tâche
planifiée, seul le bouton "Importer maintenant" (Réglages → IMAP) déclenche
un relevé, ce qui explique pourquoi l'import ne semblait se produire qu'en
cliquant dessus.

Usage (à programmer par ex. toutes les 15-20 min selon les fenêtres
disponibles sur votre plan PythonAnywhere — certains plans limitent la
fréquence minimale des tâches planifiées) :
    python scripts/poll_imap.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Importer app.main (et non juste app.services.planche_importer) pour que
# tous les modèles soient enregistrés sur Base.metadata avant tout accès DB —
# même raison que dans scripts/migrate.py.
import app.main  # noqa: F401
from app.services.planche_importer import run_once


async def main():
    n = await run_once()
    print(f"Relevé IMAP terminé : {n} planche(s) importée(s) dans la GED (voir logs applicatifs pour le détail des imports de transfert personnel).")


if __name__ == "__main__":
    asyncio.run(main())
