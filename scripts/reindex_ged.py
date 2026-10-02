"""Ré-indexation full-text de la GED, en ligne de commande.

Utilise sqlite3 directement (busy_timeout généreux) pour cohabiter avec
l'app web sans "database is locked". Affiche la progression document par
document. Aucun timeout uWSGI (hors requête web) → adapté aux gros volumes.

NE PAS activer PRAGMA journal_mode=WAL ici : le stockage PythonAnywhere
(NFS) ne supporte pas son verrouillage par fichier partagé — incident du
02/10/2026, base corrompue en production ("database disk image is
malformed") après qu'un worker a planté en pleine écriture WAL sur NFS.
Voir app/migrations.py::ensure_wal_mode pour le détail.

Usage :
    python scripts/reindex_ged.py
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.doc_index import CREATE_FTS, extract_text


def _get_db_path() -> str:
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].strip().strip('"').strip("'")
                if "sqlite" in url:
                    path = url.split("///", 1)[-1]
                    return path if path.startswith("/") else "/" + path
    return "/home/portailsocrate/portail-socrate/socrate_prod.db"


def run() -> None:
    db_path = _get_db_path()
    print(f"Base : {db_path}\n")

    con = sqlite3.connect(db_path, timeout=60)   # busy_timeout = 60s
    con.execute("PRAGMA busy_timeout=60000")
    cur = con.cursor()

    # Garantir la table FTS
    cur.executescript(CREATE_FTS)

    cur.execute(
        "SELECT id, name, storage_path, mime_type FROM documents "
        "WHERE status = 'PUBLISHED' AND storage_path IS NOT NULL"
    )
    docs = cur.fetchall()
    total = len(docs)
    print(f"{total} document(s) publié(s) à indexer.\n")

    cur.execute("DELETE FROM doc_fts")
    con.commit()

    count = 0
    indexed_text = 0
    for i, (doc_id, name, storage_path, mime_type) in enumerate(docs, 1):
        try:
            body = extract_text(storage_path, mime_type)
        except Exception:
            body = ""
        chars = len(body)
        cur.execute("DELETE FROM doc_fts WHERE doc_id = ?", (doc_id,))
        cur.execute(
            "INSERT INTO doc_fts (doc_id, title, body) VALUES (?, ?, ?)",
            (doc_id, name or "", body or ""),
        )
        count += 1
        if chars:
            indexed_text += 1
        flag = "✓" if chars else "·"
        nm = (name or "")[:55]
        print(f"  [{i}/{total}] {flag} {nm:<55} {chars} car.")
        if i % 25 == 0:
            con.commit()

    con.commit()
    con.close()
    print(f"\n✓ Terminé : {count} document(s) indexé(s), dont {indexed_text} avec du texte.")
    print("  (Les '·' à 0 car. sont des fichiers sans couche texte : images, PDF scannés…)")


if __name__ == "__main__":
    run()
