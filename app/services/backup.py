"""Service de sauvegarde automatique — ZIP (DB + uploads) + envoi email."""
import asyncio
import glob
import io
import logging
import os
import smtplib
import zipfile
from datetime import datetime
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from pathlib import Path

logger = logging.getLogger(__name__)

BACKUP_DIR = Path("backups")
MAX_BACKUPS = 5  # nombre de ZIP à conserver localement


def _db_path() -> Path | None:
    """Trouve le fichier SQLite depuis DATABASE_URL."""
    from app.config import get_settings
    url = get_settings().database_url
    if "sqlite" in url:
        # aiosqlite:///./socrate.db  → socrate.db
        path = url.split("///")[-1].lstrip("./")
        return Path(path)
    return None


def create_backup_zip() -> Path:
    """Crée un ZIP horodaté contenant la DB + le dossier uploads.
    Retourne le chemin du fichier créé."""
    BACKUP_DIR.mkdir(exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = BACKUP_DIR / f"backup_{stamp}.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Base SQLite
        db = _db_path()
        if db and db.exists():
            zf.write(db, f"db/{db.name}")

        # Dossier uploads
        uploads = Path("uploads")
        if uploads.exists():
            for fpath in uploads.rglob("*"):
                if fpath.is_file():
                    zf.write(fpath, str(fpath))

    logger.info("Backup créé : %s (%.1f Mo)", zip_path, zip_path.stat().st_size / 1_048_576)

    # Nettoyage : garder seulement les N derniers
    existing = sorted(BACKUP_DIR.glob("backup_*.zip"), key=lambda p: p.stat().st_mtime)
    for old in existing[:-MAX_BACKUPS]:
        old.unlink(missing_ok=True)
        logger.info("Ancien backup supprimé : %s", old)

    return zip_path


def send_backup_email(zip_path: Path, to: str) -> bool:
    """Envoie le ZIP par email. Retourne True si succès."""
    from app.config import get_settings
    s = get_settings()

    if not to or not s.smtp_host:
        logger.warning("Envoi backup ignoré : pas de destinataire ou SMTP non configuré")
        return False

    msg = MIMEMultipart()
    msg["From"] = s.smtp_from
    msg["To"] = to
    msg["Subject"] = f"[Portail Socrate] Sauvegarde automatique — {datetime.now().strftime('%d/%m/%Y %H:%M')}"

    body = (
        f"Bonjour,\n\n"
        f"Veuillez trouver en pièce jointe la sauvegarde automatique du portail ({zip_path.name}).\n"
        f"Taille : {zip_path.stat().st_size / 1_048_576:.1f} Mo\n\n"
        f"Ce message est généré automatiquement — ne pas répondre.\n"
        f"Portail Socrate"
    )
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with open(zip_path, "rb") as f:
        part = MIMEBase("application", "zip")
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", "attachment", filename=zip_path.name)
    msg.attach(part)

    try:
        if s.smtp_secure == "ssl":
            srv = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=30)
        else:
            srv = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30)
            if s.smtp_secure == "tls":
                srv.starttls()
        if s.smtp_user:
            srv.login(s.smtp_user, s.smtp_pass)
        srv.sendmail(s.smtp_from, [to], msg.as_string())
        srv.quit()
        logger.info("Email backup envoyé à %s", to)
        return True
    except Exception as exc:
        logger.error("Échec envoi backup : %s", exc)
        return False


async def run_backup(to_email: str | None = None) -> dict:
    """Exécute la sauvegarde (dans un thread pour ne pas bloquer l'event loop)."""
    loop = asyncio.get_event_loop()
    zip_path = await loop.run_in_executor(None, create_backup_zip)
    sent = False
    if to_email:
        sent = await loop.run_in_executor(None, send_backup_email, zip_path, to_email)
    return {"zip": str(zip_path), "sent": sent}


async def weekly_backup_loop(get_admin_email_fn):
    """Boucle asyncio : sauvegarde toutes les 7 jours."""
    SEVEN_DAYS = 7 * 24 * 3600
    await asyncio.sleep(60)  # délai au démarrage
    while True:
        try:
            email = await get_admin_email_fn()
            result = await run_backup(to_email=email)
            logger.info("Sauvegarde hebdomadaire : %s", result)
        except Exception as exc:
            logger.error("Erreur sauvegarde hebdo : %s", exc, exc_info=True)
        await asyncio.sleep(SEVEN_DAYS)
