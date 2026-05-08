"""
Import GED depuis le backup Agora → Portail Socrate

Lit le dump SQL Agora, reconstruit l'arbre de dossiers,
mappe vers les doc_folders Socrate (par nom), copie les fichiers
et insère les enregistrements Document dans socrate_local.db.

Usage:
  python import_ged_agora.py --dry-run    # Simulation, rien n'est modifié
  python import_ged_agora.py              # Import réel
  python import_ged_agora.py --report     # Affiche le mapping dossiers seulement
"""

import argparse
import hashlib
import io
import mimetypes
import os
import re
import shutil
import sqlite3
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

# Force UTF-8 output on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Chemins ─────────────────────────────────────────────────────────────────
BACKUP_DIR  = Path("C:/Users/francois-regis.auer/Documents/BackupAgora_2026-05-05")
SQL_FILE    = BACKUP_DIR / "BackupDatabase_cp1898858p21_amisd1898858_1tfkm.sql"
MODFILE_DIR = BACKUP_DIR / "modFile"
SOCRATE_DB  = Path("C:/Users/francois-regis.auer/Documents/portail-socrate/socrate_local.db")
UPLOADS_DIR = Path("C:/Users/francois-regis.auer/Documents/portail-socrate/uploads/documents")

# ── Helpers ──────────────────────────────────────────────────────────────────

def normalize(s) -> str:
    """Normalise une chaîne pour comparaison : minuscule, sans accents, sans ponctuation."""
    if not isinstance(s, str):
        return ""
    # Décoder les entités HTML (&amp; &#039; etc.)
    import html
    s = html.unescape(s)
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", s.lower())


def parse_sql_table(sql: str, table: str) -> list[tuple]:
    """Extrait les lignes d'une table depuis un dump MySQL (automate d'état)."""
    # Trouver le bloc INSERT correspondant
    start_marker = f"INSERT INTO `{table}` VALUES"
    pos = sql.find(start_marker)
    if pos == -1:
        return []
    pos += len(start_marker)
    # Avancer jusqu'au premier '('
    while pos < len(sql) and sql[pos] != '(':
        pos += 1

    rows = []
    n = len(sql)

    while pos < n and sql[pos] == '(':
        pos += 1  # skip '('
        cols = []
        buf = ""
        in_str = False
        escape = False

        while pos < n:
            ch = sql[pos]

            if escape:
                buf += ch
                escape = False
                pos += 1
                continue

            if ch == '\\' and in_str:
                escape = True
                buf += ch
                pos += 1
                continue

            if ch == "'" and not in_str:
                in_str = True
                pos += 1
                continue

            if ch == "'" and in_str:
                # double-quote escape ''
                if pos + 1 < n and sql[pos + 1] == "'":
                    buf += "'"
                    pos += 2
                    continue
                in_str = False
                pos += 1
                continue

            if in_str:
                buf += ch
                pos += 1
                continue

            # Hors string
            if ch == ',':
                val = buf.strip()
                if val.upper() == 'NULL':
                    cols.append(None)
                else:
                    try:
                        cols.append(int(val))
                    except ValueError:
                        cols.append(val if val else None)
                buf = ""
                pos += 1
                continue

            if ch == ')':
                # Fin du tuple
                val = buf.strip()
                if val.upper() == 'NULL':
                    cols.append(None)
                else:
                    try:
                        cols.append(int(val))
                    except ValueError:
                        cols.append(val if val else None)
                rows.append(tuple(cols))
                pos += 1
                break

            buf += ch
            pos += 1

        # Chercher la prochaine ligne ou fin
        while pos < n and sql[pos] in (' ', '\t', '\n', '\r', ','):
            pos += 1
        if pos >= n or sql[pos] == ';':
            break

    return rows


def build_agora_tree(folders: list[tuple]) -> dict:
    """
    Construit l'arbre de dossiers Agora.
    Retourne dict {folder_id: {"name": str, "parent": int, "children": [ids]}}
    """
    tree = {}
    for row in folders:
        fid, parent, name = row[0], row[1], row[2]
        tree[fid] = {"id": fid, "parent": parent, "name": name or "", "children": []}
    for fid, node in tree.items():
        p = node["parent"]
        if p and p in tree:
            tree[p]["children"].append(fid)
    return tree


def folder_path(tree: dict, fid: int) -> str:
    """Chemin complet d'un dossier Agora (ex: 'Administratif / Secrétariat / Programmes')."""
    parts = []
    current = fid
    visited = set()
    while current and current in tree and current not in visited:
        visited.add(current)
        node = tree[current]
        name = node["name"]
        if name and isinstance(name, str):
            parts.append(name)
        current = node["parent"]
        if not current or current == 0:
            break
    parts.reverse()
    return " / ".join(parts)


def get_socrate_folders(conn: sqlite3.Connection) -> dict[int, dict]:
    """Retourne {folder_id: {name, parent_id, space_id}} pour tous les doc_folders."""
    c = conn.execute("SELECT id, name, parent_id, space_id FROM doc_folders")
    return {r[0]: {"id": r[0], "name": r[1], "parent_id": r[2], "space_id": r[3]} for r in c}


def get_socrate_spaces(conn: sqlite3.Connection) -> dict[int, str]:
    c = conn.execute("SELECT id, name FROM doc_spaces")
    return {r[0]: r[1] for r in c}


def socrate_folder_path(folders: dict, spaces: dict, fid: int) -> str:
    """Chemin complet d'un dossier Socrate."""
    parts = []
    current = fid
    while current and current in folders:
        node = folders[current]
        parts.append(node["name"])
        current = node["parent_id"]
    if current is None and fid in folders:
        sid = folders[fid]["space_id"]
        parts.append(spaces.get(sid, "?"))
    parts.reverse()
    return " / ".join(parts)


def match_folders(agora_tree: dict, socrate_folders: dict, socrate_spaces: dict) -> dict[int, int | None]:
    """
    Mappe chaque dossier Agora → dossier Socrate.
    Stratégies (par ordre de priorité) :
    1. Correspondance exacte normalisée (nom + parent)
    2. Correspondance exacte normalisée (nom seul)
    3. Correspondances manuelles connues (noms différents entre Agora et Socrate)
    4. Fallback : dossier parent Agora → si lui est mappé, utiliser sa cible
       (pour les sous-dossiers par membre non recréés dans Socrate)
    """
    # Correspondances manuelles : normalize(agora_name) → normalize(socrate_name)
    MANUAL = {
        "sceauximage":                "sceauxcommunication",
        "presencesetetativite":       "presencesetactivite",
        "presencesetatvite":          "presencesetactivite",
        "app":                        "rituelsapp",
        "comp":                       "rituelscomp",
        "mmmm":                       "rituelsmmm",
        "reference":                  "referencesetdocumentsdivers",
        "pierredanglesocrate":        "pierredanglesocrate",
        "planchesprogrammes":         "planchesprogrammesexternesocrate",
        "socratereflexionsetprojets": "socratereflexionsprojets",
        "documentsma":                "documentsmaconniques",
    }

    # Index Socrate : normalize(name) → [folder_id, ...]
    socrate_index: dict[str, list[int]] = {}
    for fid, f in socrate_folders.items():
        key = normalize(f["name"])
        socrate_index.setdefault(key, []).append(fid)

    # Espaces Socrate : normalize(space_name) → premier folder_id racine de cet espace
    space_defaults: dict[str, int] = {}
    for sid, sname in socrate_spaces.items():
        key = normalize(sname)
        root_folders = [f for f in socrate_folders.values()
                        if f["space_id"] == sid and f["parent_id"] is None]
        if root_folders:
            space_defaults[key] = root_folders[0]["id"]

    def best_match(key: str, parent_key: str) -> int | None:
        # Correspondance manuelle
        mapped_key = MANUAL.get(key, key)
        candidates = socrate_index.get(mapped_key, [])
        if not candidates:
            # Essayer comme nom d'espace → dossier par défaut de l'espace
            if mapped_key in space_defaults:
                return space_defaults[mapped_key]
            if key in space_defaults:
                return space_defaults[key]
            return None
        if len(candidates) == 1:
            return candidates[0]
        # Ambiguïté → matcher le parent
        for cid in candidates:
            sf = socrate_folders[cid]
            pid = sf["parent_id"]
            if pid and pid in socrate_folders:
                if normalize(socrate_folders[pid]["name"]) == parent_key:
                    return cid
            # Espace comme parent
            sid = sf["space_id"]
            if normalize(socrate_spaces.get(sid, "")) == parent_key:
                return cid
        return candidates[0]

    # Première passe : correspondances directes
    mapping: dict[int, int | None] = {}
    for agora_id, node in agora_tree.items():
        if not isinstance(node.get("name"), str) or not node["name"]:
            mapping[agora_id] = None
            continue
        key = normalize(node["name"])
        parent_node = agora_tree.get(node["parent"], {})
        parent_key = normalize(parent_node.get("name", "") or "")
        mapping[agora_id] = best_match(key, parent_key)

    # Deuxième passe : fallback au parent mappé (pour dossiers par membre, etc.)
    changed = True
    while changed:
        changed = False
        for agora_id, socrate_id in list(mapping.items()):
            if socrate_id is not None:
                continue
            node = agora_tree[agora_id]
            parent_agora = node["parent"]
            if parent_agora and mapping.get(parent_agora) is not None:
                mapping[agora_id] = mapping[parent_agora]
                changed = True

    return mapping


def find_physical_file(agora_folder_id: int, real_name: str) -> Path | None:
    """
    Cherche le fichier physique dans modFile.
    Agora stocke les fichiers dans modFile/{folder_id}/{real_name}
    ou dans des sous-répertoires.
    """
    # Chemin direct
    direct = MODFILE_DIR / str(agora_folder_id) / real_name
    if direct.exists():
        return direct
    # Recherche récursive dans le sous-dossier du folder
    folder_root = MODFILE_DIR / str(agora_folder_id)
    if folder_root.exists():
        for p in folder_root.rglob(real_name):
            return p
    # Recherche globale (plus lente)
    for p in MODFILE_DIR.rglob(real_name):
        return p
    return None


def unique_dest(dest_dir: Path, real_name: str) -> Path:
    """Génère un nom de destination unique basé sur le hash MD5 du nom + timestamp."""
    stem = Path(real_name).stem
    suffix = Path(real_name).suffix
    uid = hashlib.md5(real_name.encode()).hexdigest()[:12]
    return dest_dir / f"{uid}{suffix}"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Simulation sans modification")
    parser.add_argument("--report", action="store_true", help="Rapport de mapping seulement")
    args = parser.parse_args()

    dry = args.dry_run or args.report
    print(f"{'[DRY-RUN] ' if dry else ''}Import GED Agora → Portail Socrate")
    print("=" * 60)

    # Lire le SQL
    print("Lecture du dump SQL Agora…")
    sql = SQL_FILE.read_text(encoding="utf-8", errors="replace")

    folders_raw = parse_sql_table(sql, "ap_fileFolder")
    files_raw   = parse_sql_table(sql, "ap_file")
    versions_raw = parse_sql_table(sql, "ap_fileVersion")

    print(f"  {len(folders_raw)} dossiers, {len(files_raw)} fichiers, {len(versions_raw)} versions")

    # Construire l'arbre Agora
    agora_tree = build_agora_tree(folders_raw)

    # Index versions : file_id → real_name
    # ap_fileVersion: (_idFile, name, realName, octetSize, description, dateCrea, _idUser)
    version_index: dict[int, str] = {}
    for row in versions_raw:
        fid = row[0]
        real_name = row[2] if len(row) > 2 else None
        if fid and real_name and fid not in version_index:
            version_index[fid] = real_name

    # Index fichiers : file_id → (container_id, name, description, size, date)
    # ap_file: (_id, _idContainer, name, description, octetSize, downloadsNb, ..., dateCrea, ...)
    file_index: dict[int, dict] = {}
    for row in files_raw:
        fid = row[0]
        file_index[fid] = {
            "id": fid,
            "container": row[1],
            "name": row[2] or "",
            "description": row[3],
            "size": row[4],
            "date_crea": row[8] if len(row) > 8 else None,
        }

    # Connexion Socrate
    conn = sqlite3.connect(str(SOCRATE_DB))
    socrate_folders = get_socrate_folders(conn)
    socrate_spaces  = get_socrate_spaces(conn)

    # Mapping dossiers
    mapping = match_folders(agora_tree, socrate_folders, socrate_spaces)

    # ── Rapport mapping ────────────────────────────────────────────────────
    print("\n── Mapping dossiers Agora → Socrate ────────────────────────────")
    matched = sum(1 for v in mapping.values() if v is not None)
    print(f"  {matched}/{len(mapping)} dossiers mappés")

    unmatched = []
    for agora_id, socrate_id in sorted(mapping.items()):
        agora_path = folder_path(agora_tree, agora_id)
        if socrate_id:
            socrate_path = socrate_folder_path(socrate_folders, socrate_spaces, socrate_id)
            status = "✓"
        else:
            socrate_path = "— NON MAPPÉ —"
            status = "✗"
            unmatched.append((agora_id, agora_path))
        print(f"  {status} Agora [{agora_id:3d}] {agora_path}")
        if socrate_id:
            print(f"      → Socrate [{socrate_id:3d}] {socrate_path}")

    if unmatched:
        print(f"\n⚠  {len(unmatched)} dossiers Agora sans correspondance Socrate :")
        for aid, ap in unmatched:
            print(f"   [{aid}] {ap}")

    if args.report:
        conn.close()
        return

    # ── Import fichiers ────────────────────────────────────────────────────
    print("\n── Import des fichiers ─────────────────────────────────────────")
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

    stats = {"ok": 0, "skipped_no_mapping": 0, "skipped_no_file": 0,
             "skipped_existing": 0, "error": 0}

    for fid, fdata in sorted(file_index.items()):
        agora_folder = fdata["container"]
        socrate_folder_id = mapping.get(agora_folder)

        if not socrate_folder_id:
            agora_path = folder_path(agora_tree, agora_folder)
            print(f"  SKIP [{fid}] {fdata['name']} — dossier Agora [{agora_folder}] non mappé ({agora_path})")
            stats["skipped_no_mapping"] += 1
            continue

        # Trouver le fichier physique
        real_name = version_index.get(fid)
        if not real_name:
            # Essayer de reconstruire depuis le nom
            print(f"  SKIP [{fid}] {fdata['name']} — aucune version trouvée dans ap_fileVersion")
            stats["skipped_no_file"] += 1
            continue

        phys = find_physical_file(agora_folder, real_name)
        if not phys:
            print(f"  MISS [{fid}] {fdata['name']} — fichier physique introuvable : {real_name}")
            stats["skipped_no_file"] += 1
            continue

        # Vérifier si déjà importé (par original_filename)
        existing = conn.execute(
            "SELECT id FROM documents WHERE original_filename = ?", (fdata["name"],)
        ).fetchone()
        if existing:
            print(f"  DUP  [{fid}] {fdata['name']} — déjà en base (doc_id={existing[0]})")
            stats["skipped_existing"] += 1
            continue

        # Destination
        dest = unique_dest(UPLOADS_DIR, real_name)
        mime = mimetypes.guess_type(fdata["name"])[0] or "application/octet-stream"
        storage_path = f"documents/{dest.name}"

        print(f"  {'[DRY]' if dry else 'COPY'} [{fid}] {fdata['name']}")
        print(f"       → dossier Socrate [{socrate_folder_id}]  fichier: {dest.name}")

        if not dry:
            try:
                shutil.copy2(phys, dest)
                conn.execute(
                    """INSERT INTO documents
                       (folder_id, name, description, original_filename, mime_type,
                        file_size, storage_path, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'PUBLISHED', ?, ?)""",
                    (
                        socrate_folder_id,
                        fdata["name"],
                        fdata["description"],
                        fdata["name"],
                        mime,
                        fdata["size"],
                        storage_path,
                        fdata["date_crea"] or datetime.now().isoformat(),
                        datetime.now().isoformat(),
                    ),
                )
                stats["ok"] += 1
            except Exception as e:
                print(f"       ✗ ERREUR : {e}")
                stats["error"] += 1
        else:
            stats["ok"] += 1

    if not dry:
        conn.commit()

    conn.close()

    print("\n── Résumé ──────────────────────────────────────────────────────")
    print(f"  ✓ Importés       : {stats['ok']}")
    print(f"  ⟳ Déjà présents  : {stats['skipped_existing']}")
    print(f"  ○ Sans mapping   : {stats['skipped_no_mapping']}")
    print(f"  ✗ Fichier absent : {stats['skipped_no_file']}")
    if stats["error"]:
        print(f"  ✗ Erreurs        : {stats['error']}")
    if dry:
        print("\n  ⚡ Mode simulation — aucune modification effectuée.")
        print("     Relancer sans --dry-run pour importer.")


if __name__ == "__main__":
    main()
