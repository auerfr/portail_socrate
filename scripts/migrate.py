"""Applique les migrations de schéma (tables + colonnes manquantes) et le
mode WAL SQLite, sans démarrer le serveur.

À lancer manuellement après chaque `git pull` en production. Nécessaire sur
les hébergements où le cycle de vie ASGI (lifespan FastAPI) ne se déclenche
pas au redémarrage de l'app (ex: PythonAnywhere en web app WSGI classique) —
sans ça, les migrations normalement automatiques au démarrage ne s'appliquent
jamais (colonnes manquantes) et le mode WAL n'est jamais activé (la base
reste en mode "delete" par défaut, qui bloque les lectures pendant une
écriture — cause probable de lenteurs intermittentes).

Usage :
    python scripts/migrate.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Importer app.main (plutôt que juste app.database/app.migrations) pour que
# TOUS les modèles soient enregistrés sur Base.metadata avant le create_all —
# main.py importe explicitement les modèles qui ne sont pas déjà couverts par
# app/models/__init__.py (messagerie, GED, chat, mailing, etc.).
import app.main  # noqa: F401
from app.database import engine, Base
from app.migrations import run_lightweight_migrations, ensure_wal_mode


async def main():
    await ensure_wal_mode(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await run_lightweight_migrations(engine)

    async with engine.begin() as conn:
        mode = (await conn.exec_driver_sql("PRAGMA journal_mode")).scalar()
    print(f"Migrations appliquées avec succès. journal_mode = {mode}")


if __name__ == "__main__":
    asyncio.run(main())
