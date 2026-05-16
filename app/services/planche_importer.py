"""
Service d'import automatique de planches par email (IMAP).

Polling de la boîte IMAP configurée — chaque email avec pièce jointe
est traité et les PJ sont classées dans la GED sous :
  Espace "Planches reçues" → dossier "AAAA-MM" (créé si besoin)

Les emails traités sont déplacés dans un dossier IMAP "Traité" ou marqués lus.
"""
import asyncio
import email
import imaplib
import logging
import ssl
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

EXTENSIONS_ACCEPTEES = {".pdf", ".doc", ".docx", ".odt", ".rtf", ".jpg", ".jpeg", ".png"}
ESPACE_NOM = "Planches reçues"


async def _get_or_create_space(db, nom: str):
    """Retourne (ou crée) l'espace GED 'Planches reçues'."""
    from sqlalchemy import select
    from app.models.documents import DocSpace, DocAccessMode, MinGrade
    r = await db.execute(select(DocSpace).where(DocSpace.name == nom))
    space = r.scalar_one_or_none()
    if not space:
        space = DocSpace(
            name=nom,
            description="Planches reçues par email d'autres loges",
            access_mode=DocAccessMode.GRADE,
            min_grade=MinGrade.MAITRE,
        )
        db.add(space)
        await db.flush()
    return space


async def _get_or_create_folder(db, space_id: int, folder_name: str):
    """Retourne (ou crée) le dossier mensuel 'AAAA-MM'."""
    from sqlalchemy import select
    from app.models.documents import DocFolder
    r = await db.execute(
        select(DocFolder).where(
            DocFolder.space_id == space_id,
            DocFolder.name == folder_name,
            DocFolder.parent_id.is_(None),
        )
    )
    folder = r.scalar_one_or_none()
    if not folder:
        folder = DocFolder(
            space_id=space_id,
            name=folder_name,
            description=f"Planches reçues en {folder_name}",
        )
        db.add(folder)
        await db.flush()
    return folder


def _decode_header(value: str) -> str:
    """Décode un header email (gère le RFC 2047 / encodages divers)."""
    import email.header
    parts = email.header.decode_header(value or "")
    result = []
    for part, enc in parts:
        if isinstance(part, bytes):
            result.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            result.append(part)
    return " ".join(result)


def _extract_sender_label(from_header: str, body_text: str = "") -> str:
    """Extrait un label court depuis l'expéditeur.
    Pour les emails transférés, cherche le vrai expéditeur dans le corps.
    """
    import re

    # Dans un email transféré, chercher "De :" ou "From :" dans le corps
    if body_text:
        for pattern in [
            r'De\s*:\s*(.+?)(?:\n|<)',
            r'From\s*:\s*(.+?)(?:\n|<)',
            r'Expéditeur\s*:\s*(.+?)(?:\n|<)',
        ]:
            m = re.search(pattern, body_text, re.IGNORECASE)
            if m:
                candidate = m.group(1).strip()
                if candidate and len(candidate) > 3 and '@' not in candidate:
                    return re.sub(r'[^\w\s-]', '', candidate).strip().replace(' ', '_')[:40]
                # Si c'est un email, prendre le domaine
                m2 = re.search(r'@([^>\s]+)', candidate)
                if m2:
                    domain = m2.group(1).split('.')[0]
                    return re.sub(r'[^\w-]', '', domain)[:40]

    # Sinon extraire depuis le From: header
    m = re.match(r'^"?([^"<]+)"?\s*<', from_header)
    if m:
        name = m.group(1).strip()
    else:
        m2 = re.search(r'@([^>]+)', from_header)
        name = m2.group(1).split('.')[0] if m2 else "Externe"
    return re.sub(r'[^\w\s-]', '', name).strip().replace(' ', '_')[:40]


def _get_body_text(msg) -> str:
    """Extrait le texte brut d'un email (pour trouver les infos de transfert)."""
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            payload = part.get_payload(decode=True)
            if payload:
                try:
                    return payload.decode("utf-8", errors="replace")
                except Exception:
                    pass
    return ""


async def _import_one(db, msg_bytes: bytes, upload_dir: Path) -> int:
    """Traite un email et importe ses PJ dans la GED. Retourne le nb de fichiers importés."""
    from app.models.documents import Document, DocStatus, DocFolder
    msg = email.message_from_bytes(msg_bytes)
    sender_raw = msg.get("From", "Inconnu")
    sender = _decode_header(sender_raw)
    subject = _decode_header(msg.get("Subject", "Sans objet"))
    received_at = datetime.now()
    folder_name = received_at.strftime("%Y-%m")
    body_text = _get_body_text(msg)
    sender_label = _extract_sender_label(sender_raw, body_text)
    date_label = received_at.strftime("%Y-%m-%d")

    space = await _get_or_create_space(db, ESPACE_NOM)
    folder = await _get_or_create_folder(db, space.id, folder_name)

    imported = 0
    for part in msg.walk():
        content_disp = part.get("Content-Disposition", "")
        if "attachment" not in content_disp and "inline" not in content_disp:
            continue
        filename = part.get_filename()
        if not filename:
            continue
        ext = Path(filename).suffix.lower()
        if ext not in EXTENSIONS_ACCEPTEES:
            continue

        content = part.get_payload(decode=True)
        if not content:
            continue

        # Nommage enrichi : date + expéditeur + nom original
        base = Path(filename).stem
        safe_base = "".join(c for c in base if c.isalnum() or c in "._- ")[:80]
        safe_name = f"{date_label}_{sender_label}_{safe_base}{ext}" if safe_base else f"{date_label}_{sender_label}{ext}"
        stored_name = f"{uuid.uuid4().hex}{ext}"
        dest = upload_dir / stored_name
        dest.write_bytes(content)

        # Détecter le MIME type
        mime_map = {
            ".pdf": "application/pdf",
            ".doc": "application/msword",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".odt": "application/vnd.oasis.opendocument.text",
            ".rtf": "application/rtf",
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png",
        }
        mime_type = mime_map.get(ext, "application/octet-stream")

        doc = Document(
            folder_id=folder.id,
            name=safe_name,
            original_filename=filename,
            storage_path=str(dest),
            file_size=len(content),
            mime_type=mime_type,
            status=DocStatus.PUBLISHED,
            description=f"Reçu de {sender} — {subject}",
        )
        db.add(doc)
        imported += 1
        logger.info("Planche importée : %s (%d octets) depuis %s", filename, len(content), sender)

    if imported:
        await db.commit()
    return imported


async def run_once(upload_dir: str = "uploads/documents/planches_recues") -> int:
    """Polling IMAP unique. Retourne le nombre total de fichiers importés."""
    from app.config import get_settings
    from app.database import AsyncSessionLocal

    # Charger tous les modèles pour que SQLAlchemy résolve les FK
    import app.models.documents
    import app.models.identity
    import app.models.groups
    import app.models.lodge
    import app.models.meetings

    s = get_settings()
    if not s.imap_host or not s.imap_user or not s.imap_pass:
        logger.debug("IMAP non configuré — import planches ignoré")
        return 0

    dest_dir = Path(upload_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    total = 0
    try:
        ctx = ssl.create_default_context()
        conn = imaplib.IMAP4_SSL(s.imap_host, s.imap_port, ssl_context=ctx)
        conn.login(s.imap_user, s.imap_pass)
        conn.select(s.imap_folder)

        # Chercher les emails non lus avec pièces jointes
        _, uids = conn.search(None, "UNSEEN")
        uid_list = uids[0].split()

        if not uid_list:
            conn.logout()
            return 0

        logger.info("%d email(s) non lu(s) à traiter", len(uid_list))

        async with AsyncSessionLocal() as db:
            for uid in uid_list:
                _, data = conn.fetch(uid, "(RFC822)")
                if not data or not data[0]:
                    continue
                msg_bytes = data[0][1]
                try:
                    n = await _import_one(db, msg_bytes, dest_dir)
                    total += n
                    # Marquer comme lu après traitement
                    conn.store(uid, "+FLAGS", "\\Seen")
                    if n > 0:
                        logger.info("Email uid=%s : %d fichier(s) importé(s)", uid, n)
                except Exception as e:
                    logger.error("Erreur traitement email uid=%s : %s", uid, e)

        conn.logout()

    except Exception as e:
        logger.error("Erreur IMAP planche importer : %s", e)

    return total


async def cleanup_old_planches(max_months: int = 3) -> int:
    """Supprime automatiquement les planches reçues de plus de max_months mois.
    Supprime aussi les dossiers mensuels vides."""
    from app.database import AsyncSessionLocal
    import app.models.documents, app.models.identity, app.models.groups
    import app.models.lodge, app.models.meetings
    from sqlalchemy import select
    from app.models.documents import DocSpace, DocFolder, Document
    from datetime import timedelta

    cutoff = datetime.now() - timedelta(days=30 * max_months)
    deleted = 0

    async with AsyncSessionLocal() as db:
        # Trouver l'espace
        r = await db.execute(select(DocSpace).where(DocSpace.name == ESPACE_NOM))
        space = r.scalar_one_or_none()
        if not space:
            return 0

        # Trouver les dossiers mensuels
        r_folders = await db.execute(
            select(DocFolder).where(DocFolder.space_id == space.id)
        )
        folders = r_folders.scalars().all()

        for folder in folders:
            # Vérifier si le dossier correspond à un mois dépassé (format YYYY-MM)
            try:
                from datetime import datetime as _dt
                folder_date = _dt.strptime(folder.name, "%Y-%m")
                if folder_date > cutoff:
                    continue  # Dossier récent → garder
            except ValueError:
                continue  # Nom non reconnu → ignorer

            # Supprimer les documents du dossier
            r_docs = await db.execute(
                select(Document).where(Document.folder_id == folder.id)
            )
            docs = r_docs.scalars().all()
            for doc in docs:
                # Supprimer le fichier physique
                if doc.storage_path:
                    try:
                        Path(doc.storage_path).unlink(missing_ok=True)
                    except Exception:
                        pass
                await db.delete(doc)
                deleted += 1

            # Supprimer le dossier maintenant vide
            await db.delete(folder)
            logger.info("Dossier %s supprimé (%d fichier(s))", folder.name, len(docs))

        if deleted:
            await db.commit()
            logger.info("Nettoyage planches : %d fichier(s) supprimé(s) (> %d mois)", deleted, max_months)

    return deleted


async def planche_import_loop():
    """Boucle infinie — vérifie les emails toutes les 15 minutes.
    Nettoyage automatique des planches > 3 mois une fois par jour (à 2h)."""
    logger.info("Démarrage service import planches (IMAP, toutes les 15 min)")
    last_cleanup = None

    while True:
        try:
            n = await run_once()
            if n:
                logger.info("Import planches : %d fichier(s) ajouté(s) à la GED", n)
        except Exception as e:
            logger.error("planche_import_loop erreur import : %s", e)

        # Nettoyage quotidien à 2h
        now = datetime.now()
        if now.hour == 2 and (last_cleanup is None or last_cleanup.date() < now.date()):
            try:
                d = await cleanup_old_planches(max_months=3)
                last_cleanup = now
                if d:
                    logger.info("Nettoyage auto : %d planche(s) supprimée(s)", d)
            except Exception as e:
                logger.error("planche_import_loop erreur nettoyage : %s", e)

        await asyncio.sleep(15 * 60)  # 15 minutes
