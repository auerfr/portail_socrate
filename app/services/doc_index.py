"""Service d'indexation full-text des documents GED.

Utilise SQLite FTS5 pour une recherche rapide sans dépendance externe.
L'extraction de texte supporte : PDF (pdfminer), Word .docx (python-docx),
texte brut, et fournit un résultat vide pour les autres formats (images…).
"""
import logging
from pathlib import Path
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  Extraction de texte selon le type MIME
# ─────────────────────────────────────────────────────────────────────────────

def extract_text(storage_path: str, mime_type: Optional[str]) -> str:
    """Retourne le texte brut extrait d'un fichier. Jamais d'exception."""
    if not storage_path:
        return ""
    path = Path(storage_path)
    if not path.exists():
        return ""
    try:
        if mime_type == "application/pdf" or path.suffix.lower() == ".pdf":
            return _extract_pdf(path)
        if mime_type in (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ) or path.suffix.lower() == ".docx":
            return _extract_docx(path)
        if mime_type and mime_type.startswith("text/"):
            return path.read_text(errors="ignore")[:50_000]
    except Exception as e:
        logger.debug("Extraction texte %s : %s", path.name, e)
    return ""


def _extract_pdf(path: Path) -> str:
    from pdfminer.high_level import extract_text as pdf_extract
    try:
        return (pdf_extract(str(path)) or "")[:100_000]
    except Exception:
        return ""


def _extract_docx(path: Path) -> str:
    try:
        from docx import Document as DocxDoc
        d = DocxDoc(str(path))
        return "\n".join(p.text for p in d.paragraphs)[:100_000]
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────────────────
#  Table FTS5
# ─────────────────────────────────────────────────────────────────────────────

CREATE_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS doc_fts USING fts5(
    doc_id UNINDEXED,
    title,
    body,
    tokenize = 'unicode61 remove_diacritics 2'
)
"""


async def ensure_fts_table(db: AsyncSession) -> None:
    """Crée la table FTS5 si elle n'existe pas encore."""
    await db.execute(text(CREATE_FTS))
    await db.commit()


# ─────────────────────────────────────────────────────────────────────────────
#  Index / déindex
# ─────────────────────────────────────────────────────────────────────────────

async def index_document(db: AsyncSession, doc_id: int, title: str, body: str) -> None:
    """Indexe ou ré-indexe un document (upsert via delete + insert)."""
    await db.execute(text("DELETE FROM doc_fts WHERE doc_id = :id"), {"id": doc_id})
    await db.execute(
        text("INSERT INTO doc_fts (doc_id, title, body) VALUES (:id, :t, :b)"),
        {"id": doc_id, "t": title or "", "b": body or ""},
    )
    # Pas de commit ici — la transaction parente le fera


async def remove_document(db: AsyncSession, doc_id: int) -> None:
    """Supprime un document de l'index."""
    await db.execute(text("DELETE FROM doc_fts WHERE doc_id = :id"), {"id": doc_id})


# ─────────────────────────────────────────────────────────────────────────────
#  Recherche
# ─────────────────────────────────────────────────────────────────────────────

async def search_documents(
    db: AsyncSession,
    query: str,
    limit: int = 30,
) -> list[dict]:
    """Retourne les doc_id correspondants + extrait (snippet) triés par pertinence.

    Le snippet est généré par FTS5 (highlight + ellipsis).
    """
    if not query or len(query.strip()) < 2:
        return []

    q = query.strip()
    # Protège contre les injections FTS5 : on échappe les guillemets
    q_safe = q.replace('"', '""')

    try:
        rows = await db.execute(
            text("""
                SELECT
                    doc_id,
                    snippet(doc_fts, 1, '<mark>', '</mark>', '…', 20)  AS snip_title,
                    snippet(doc_fts, 2, '<mark>', '</mark>', '…', 40)  AS snip_body,
                    rank
                FROM doc_fts
                WHERE doc_fts MATCH :q
                ORDER BY rank
                LIMIT :lim
            """),
            {"q": f'"{q_safe}"*', "lim": limit},
        )
        return [
            {
                "doc_id": r[0],
                "snip_title": r[1],
                "snip_body": r[2],
                "rank": r[3],
            }
            for r in rows.fetchall()
        ]
    except Exception as e:
        logger.warning("Erreur FTS5 search '%s': %s", query, e)
        return []


# ─────────────────────────────────────────────────────────────────────────────
#  Ré-indexation complète (tâche admin)
# ─────────────────────────────────────────────────────────────────────────────

async def reindex_all(db: AsyncSession) -> int:
    """Reconstruit l'index complet depuis tous les documents publiés.

    Retourne le nombre de documents indexés.
    Commits par batch de 50 pour libérer le verrou régulièrement et
    permettre aux autres requêtes de passer.
    """
    from app.models.documents import Document, DocStatus
    from sqlalchemy import select

    docs = (await db.execute(
        select(Document).where(
            Document.status == DocStatus.PUBLISHED,
            Document.storage_path.isnot(None),
        )
    )).scalars().all()

    # Vider l'index en une seule opération
    await db.execute(text("DELETE FROM doc_fts"))
    await db.commit()

    BATCH = 50
    count = 0
    for i, doc in enumerate(docs):
        body = extract_text(doc.storage_path, doc.mime_type)
        if body or doc.name:
            await index_document(db, doc.id, doc.name, body)
            count += 1
        # Commit tous les 50 docs → libère le verrou brièvement
        if (i + 1) % BATCH == 0:
            await db.commit()

    await db.commit()  # dernier batch
    return count
