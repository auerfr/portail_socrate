"""Ré-indexation full-text de la GED, en ligne de commande.

À lancer hors requête web (console / scheduled task) → aucun timeout uWSGI.
Affiche la progression document par document.

Usage :
    /home/portailsocrate/.virtualenvs/socrate-env/bin/python scripts/reindex_ged.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, text
from app.database import AsyncSessionLocal
from app.services.doc_index import ensure_fts_table, extract_text, index_document


async def run() -> None:
    from app.models.documents import Document, DocStatus

    async with AsyncSessionLocal() as db:
        await ensure_fts_table(db)

        docs = (await db.execute(
            select(Document).where(
                Document.status == DocStatus.PUBLISHED,
                Document.storage_path.isnot(None),
            )
        )).scalars().all()

        print(f"{len(docs)} document(s) publié(s) à indexer.\n")

        await db.execute(text("DELETE FROM doc_fts"))
        await db.commit()

        count = 0
        for i, doc in enumerate(docs, 1):
            body = await asyncio.to_thread(extract_text, doc.storage_path, doc.mime_type)
            chars = len(body)
            if body or doc.name:
                await index_document(db, doc.id, doc.name, body)
                count += 1
            flag = "✓" if chars else "·"
            print(f"  [{i}/{len(docs)}] {flag} {doc.name[:60]:<60} {chars} car.")
            if i % 20 == 0:
                await db.commit()

        await db.commit()
        print(f"\n✓ Ré-indexation terminée : {count} document(s) indexé(s).")


if __name__ == "__main__":
    asyncio.run(run())
