"""
Corrige l'adresse du temple GLDF de Nancy : "avenue de la Garenne" (et non
"rue de la Garenne", qui a été géolocalisée à tort sur la commune de
Custines par la géolocalisation automatique — pas de rue de ce nom à
Nancy même). Réinitialise aussi les coordonnées de ces loges sur le
centre de Nancy (elles pointaient vers Custines, ~15 km plus loin).

Contrairement à apply_lodge_addresses.py, ce script écrase l'adresse même
si elle est déjà renseignée (correction d'une valeur erronée), pour les
5 loges de l'orient "NANCY (54) Garenne" uniquement.

Usage :
    python scripts/fix_nancy_garenne_address.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import app.main  # noqa: F401 — enregistre tous les modèles sur Base.metadata
from app.database import AsyncSessionLocal, engine
from app.migrations import run_lightweight_migrations, ensure_wal_mode
from app.models.lodges_directory import NeighboringLodge
from sqlalchemy import select

CORRECT_ADDRESS = "avenue de la Garenne, 54000 Nancy"
NANCY_CENTER = (48.6921, 6.1844)


async def main():
    await ensure_wal_mode(engine)
    await run_lightweight_migrations(engine)

    async with AsyncSessionLocal() as db:
        r = await db.execute(
            select(NeighboringLodge).where(NeighboringLodge.orient == "NANCY (54) Garenne")
        )
        lodges = r.scalars().all()
        for lg in lodges:
            print(f"  {lg.name}: {lg.address!r} ({lg.latitude}, {lg.longitude}) -> "
                  f"{CORRECT_ADDRESS!r} {NANCY_CENTER}")
            lg.address = CORRECT_ADDRESS
            lg.latitude, lg.longitude = NANCY_CENTER

        await db.commit()
        print(f"\n{len(lodges)} loge(s) corrigée(s).")


if __name__ == "__main__":
    asyncio.run(main())
