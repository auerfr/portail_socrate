"""
Migration — ajoute les colonnes manquantes dans lodge_settings
sans toucher aux données existantes.

Usage : python migrate_lodge.py
"""
import asyncio, os, sys
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ".")

from dotenv import load_dotenv
load_dotenv()

from app.config import get_settings

settings = get_settings()

# Colonnes à ajouter : (nom, définition SQL)
COLUMNS = [
    ("vm_member_id",          "INTEGER REFERENCES members(id)"),
    ("secretary_member_id",   "INTEGER REFERENCES members(id)"),
    ("vm_name_display",       "VARCHAR(200)"),
    ("vm_email_display",      "VARCHAR(200)"),
    ("vm_phone",              "VARCHAR(30)"),
    ("secretary_name_display","VARCHAR(200)"),
    ("secretary_email_display","VARCHAR(200)"),
    ("standard_schedule",     "TEXT"),
    ("chantiers_info",        "TEXT"),
    ("common_agenda",         "TEXT"),
    ("temple_name",           "VARCHAR(300)"),
    ("temple_note",           "VARCHAR(300)"),
    ("loge_number",           "VARCHAR(20)"),
]


async def migrate():
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncConnection
    from sqlalchemy import text

    engine = create_async_engine(settings.database_url, echo=False)

    async with engine.begin() as conn:
        # Récupérer les colonnes existantes
        result = await conn.execute(text("PRAGMA table_info(lodge_settings)"))
        existing = {row[1] for row in result.fetchall()}
        print(f"Colonnes existantes : {sorted(existing)}")

        added = []
        for col_name, col_def in COLUMNS:
            if col_name not in existing:
                sql = f"ALTER TABLE lodge_settings ADD COLUMN {col_name} {col_def}"
                await conn.execute(text(sql))
                added.append(col_name)
                print(f"  OK ajout : {col_name}")
            else:
                print(f"  deja presente : {col_name}")

        if not added:
            print("Aucune colonne a ajouter - base a jour.")
        else:
            print(f"\n{len(added)} colonne(s) ajoutee(s) : {added}")

    await engine.dispose()
    print("\nMigration terminee. Relancez uvicorn.")


if __name__ == "__main__":
    asyncio.run(migrate())
