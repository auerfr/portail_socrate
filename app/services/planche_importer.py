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

EXTENSIONS_ACCEPTEES = {".pdf", ".doc", ".docx", ".odt", ".txt", ".jpg", ".jpeg", ".png"}
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


async def _import_one(db, msg_bytes: bytes, upload_dir: Path) -> int:
    """Traite un email et importe ses PJ dans la GED. Retourne le nb de fichiers importés."""
    from app.models.documents import Document, DocStatus, DocFolder
    msg = email.message_from_bytes(msg_bytes)
    sender = msg.get("From", "Inconnu")
    subject = msg.get("Subject", "Sans objet")
    received_at = datetime.now()
    folder_name = received_at.strftime("%Y-%m")

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

        # Nettoyage du nom de fichier
        safe_name = "".join(c for c in filename if c.isalnum() or c in "._- ")[:200]
        stored_name = f"{uuid.uuid4().hex}{ext}"
        dest = upload_dir / stored_name
        dest.write_bytes(content)

        doc = Document(
            folder_id=folder.id,
            name=safe_name,
            original_filename=filename,
            storage_path=str(dest),
            file_size=len(content),
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


async def planche_import_loop():
    """Boucle infinie — vérifie les emails toutes les 15 minutes."""
    logger.info("Démarrage service import planches (IMAP, toutes les 15 min)")
    while True:
        try:
            n = await run_once()
            if n:
                logger.info("Import planches : %d fichier(s) ajouté(s) à la GED", n)
        except Exception as e:
            logger.error("planche_import_loop erreur : %s", e)
        await asyncio.sleep(15 * 60)  # 15 minutes
