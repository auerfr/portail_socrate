"""
Service d'import automatique de planches par email (IMAP).

Polling de la boîte IMAP configurée — chaque email est examiné :
  1. S'il porte le jeton de transfert personnel d'un membre (adresse en
     copie +jeton, ou jeton [XXXX] en tête de l'objet), il est importé
     comme un Message interne pour ce membre, pièce jointe comprise
     (cf. _try_import_as_member_message).
  2. Sinon, s'il a une pièce jointe, elle est classée dans la GED sous :
       Espace "Planches reçues" → dossier "AAAA-MM" (créé si besoin)

Les emails traités sont marqués lus.
"""
import asyncio
import email
import imaplib
import logging
import re
import ssl
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy import func

logger = logging.getLogger(__name__)

EXTENSIONS_ACCEPTEES = {".pdf", ".doc", ".docx", ".odt", ".rtf", ".jpg", ".jpeg", ".png"}
ESPACE_NOM = "Planches reçues"

# ── Import comme message personnel (transfert par jeton) ───────────────────
# Même politique de pièces jointes que le composeur de messages (cf.
# app/routers/messages.py) — dupliquée ici plutôt qu'importée depuis un
# router (un service ne doit pas dépendre d'un router).
MSG_ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".txt"}
MSG_MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 Mo
MSG_UPLOAD_DIR = Path("app/static/uploads/messages")
# Garde-fou anti-abus : au-delà, un email reconnu (jeton valide) est ignoré
# plutôt qu'importé — protège un jeton éventuellement compromis ou une
# boucle de transfert automatique mal configurée côté membre.
MEMBER_IMPORT_DAILY_QUOTA = 20


async def _get_or_create_space(db, nom: str):
    """Retourne (ou crée) l'espace GED 'Planches reçues'. Met à jour min_grade si besoin."""
    from sqlalchemy import select
    from app.models.documents import DocSpace, DocAccessMode, MinGrade
    r = await db.execute(select(DocSpace).where(DocSpace.name == nom))
    space = r.scalar_one_or_none()
    if not space:
        space = DocSpace(
            name=nom,
            description="Planches reçues par email d'autres loges",
            access_mode=DocAccessMode.GRADE,
            min_grade=MinGrade.ALL,
        )
        db.add(space)
        await db.flush()
    elif space.min_grade != MinGrade.ALL:
        space.min_grade = MinGrade.ALL
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


_TOKEN_RE = re.compile(r"^[a-zA-Z0-9_-]{8,64}$")


def _extract_import_token(msg) -> Optional[str]:
    """Cherche le jeton de transfert personnel d'un membre dans cet email.

    Deux emplacements possibles, cherchés dans l'ordre :
    1. Adressage "+jeton" (ex: boite+ab12cd34@domaine.fr) — présent dans
       les en-têtes de destination habituels. Nécessite que le serveur mail
       (LWS) livre bien ce type d'adresse dans la même boîte — à vérifier
       en conditions réelles avant de s'y fier exclusivement.
    2. Repli universel, indépendant du serveur mail : le membre ajoute
       "[jeton]" en tête de l'objet de l'email transféré.
    Retourne le jeton trouvé (sans le nettoyer/valider contre la base — ça,
    c'est le rôle de l'appelant), ou None si rien de reconnaissable.
    """
    for header_name in ("To", "Delivered-To", "X-Original-To", "Envelope-To"):
        for raw in msg.get_all(header_name, []):
            m = re.search(r"\+([a-zA-Z0-9_-]{8,64})@", raw)
            if m:
                return m.group(1)

    subject = _decode_header(msg.get("Subject", ""))
    m = re.match(r"^\s*\[([a-zA-Z0-9_-]{8,64})\]", subject)
    if m:
        return m.group(1)

    return None


def _extract_sender_email(msg) -> Optional[str]:
    """Extrait l'adresse email pure du "De :" (sans le nom affiché)."""
    import email.utils as _eu
    addr = _eu.parseaddr(msg.get("From", ""))[1]
    return addr.strip().lower() or None


async def _create_member_message(db, msg, member) -> bool:
    """Crée un Message interne (expéditeur ET destinataire = member) à partir
    de cet email, avec ses pièces jointes. Le membre est déjà authentifié par
    l'appelant (jeton vérifié en base — soit via le sujet/l'adresse, soit
    implicitement par le nom du dossier IMAP dédié à ce jeton).
    Retourne False sans rien créer si le quota anti-abus est dépassé."""
    from sqlalchemy import select
    from app.models.messaging import Message, MessageAttachment, MessageRecipient, MessageTargetType

    # Quota anti-abus (jeton compromis, transfert en boucle mal configuré…)
    since = datetime.now() - timedelta(hours=24)
    r_count = await db.execute(
        select(func.count(Message.id)).where(
            Message.sender_id == member.id,
            Message.imported_from_email.is_(True),
            Message.created_at >= since,
        )
    )
    if (r_count.scalar() or 0) >= MEMBER_IMPORT_DAILY_QUOTA:
        logger.warning("Quota de transfert email atteint pour le membre #%s — email ignoré", member.id)
        return False

    sender_raw = msg.get("From", "")
    subject = _decode_header(msg.get("Subject", "Sans objet")).strip() or "Sans objet"
    # Retirer un éventuel repli "[jeton]" du sujet s'il y était (adressage
    # +jeton ou dossier dédié : n'apparaît jamais dans le sujet).
    subject = re.sub(r"^\s*\[[a-zA-Z0-9_-]{8,64}\]\s*", "", subject).strip() or "Sans objet"

    body_text = _get_body_text(msg).strip()
    if not body_text:
        body_text = "(email transféré sans contenu texte lisible — voir pièce(s) jointe(s))"
    body_text = f"— Email transféré depuis {_decode_header(sender_raw) or 'expéditeur inconnu'} —\n\n{body_text}"

    received_at = datetime.now()
    date_header = msg.get("Date")
    if date_header:
        try:
            import email.utils as _eu
            parsed = _eu.parsedate_to_datetime(date_header)
            if parsed:
                received_at = parsed.replace(tzinfo=None)
        except Exception:
            pass

    # Corps stocké en texte brut uniquement (body_html laissé vide) : le
    # contenu vient d'un tiers externe non maîtrisé, le rendre en HTML "safe"
    # comme un message composé dans l'app ouvrirait une brèche XSS.
    new_msg = Message(
        subject=subject[:300],
        body=body_text,
        body_html=None,
        sender_id=member.id,
        target_type=MessageTargetType.MANUAL,
        target_filter=f'{{"member_ids": [{member.id}]}}',
        sent_at=received_at,
        imported_from_email=True,
    )
    db.add(new_msg)
    await db.flush()
    db.add(MessageRecipient(
        message_id=new_msg.id,
        member_id=member.id,
        delivered_at=received_at,
    ))

    imported_files = 0
    for part in msg.walk():
        content_disp = part.get("Content-Disposition", "")
        if "attachment" not in content_disp and "inline" not in content_disp:
            continue
        filename = part.get_filename()
        if not filename:
            continue
        filename = _decode_header(filename)
        ext = Path(filename).suffix.lower()
        if ext not in MSG_ALLOWED_EXTENSIONS:
            continue
        content = part.get_payload(decode=True)
        if not content or len(content) > MSG_MAX_FILE_SIZE:
            continue

        MSG_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        stored_name = f"{new_msg.id}_{uuid.uuid4().hex}{ext}"
        (MSG_UPLOAD_DIR / stored_name).write_bytes(content)
        db.add(MessageAttachment(
            message_id=new_msg.id,
            filename=filename,
            stored_name=stored_name,
            mime_type=part.get_content_type() or "application/octet-stream",
            size_bytes=len(content),
        ))
        imported_files += 1

    await db.commit()
    logger.info(
        "Email importé comme message pour le membre #%s : « %s » (%d pièce(s) jointe(s))",
        member.id, subject, imported_files,
    )

    try:
        from app.models.system import Notification, NotificationType
        db.add(Notification(
            member_id=member.id,
            type=NotificationType.INFO,
            title="Email importé",
            message=f"« {subject} » a été importé dans vos messages.",
            link_url=f"/messages/{new_msg.id}",
        ))
        await db.commit()
    except Exception as e:
        logger.warning("Notification import email échouée : %s", e)

    return True


async def _try_import_as_member_message(db, msg, msg_bytes: bytes) -> bool:
    """Si cet email porte le jeton de transfert d'un membre ayant activé la
    fonction — trouvé dans l'adresse ("+jeton") ou en repli entre crochets en
    tête de l'objet — l'importe comme Message interne. Utilisé pour la boîte
    de réception principale et pour le dossier partagé de repli
    (PERSONAL_TRANSFER_FOLDER) ; les dossiers auto-créés par adresse "+jeton"
    sont eux traités par _process_token_folders, qui connaît déjà le membre
    via le nom du dossier et appelle directement _create_member_message.

    Retourne True si l'email a été traité ici (qu'il ait abouti ou non — un
    jeton reconnu mais invalide/désactivé/en quota dépassé ne doit pas
    retomber sur l'import "planche", qui n'a rien à voir)."""
    from sqlalchemy import select
    from app.models.identity import Member

    token = _extract_import_token(msg)
    if not token or not _TOKEN_RE.match(token):
        # Jeton absent ou mal recopié (oubli fréquent) — avant de laisser
        # tomber sur l'import planche (qui publierait l'email dans la GED
        # partagée), filet de sécurité : si l'expéditeur correspond à un
        # membre ayant activé le transfert, c'est très probablement une
        # tentative manquée de transfert personnel plutôt qu'une planche à
        # publier — on l'ignore silencieusement au lieu de risquer d'exposer
        # un contenu privé. Ne sert pas à authentifier (l'adresse "De :" est
        # falsifiable) — seulement à éviter une fuite accidentelle.
        sender_email = _extract_sender_email(msg)
        if sender_email:
            r0 = await db.execute(
                select(Member).where(
                    func.lower(Member.email) == sender_email,
                    Member.email_import_enabled.is_(True),
                )
            )
            safety_member = r0.scalar_one_or_none()
            if safety_member:
                logger.warning(
                    "Email sans jeton valide depuis %s (transfert activé, membre #%s) — "
                    "ignoré plutôt que classé en planche",
                    sender_email, safety_member.id,
                )
                return True
        return False

    r = await db.execute(
        select(Member).where(
            Member.email_import_token == token,
            Member.email_import_enabled.is_(True),
        )
    )
    member = r.scalar_one_or_none()
    if not member:
        logger.warning("Email avec jeton de transfert inconnu ou désactivé : %s", token)
        return True  # reconnu comme tentative de transfert personnel — pas une planche

    await _create_member_message(db, msg, member)
    return True


# ── Dossiers auto-créés par le serveur mail pour l'adressage "+jeton" ──────
# Comportement Dovecot standard (recipient_delimiter="+") : un email envoyé à
# boite+xxx@domaine est livré dans un sous-dossier INBOX.xxx plutôt que fondu
# dans la boîte de réception — plutôt qu'un défaut, c'est en fait idéal ici :
# ces dossiers sont structurellement séparés de la boîte de réception (où
# vivent les planches), donc aucun risque qu'un import personnel non reconnu
# ne "tombe" par erreur dans la GED partagée — il suffit de ne jamais faire
# suivre ces dossiers vers l'import planche, ce que les fonctions ci-dessous
# garantissent par construction (chemin de code entièrement séparé).

PERSONAL_TRANSFER_FOLDER = "INBOX.Transferperso"
_NON_TOKEN_FOLDER_NAMES = {"archive", "sent", "drafts", "trash", "junk", "spam", "inbox", "transferperso"}
_LIST_LINE_RE = re.compile(r'^\(([^)]*)\)\s+"([^"]*)"\s+(.+)$')


def _parse_list_folder_name(line) -> Optional[str]:
    """Extrait le nom du dossier d'une ligne de réponse IMAP LIST, ex :
    '(\\HasNoChildren \\Marked) "." INBOX.ab12cd34ef56gh78' → 'INBOX.ab12cd34ef56gh78'."""
    line_str = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else line
    m = _LIST_LINE_RE.match(line_str)
    if not m:
        return None
    name = m.group(3).strip()
    if name.startswith('"') and name.endswith('"'):
        name = name[1:-1]
    return name


async def _process_token_folders(db, conn) -> int:
    """Parcourt tous les dossiers IMAP nommés INBOX.<jeton> correspondant à
    un membre actif, et importe leurs emails non lus. Jamais de repli
    planche : un dossier orphelin (jeton régénéré, ancien test, candidat qui
    ne correspond à aucun membre) est simplement ignoré, ses messages restent
    en place sans être traités nulle part."""
    from sqlalchemy import select
    from app.models.identity import Member

    try:
        typ, folders_raw = conn.list()
    except Exception as e:
        logger.error("Erreur listage des dossiers IMAP : %s", e)
        return 0
    if typ != "OK" or not folders_raw:
        return 0

    processed = 0
    for line in folders_raw:
        name = _parse_list_folder_name(line)
        if not name or not name.upper().startswith("INBOX."):
            continue
        candidate = name.split(".", 1)[1]
        if candidate.lower() in _NON_TOKEN_FOLDER_NAMES or not _TOKEN_RE.match(candidate):
            continue

        r = await db.execute(
            select(Member).where(
                Member.email_import_token == candidate,
                Member.email_import_enabled.is_(True),
            )
        )
        member = r.scalar_one_or_none()
        if not member:
            continue  # dossier orphelin — ignoré, jamais publié en planche

        try:
            typ2, _ = conn.select(name)
        except Exception as e:
            logger.error("Erreur sélection dossier %s : %s", name, e)
            continue
        if typ2 != "OK":
            continue

        _, uids = conn.search(None, "UNSEEN")
        uid_list = uids[0].split() if uids and uids[0] else []
        for uid in uid_list:
            _, data = conn.fetch(uid, "(RFC822)")
            if not data or not data[0]:
                continue
            msg = email.message_from_bytes(data[0][1])
            try:
                if await _create_member_message(db, msg, member):
                    processed += 1
            except Exception as e:
                logger.error("Erreur import dossier %s uid=%s : %s", name, uid, e)
            conn.store(uid, "+FLAGS", "\\Seen")

    return processed


async def _process_shared_transfer_folder(db, conn) -> int:
    """Traite le dossier partagé de repli PERSONAL_TRANSFER_FOLDER (utilisé
    quand l'adressage "+jeton" ne fonctionne pas pour un membre) : jeton
    attendu entre crochets en tête de l'objet. Jamais de repli planche ici
    non plus — dossier entièrement dédié."""
    try:
        typ, _ = conn.select(PERSONAL_TRANSFER_FOLDER)
    except Exception:
        return 0  # dossier pas encore créé côté cPanel — rien à faire
    if typ != "OK":
        return 0

    _, uids = conn.search(None, "UNSEEN")
    uid_list = uids[0].split() if uids and uids[0] else []
    processed = 0
    for uid in uid_list:
        _, data = conn.fetch(uid, "(RFC822)")
        if not data or not data[0]:
            continue
        msg_bytes = data[0][1]
        msg = email.message_from_bytes(msg_bytes)
        try:
            if await _try_import_as_member_message(db, msg, msg_bytes):
                processed += 1
        except Exception as e:
            logger.error("Erreur import dossier %s uid=%s : %s", PERSONAL_TRANSFER_FOLDER, uid, e)
        conn.store(uid, "+FLAGS", "\\Seen")

    return processed


async def _import_one(db, msg_bytes: bytes, upload_dir: Path, skip_duplicates: bool = False) -> int:
    """Traite un email et importe ses PJ dans la GED. Retourne le nb de fichiers importés.

    skip_duplicates : si True, n'importe pas une pièce jointe si un document du
    même nom existe déjà dans le dossier cible — utilisé pour le rattrapage
    ponctuel (run_since), qui peut retraiter des emails déjà passés par le
    polling normal (celui-ci ne filtre pas par lu/non-lu)."""
    from sqlalchemy import select
    from app.models.documents import Document, DocStatus, DocFolder

    msg = email.message_from_bytes(msg_bytes)

    # Transfert personnel par jeton : traité à part, jamais comme une
    # planche (même sans pièce jointe reconnue — un jeton valide mais sans
    # PJ exploitable ne doit pas non plus finir classé dans la GED).
    if await _try_import_as_member_message(db, msg, msg_bytes):
        return 0

    sender_raw = msg.get("From", "Inconnu")
    sender = _decode_header(sender_raw)
    subject = _decode_header(msg.get("Subject", "Sans objet"))

    # Date réelle d'envoi de l'email (pas la date de traitement) — important
    # pour le rattrapage ponctuel où on traite des emails reçus il y a
    # plusieurs jours, afin de les classer dans le bon dossier mensuel.
    received_at = datetime.now()
    date_header = msg.get("Date")
    if date_header:
        try:
            import email.utils as _eu
            parsed = _eu.parsedate_to_datetime(date_header)
            if parsed:
                received_at = parsed.replace(tzinfo=None)
        except Exception:
            pass

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

        if skip_duplicates:
            dup_r = await db.execute(
                select(Document.id).where(
                    Document.folder_id == folder.id,
                    Document.original_filename == filename,
                )
            )
            if dup_r.scalar_one_or_none() is not None:
                logger.info("Planche déjà présente, ignorée : %s (%s)", filename, sender)
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
        # Notifier les membres de l'arrivée de la/les planche(s)
        try:
            await _notify_new_planches(db, space.id, imported, sender_label)
        except Exception as e:
            logger.warning("Notification planches échouée : %s", e)
    return imported


async def _notify_new_planches(db, space_id: int, count: int, sender_label: str) -> None:
    """Crée une notification in-app pour tous les membres actifs."""
    from sqlalchemy import select
    from app.models.identity import Member, MemberStatus
    from app.models.system import Notification, NotificationType

    members_r = await db.execute(
        select(Member.id).where(Member.status == MemberStatus.ACTIVE)
    )
    member_ids = [row[0] for row in members_r.all()]
    for mid in member_ids:
        db.add(Notification(
            member_id=mid,
            type=NotificationType.INFO,
            title=f"Nouvelle planche reçue",
            message=f"{count} planche(s) reçue(s) de « {sender_label} » et classée(s) dans la GED.",
            link_url=f"/documents/space/{space_id}",
        ))
    if member_ids:
        await db.commit()
        logger.info("Notification planches envoyée à %d membre(s)", len(member_ids))


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
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        conn = imaplib.IMAP4_SSL(s.imap_host, s.imap_port, ssl_context=ctx, timeout=20)
        conn.login(s.imap_user, s.imap_pass)

        async with AsyncSessionLocal() as db:
            # 1. Dossiers auto-créés par le serveur pour l'adressage "+jeton"
            try:
                n_tok = await _process_token_folders(db, conn)
                if n_tok:
                    logger.info("%d email(s) importé(s) via dossier(s) +jeton", n_tok)
            except Exception as e:
                logger.error("Erreur traitement dossiers +jeton : %s", e)

            # 2. Dossier partagé de repli (objet [jeton])
            try:
                n_shared = await _process_shared_transfer_folder(db, conn)
                if n_shared:
                    logger.info("%d email(s) importé(s) via %s", n_shared, PERSONAL_TRANSFER_FOLDER)
            except Exception as e:
                logger.error("Erreur traitement dossier %s : %s", PERSONAL_TRANSFER_FOLDER, e)

            # 3. Boîte de réception (planches + filet de sécurité jeton oublié)
            conn.select(s.imap_folder)
            _, uids = conn.search(None, "UNSEEN")
            uid_list = uids[0].split() if uids and uids[0] else []

            if uid_list:
                logger.info("%d email(s) non lu(s) à traiter (boîte principale)", len(uid_list))
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


async def run_since(days: int = 7, upload_dir: str = "uploads/documents/planches_recues") -> int:
    """Rattrapage ponctuel : reprend tous les emails des N derniers jours,
    lus ou non (contrairement à run_once qui ne traite que les non-lus) —
    pour rattraper une période où le service n'était pas démarré. Protégé
    contre les doublons (même nom de fichier déjà présent dans le dossier
    cible), et marque les emails comme lus une fois traités."""
    from app.config import get_settings
    from app.database import AsyncSessionLocal

    import app.models.documents
    import app.models.identity
    import app.models.groups
    import app.models.lodge
    import app.models.meetings

    s = get_settings()
    if not s.imap_host or not s.imap_user or not s.imap_pass:
        return 0

    dest_dir = Path(upload_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    total = 0
    since_date = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    conn = imaplib.IMAP4_SSL(s.imap_host, s.imap_port, ssl_context=ctx, timeout=20)
    conn.login(s.imap_user, s.imap_pass)

    async with AsyncSessionLocal() as db:
        # Rattrapage transfert personnel : dossiers +jeton et dossier partagé
        # (mêmes fonctions que le polling normal — non-lus uniquement, pas de
        # notion de SINCE ici, mais couvre le cas où le service était éteint).
        try:
            n_tok = await _process_token_folders(db, conn)
            if n_tok:
                logger.info("Rattrapage : %d email(s) importé(s) via dossier(s) +jeton", n_tok)
        except Exception as e:
            logger.error("Erreur rattrapage dossiers +jeton : %s", e)

        try:
            n_shared = await _process_shared_transfer_folder(db, conn)
            if n_shared:
                logger.info("Rattrapage : %d email(s) importé(s) via %s", n_shared, PERSONAL_TRANSFER_FOLDER)
        except Exception as e:
            logger.error("Erreur rattrapage dossier %s : %s", PERSONAL_TRANSFER_FOLDER, e)

        conn.select(s.imap_folder)
        _, uids = conn.search(None, f'(SINCE "{since_date}")')
        uid_list = uids[0].split() if uids and uids[0] else []
        logger.info("Rattrapage planches : %d email(s) depuis %s", len(uid_list), since_date)

        for uid in uid_list:
            _, data = conn.fetch(uid, "(RFC822)")
            if not data or not data[0]:
                continue
            msg_bytes = data[0][1]
            try:
                n = await _import_one(db, msg_bytes, dest_dir, skip_duplicates=True)
                total += n
                conn.store(uid, "+FLAGS", "\\Seen")
            except Exception as e:
                logger.error("Erreur rattrapage email uid=%s : %s", uid, e)

    conn.logout()
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
