"""Répare l'incident du 12/11/2026 : le premier clic sur « Appliquer » (désactiver
les non-répondants) a désactivé 273 correspondants externes sur 286.

Cause : confirmation_sent_at avait été rempli par un backfill (voir git log,
app/migrations.py) basé sur l'historique email_logs — qui ne contenait qu'un
envoi ancien et ponctuel de la campagne de confirmation (test ou envoi unique,
il y a bien plus de 60 jours). Résultat : la quasi-totalité des contacts
passaient pour « sans réponse depuis 60 jours » alors qu'ils n'avaient jamais
reçu de campagne RÉCENTE. Le backfill a été retiré de app/migrations.py.

Ce script :
  1. Réactive tous les contacts désactivés automatiquement (is_active=0,
     removal_requested_at NULL) — ne touche JAMAIS un contact qui a demandé
     explicitement sa désinscription (removal_requested_at renseigné).
  2. Remet confirmation_sent_at à NULL pour tous les contacts, pour que le
     délai de 60 jours ne reparte qu'à partir du PROCHAIN vrai envoi de
     « Envoyer la confirmation annuelle ».

Usage :
    python scripts/fix_contact_confirmation_rollback.py [--dry-run]
"""
import argparse
import sqlite3
from pathlib import Path


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


def run(dry_run: bool = False) -> None:
    db_path = _get_db_path()
    print(f"Base : {db_path}")

    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    cur.execute("""
        SELECT id, name, email FROM external_contacts
        WHERE is_active = 0 AND removal_requested_at IS NULL
    """)
    to_reactivate = cur.fetchall()

    cur.execute("""
        SELECT count(*) FROM external_contacts WHERE confirmation_sent_at IS NOT NULL
    """)
    n_to_reset = cur.fetchone()[0]

    print(f"\n{len(to_reactivate)} contact(s) à réactiver (désactivés automatiquement, "
          f"jamais de demande explicite de désinscription) :")
    for c in to_reactivate:
        print(f"  ✓ id={c['id']}  {c['name']}  <{c['email']}>")

    print(f"\n{n_to_reset} contact(s) auront confirmation_sent_at remis à NULL "
          f"(le délai de 60 jours ne repartira qu'au prochain vrai envoi).")

    if dry_run:
        print("\n[DRY-RUN] Aucune modification appliquée.")
        con.close()
        return

    cur.execute("""
        UPDATE external_contacts SET is_active = 1
        WHERE is_active = 0 AND removal_requested_at IS NULL
    """)
    cur.execute("UPDATE external_contacts SET confirmation_sent_at = NULL")
    con.commit()
    con.close()
    print(f"\n{len(to_reactivate)} contact(s) réactivé(s), "
          f"{n_to_reset} date(s) de campagne remise(s) à zéro.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Affiche sans modifier")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
