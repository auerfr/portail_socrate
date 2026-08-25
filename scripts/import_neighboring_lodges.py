"""
Import initial du répertoire des loges voisines (annuaire externe).

Charge scripts/data/neighboring_lodges.json (extrait d'un tableur fourni par
l'utilisateur — orient, région, loge, rite, obédience, horaire et rythme
théorique de réunion) dans la table neighboring_lodges. Idempotent : une
loge déjà présente (même orient + nom + horaire) est ignorée, pas dupliquée
— donc sans risque de relancer ce script plusieurs fois. Le triplet inclut
l'horaire car une même loge peut avoir plusieurs tenues théoriques
distinctes (ex : une tenue en semaine + une tenue du dimanche matin).

Usage :
    python scripts/import_neighboring_lodges.py
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import app.main  # noqa: F401 — enregistre tous les modèles sur Base.metadata
from app.database import AsyncSessionLocal, engine, Base
from app.migrations import run_lightweight_migrations, ensure_wal_mode
from app.models.lodges_directory import NeighboringLodge
from sqlalchemy import select


async def main():
    # S'assure que la table existe (utile si le script est lancé avant tout
    # démarrage de l'app / tout scripts/migrate.py sur ce serveur)
    await ensure_wal_mode(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await run_lightweight_migrations(engine)

    data_path = Path(__file__).parent / "data" / "neighboring_lodges.json"
    records = json.loads(data_path.read_text(encoding="utf-8"))

    async with AsyncSessionLocal() as db:
        r = await db.execute(select(
            NeighboringLodge.orient, NeighboringLodge.name, NeighboringLodge.meeting_time,
        ))
        existing = {(row[0], row[1], row[2]) for row in r.all()}

        added = 0
        for rec in records:
            key = (rec["orient"], rec["name"], rec.get("meeting_time"))
            if key in existing:
                continue
            db.add(NeighboringLodge(
                orient=rec["orient"],
                region=rec.get("region"),
                name=rec["name"],
                rite=rec.get("rite"),
                obedience=rec.get("obedience"),
                meeting_time=rec.get("meeting_time"),
                schedule=rec.get("schedule") or None,
            ))
            existing.add(key)
            added += 1

        await db.commit()
        print(f"Import terminé : {added} loge(s) ajoutée(s) sur {len(records)} dans le fichier "
              f"({len(records) - added} déjà présentes, ignorées).")


if __name__ == "__main__":
    asyncio.run(main())
