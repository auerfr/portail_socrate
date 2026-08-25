"""
Applique des adresses de temple connues au répertoire des loges voisines,
par orient (toutes les loges qui se réunissent au même temple partagent
la même adresse).

Idempotent : ne touche que les loges dont l'adresse est vide, pour ne
jamais écraser une adresse déjà saisie/corrigée manuellement.

Usage :
    python scripts/apply_lodge_addresses.py
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

ORIENT_TO_ADDRESS = {
    "METZ (57)": "9b rue Devilly, 57000 Metz",
    "NANCY (54) Drouin": "15 rue Drouin, 54000 Nancy",
    "THIONVILLE (57) Yutz": "9 rue des Métiers, 57970 Yutz",
}


async def main():
    await ensure_wal_mode(engine)
    await run_lightweight_migrations(engine)

    async with AsyncSessionLocal() as db:
        updated = 0
        for orient, address in ORIENT_TO_ADDRESS.items():
            r = await db.execute(
                select(NeighboringLodge).where(
                    NeighboringLodge.orient == orient,
                    NeighboringLodge.address.is_(None),
                )
            )
            lodges = r.scalars().all()
            for lg in lodges:
                lg.address = address
                updated += 1
            print(f"{orient} -> {len(lodges)} loge(s) mise(s) à jour")

        await db.commit()
        print(f"Total : {updated} loge(s) mise(s) à jour.")


if __name__ == "__main__":
    asyncio.run(main())
