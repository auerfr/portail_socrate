"""
Applique des coordonnées GPS (niveau ville) au répertoire des loges voisines.

scripts/data/lodge_coordinates.json fait correspondre chaque "orient" à des
coordonnées lat/lon curées manuellement (la géolocalisation automatique via
Nominatim/OpenStreetMap n'est pas joignable depuis l'environnement de build).
Précision : niveau ville, pas l'adresse exacte du temple — un admin peut
affiner une loge via le formulaire d'édition si besoin.

Idempotent : n'écrase pas une latitude/longitude déjà renseignée (ex : déjà
corrigée manuellement par un admin) ; ne touche que les loges sans coordonnées.

Usage :
    python scripts/apply_lodge_coordinates.py
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import app.main  # noqa: F401 — enregistre tous les modèles sur Base.metadata
from app.database import AsyncSessionLocal, engine
from app.migrations import run_lightweight_migrations, ensure_wal_mode
from app.models.lodges_directory import NeighboringLodge
from sqlalchemy import select


async def main():
    await ensure_wal_mode(engine)
    await run_lightweight_migrations(engine)

    data_path = Path(__file__).parent / "data" / "lodge_coordinates.json"
    coords = json.loads(data_path.read_text(encoding="utf-8"))

    async with AsyncSessionLocal() as db:
        r = await db.execute(select(NeighboringLodge))
        lodges = r.scalars().all()

        updated = 0
        for lg in lodges:
            if lg.latitude is not None and lg.longitude is not None:
                continue
            pos = coords.get(lg.orient)
            if not pos:
                continue
            lg.latitude, lg.longitude = pos
            updated += 1

        await db.commit()
        print(f"Coordonnées appliquées : {updated} loge(s) sur {len(lodges)} "
              f"({len(lodges) - updated} déjà positionnées ou sans correspondance).")


if __name__ == "__main__":
    asyncio.run(main())
