"""Script de dédoublonnage des visiteurs (Visitor) en base SQLite.

Utilise sqlite3 directement (synchrone) avec timeout=30s pour éviter
les conflits de verrou avec l'app web en cours.

Usage :
    python scripts/dedup_visitors.py [--dry-run]
"""
import argparse
import sqlite3
import sys
from pathlib import Path

# Chemin DB depuis .env ou valeur par défaut
def _get_db_path() -> str:
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].strip().strip('"').strip("'")
                # sqlite+aiosqlite:////path/to/file.db → /path/to/file.db
                if "sqlite" in url:
                    # sqlite+aiosqlite:////abs/path → /abs/path (garder le / initial)
                    path = url.split("///", 1)[-1]
                    return path if path.startswith("/") else "/" + path
    return "/home/portailsocrate/portail-socrate/socrate_prod.db"


def run(dry_run: bool = False) -> None:
    db_path = _get_db_path()
    print(f"Base : {db_path}")

    # timeout=30 : attend jusqu'à 30s si la DB est verrouillée
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # Trouver les doublons (même nom insensible à la casse)
    cur.execute("""
        SELECT lower(last_name) AS ln, lower(first_name) AS fn, count(*) AS cnt
        FROM visitors
        GROUP BY lower(last_name), lower(first_name)
        HAVING count(*) > 1
    """)
    dupes = cur.fetchall()

    if not dupes:
        print("Aucun doublon trouvé.")
        con.close()
        return

    def richness(row) -> int:
        return sum(1 for f in (row["lodge_name"], row["orient_city"], row["obedience"],
                               row["email"], row["phone"], row["masonic_grade"])
                   if f and str(f).strip())

    total_merged = 0
    for d in dupes:
        cur.execute("""
            SELECT * FROM visitors
            WHERE lower(last_name) = ? AND lower(first_name) = ?
            ORDER BY id
        """, (d["ln"], d["fn"]))
        visitors = cur.fetchall()

        best = max(visitors, key=lambda v: (richness(v), -v["id"]))
        to_delete = [v for v in visitors if v["id"] != best["id"]]

        print(f"\n{'[DRY-RUN] ' if dry_run else ''}Doublon : {best['last_name']} {best['first_name']}")
        print(f"  → Conservé : id={best['id']}  loge={best['lodge_name']}  orient={best['orient_city']}")
        for v in to_delete:
            print(f"  ✗ Supprimé : id={v['id']}  loge={v['lodge_name']}  orient={v['orient_city']}")

        if not dry_run:
            for v in to_delete:
                # Réattribuer les MeetingVisitor sans conflit
                cur.execute("""
                    UPDATE meeting_visitors SET visitor_id = ?
                    WHERE visitor_id = ?
                    AND meeting_id NOT IN (
                        SELECT meeting_id FROM meeting_visitors WHERE visitor_id = ?
                    )
                """, (best["id"], v["id"], best["id"]))

                # Supprimer les MV résiduels (conflits)
                cur.execute("DELETE FROM meeting_visitors WHERE visitor_id = ?", (v["id"],))

                # Supprimer le Visitor doublon
                cur.execute("DELETE FROM visitors WHERE id = ?", (v["id"],))

            total_merged += len(to_delete)

    if not dry_run:
        con.commit()
        print(f"\n✓ {total_merged} visiteur(s) doublon(s) supprimé(s).")
    else:
        print(f"\n[DRY-RUN] {sum(1 for d in dupes)} groupe(s) de doublons détectés.")

    con.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dédoublonnage des visiteurs")
    parser.add_argument("--dry-run", action="store_true", help="Aperçu sans modification")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
