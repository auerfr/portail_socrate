"""Router — Messagerie interne ciblée"""
import json
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Optional, List, Union

from fastapi import APIRouter, Depends, File, Form, Request, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import get_db
from app.dependencies import require_auth
from app.models.identity import Member, MemberStatus, LodgeFunction, MasonicGrade
from app.models.lodge import LodgeSettings
from app.models.messaging import Message, MessageAttachment, MessageRecipient, MessageTargetType
from app.services.email import notify_new_message
from app.models.groups import LodgeGroup as Group, SYSTEM_GROUPS
from app.routers.groups import resolve_group_member_ids, ensure_system_groups

# Délai entre deux envois — évite de saturer le relais SMTP (mêmes conventions
# que app/services/mailing.py::EMAIL_DELAY_MS et member_access.py::ACCESS_EMAIL_DELAY_MS).
# Sans ce délai, un message envoyé à un grand groupe ouvrait autant de
# connexions SMTP simultanées que de destinataires, ce que les relais
# mutualisés (LWS/cPanel) rejettent en rafale avec un "421 4.3.0 Temporary
# System Problem" — la plupart des envois échouaient alors silencieusement.
NOTIFY_EMAIL_DELAY_MS = 300


async def _notify_recipients_paced(
    recipient_emails: list[str], sender_name: str, subject: str, body: str,
    message_id: int, portal_base_url: str,
) -> None:
    """Envoie les notifications de nouveau message une par une, avec une
    pause entre chaque, plutôt que toutes en parallèle (cf. NOTIFY_EMAIL_DELAY_MS)."""
    import asyncio
    for email in recipient_emails:
        try:
            await notify_new_message(
                recipient_email=email,
                sender_name=sender_name,
                subject=subject,
                body=body,
                message_id=message_id,
                portal_base_url=portal_base_url,
            )
        except Exception:
            pass
        await asyncio.sleep(NOTIFY_EMAIL_DELAY_MS / 1000.0)


def _normalize_message_links(html: Optional[str]) -> Optional[str]:
    """Corrige les liens mal formés dans un corps de message : si l'utilisateur
    a collé une URL au format Markdown (`[texte](https://...)`) directement
    dans le champ URL de l'éditeur, ou une URL sans schéma, le href stocké
    n'est pas une URL absolue valide — le navigateur la traite alors comme un
    chemin relatif (ex: /messages/[texte](https://...)), ce qui casse le lien
    et peut même faire planter la page de destination."""
    if not html:
        return html

    def _fix(m: re.Match) -> str:
        href = m.group(1)
        md_match = re.match(r"^\[.*?\]\((https?://[^)]+)\)$", href)
        if md_match:
            href = md_match.group(1)
        elif not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:", href):
            href = "https://" + href
        return f'href="{href}"'

    return re.sub(r'href="([^"]*)"', _fix, html)


def _strip_html_tags(html: str) -> str:
    """Version texte brut d'un corps HTML (éditeur riche) — pour les aperçus,
    notifications email/push, qui n'ont pas besoin de la mise en forme."""
    if not html:
        return ""
    text = re.sub(r"</(p|div|li)>", "\n", html)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    import html as _html_mod
    text = _html_mod.unescape(text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


# ── Constantes upload ─────────────────────────────────────────────────────────
UPLOAD_DIR = Path("app/static/uploads/messages")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 Mo
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "image/jpeg", "image/png", "image/gif", "image/webp",
    "text/plain",
}
ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".txt"}

router = APIRouter(prefix="/messages", tags=["messages"])
from app.template_engine import templates

# Fonctions autorisées à envoyer des messages
SENDER_FUNCTIONS = {
    LodgeFunction.VM,
    LodgeFunction.SECRETAIRE,
    LodgeFunction.TRESORIER,
    LodgeFunction.ORATEUR,
    LodgeFunction.PREMIER_S,
    LodgeFunction.SECOND_S,
    LodgeFunction.EXPERT,
    LodgeFunction.HOSPITALIER,
}

GRADE_ORDER = {
    MasonicGrade.APPRENTI: 1,
    MasonicGrade.COMPAGNON: 2,
    MasonicGrade.MAITRE: 3,
}

GRADE_LABELS = {
    "APPRENTI": "Apprenti et au-dessus",
    "COMPAGNON": "Compagnon et au-dessus",
    "MAITRE": "Maîtres uniquement",
}

FUNCTION_LABELS = {
    "VM": "Vénérable Maître",
    "PREMIER_S": "1er Surveillant",
    "SECOND_S": "2e Surveillant",
    "ORATEUR": "Orateur",
    "SECRETAIRE": "Secrétaire",
    "TRESORIER": "Trésorier",
    "EXPERT": "Expert",
    "MAITRE_CEREMONIES": "Maître des Cérémonies",
    "HARMONISTE": "Maître Harmoniste",
    "HOSPITALIER": "Hospitalier",
    "TUILEUR": "Tuileur",
    "ARCHITECTE": "Architecte",
    "MAITRE_BANQUETS": "Maître des Banquets",
    "FRERE": "Frère (sans office)",
}


def _can_send(user, member: Member) -> bool:
    return True  # Tous les membres actifs peuvent envoyer


async def _get_active_members(db: AsyncSession) -> list[Member]:
    r = await db.execute(
        select(Member)
        .where(Member.status == MemberStatus.ACTIVE)
        .order_by(Member.last_name, Member.first_name)
    )
    return r.scalars().all()


async def _resolve_recipients(
    db: AsyncSession,
    target_type: str,
    target_filter: Optional[str],
    sender_id: int,
) -> list[int]:
    """Retourne la liste des member_id destinataires (hors expéditeur)."""
    all_members = await _get_active_members(db)
    tf = json.loads(target_filter) if target_filter else {}

    if target_type == MessageTargetType.ALL:
        return [m.id for m in all_members if m.id != sender_id]

    elif target_type == MessageTargetType.GRADE:
        min_grade = tf.get("grade", "APPRENTI")
        min_level = GRADE_ORDER.get(MasonicGrade(min_grade), 1)
        return [
            m.id for m in all_members
            if m.id != sender_id
            and GRADE_ORDER.get(m.masonic_grade, 0) >= min_level
        ]

    elif target_type == MessageTargetType.FUNCTION:
        functions = set(tf.get("functions", []))
        return [
            m.id for m in all_members
            if m.id != sender_id
            and m.lodge_function and m.lodge_function.value in functions
        ]

    elif target_type == MessageTargetType.GROUP:
        group_id = tf.get("group_id")
        if group_id:
            group = await db.get(Group, int(group_id))
            if group:
                ids = await resolve_group_member_ids(db, group)
                return [mid for mid in ids if mid != sender_id]
        return []

    elif target_type == MessageTargetType.MANUAL:
        ids = set(tf.get("member_ids", []))
        return [m.id for m in all_members if m.id in ids and m.id != sender_id]

    return []


async def _target_description_async(db: AsyncSession, target_type: str, target_filter: Optional[str]) -> str:
    tf = json.loads(target_filter) if target_filter else {}
    if target_type == MessageTargetType.ALL:
        return "Tous les membres actifs"
    elif target_type == MessageTargetType.GRADE:
        g = tf.get("grade", "APPRENTI")
        return GRADE_LABELS.get(g, g)
    elif target_type == MessageTargetType.FUNCTION:
        fns = tf.get("functions", [])
        labels = [FUNCTION_LABELS.get(f, f) for f in fns]
        return ", ".join(labels) if labels else "Aucune fonction"
    elif target_type == MessageTargetType.GROUP:
        group_id = tf.get("group_id")
        if group_id:
            group = await db.get(Group, int(group_id))
            if group:
                return f"Groupe : {group.name}"
        return "Groupe inconnu"
    elif target_type == MessageTargetType.MANUAL:
        ids = tf.get("member_ids", [])
        return f"{len(ids)} membre(s) sélectionné(s)"
    return target_type


def _target_description(target_type: str, target_filter: Optional[str]) -> str:
    """Version synchrone simplifiée (sans résolution des groupes)."""
    tf = json.loads(target_filter) if target_filter else {}
    if target_type == MessageTargetType.ALL:
        return "Tous les membres actifs"
    elif target_type == MessageTargetType.GRADE:
        g = tf.get("grade", "APPRENTI")
        return GRADE_LABELS.get(g, g)
    elif target_type == MessageTargetType.FUNCTION:
        fns = tf.get("functions", [])
        labels = [FUNCTION_LABELS.get(f, f) for f in fns]
        return ", ".join(labels) if labels else "Aucune fonction"
    elif target_type == MessageTargetType.GROUP:
        return f"Groupe #{tf.get('group_id', '?')}"
    elif target_type == MessageTargetType.MANUAL:
        ids = tf.get("member_ids", [])
        return f"{len(ids)} membre(s) sélectionné(s)"
    return target_type


async def _unread_count(db: AsyncSession, member_id: int) -> int:
    r = await db.execute(
        select(func.count(MessageRecipient.id))
        .join(Message, Message.id == MessageRecipient.message_id)
        .where(
            MessageRecipient.member_id == member_id,
            MessageRecipient.read_at.is_(None),
            Message.sent_at.isnot(None),
        )
    )
    return r.scalar_one() or 0


# ══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/", response_class=HTMLResponse)
async def inbox(
    request: Request,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = 1,
    label: Optional[str] = None,
):
    user, member = ctx
    per_page = 20
    offset = (page - 1) * per_page

    base_where = [
        MessageRecipient.member_id == member.id,
        Message.sent_at.isnot(None),
        MessageRecipient.deleted_at.is_(None),
    ]
    if label:
        base_where.append(MessageRecipient.label == label)

    # Messages reçus
    r = await db.execute(
        select(MessageRecipient)
        .join(Message, Message.id == MessageRecipient.message_id)
        .where(*base_where)
        .options(selectinload(MessageRecipient.message))
        .order_by(Message.sent_at.desc())
        .offset(offset).limit(per_page)
    )
    received = r.scalars().all()

    # Total pour pagination
    r_total = await db.execute(
        select(func.count(MessageRecipient.id))
        .join(Message, Message.id == MessageRecipient.message_id)
        .where(*base_where)
    )
    total = r_total.scalar_one() or 0

    # Labels existants de ce membre (pour filtres)
    r_labels = await db.execute(
        select(MessageRecipient.label)
        .where(
            MessageRecipient.member_id == member.id,
            MessageRecipient.label.isnot(None),
            MessageRecipient.deleted_at.is_(None),
        )
        .distinct()
    )
    all_labels = sorted({row[0] for row in r_labels.all() if row[0]})

    # Expéditeurs
    sender_ids = {rec.message.sender_id for rec in received}
    senders_map: dict[int, Member] = {}
    if sender_ids:
        sr = await db.execute(select(Member).where(Member.id.in_(sender_ids)))
        senders_map = {m.id: m for m in sr.scalars().all()}

    unread = await _unread_count(db, member.id)

    return templates.TemplateResponse(request, "pages/messages/inbox.html", {
        "current_member": member,
        "current_user": user,
        "received": received,
        "senders_map": senders_map,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
        "unread_count": unread,
        "global_unread_messages": unread,
        "can_send": _can_send(user, member),
        "tab": "inbox",
        "active_label": label,
        "all_labels": all_labels,
    })


@router.get("/sent", response_class=HTMLResponse)
async def sent_messages(
    request: Request,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = 1,
):
    user, member = ctx
    if not _can_send(user, member):
        return RedirectResponse(url="/messages/", status_code=303)

    per_page = 20
    offset = (page - 1) * per_page

    sent_where = (
        Message.sender_id == member.id,
        Message.sent_at.isnot(None),
        Message.sender_deleted_at.is_(None),
    )

    r = await db.execute(
        select(Message)
        .where(*sent_where)
        .options(selectinload(Message.recipients))
        .order_by(Message.sent_at.desc())
        .offset(offset).limit(per_page)
    )
    sent = r.scalars().all()

    r_total = await db.execute(
        select(func.count(Message.id)).where(*sent_where)
    )
    total = r_total.scalar_one() or 0
    unread = await _unread_count(db, member.id)

    return templates.TemplateResponse(request, "pages/messages/inbox.html", {
        "current_member": member,
        "current_user": user,
        "sent": sent,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
        "unread_count": unread,
        "can_send": True,
        "tab": "sent",
        "target_description": _target_description,
    })


@router.get("/compose", response_class=HTMLResponse)
async def compose(
    request: Request,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    group_id: Optional[int] = None,
    reply_to: Optional[int] = None,
    reply_all: bool = False,
    to: Optional[int] = None,
    forward_from: Optional[int] = None,
):
    user, member = ctx
    if not _can_send(user, member):
        raise HTTPException(403, "Accès réservé aux officiers")

    await ensure_system_groups(db)
    await db.commit()

    all_members = await _get_active_members(db)
    # Charger tous les groupes pour le ciblage
    r_groups = await db.execute(select(Group).order_by(Group.is_system.desc(), Group.name))
    all_groups = r_groups.scalars().all()

    # Pré-sélection d'un destinataire unique (ex : depuis la fiche membre)
    preselect_member_id = None
    if to and to != member.id and any(m.id == to for m in all_members):
        preselect_member_id = to

    # Pré-remplissage si réponse à un message
    reply_msg = None
    reply_member_ids: List[int] = []
    if reply_to:
        reply_msg = await db.get(Message, reply_to, options=[selectinload(Message.recipients)])
        if reply_msg:
            if reply_all:
                reply_member_ids = list(
                    {reply_msg.sender_id} | {r.member_id for r in reply_msg.recipients} - {member.id}
                )
            else:
                reply_member_ids = [reply_msg.sender_id]

    # Pré-remplissage si transfert d'un message reçu
    forward_msg = None
    forward_sender = None
    if forward_from and not reply_msg:
        forward_msg = await db.get(Message, forward_from)
        if forward_msg:
            forward_sender = await db.get(Member, forward_msg.sender_id)

    # Pré-sélection d'un groupe
    preselect_group = None
    if group_id:
        preselect_group = await db.get(Group, group_id)

    unread = await _unread_count(db, member.id)

    ls_r = await db.execute(select(LodgeSettings).limit(1))
    lodge_cfg = ls_r.scalar_one_or_none()
    visio_server = lodge_cfg.visio_server_url.rstrip("/") if lodge_cfg and lodge_cfg.visio_server_url else ""
    visio_prefix = lodge_cfg.visio_room_prefix or "loge" if lodge_cfg else "loge"

    return templates.TemplateResponse(request, "pages/messages/compose.html", {
        "current_member": member,
        "current_user": user,
        "all_members": all_members,
        "all_groups": all_groups,
        "function_labels": FUNCTION_LABELS,
        "grade_labels": GRADE_LABELS,
        "unread_count": unread,
        "can_send": True,
        "reply_msg": reply_msg,
        "reply_member_ids": reply_member_ids,
        "reply_all": bool(reply_all and reply_msg),
        "forward_msg": forward_msg,
        "forward_sender": forward_sender,
        "preselect_group": preselect_group,
        "preselect_member_id": preselect_member_id,
        "visio_server": visio_server,
        "visio_prefix": visio_prefix,
    })


@router.post("/send")
async def send_message(
    request: Request,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    subject: Annotated[str, Form()],
    body: Annotated[str, Form()],
    target_type: Annotated[str, Form()],
    target_grade: Annotated[Optional[str], Form()] = None,
    target_functions: Annotated[Optional[List[str]], Form()] = None,
    target_group_id: Annotated[Optional[int], Form()] = None,
    target_member_ids: Annotated[Optional[str], Form()] = None,
    parent_id: Annotated[Optional[int], Form()] = None,
    visio_url: Annotated[Optional[str], Form()] = None,
    attachments: Annotated[Optional[Union[List[UploadFile], UploadFile]], File()] = None,
):
    user, member = ctx
    if not _can_send(user, member):
        raise HTTPException(403)

    # Un <input type="file" multiple> soumis sans fichier envoie une seule
    # partie vide — Starlette la remonte alors comme un UploadFile isolé au
    # lieu d'une liste, d'où la normalisation ci-dessous.
    if attachments is None:
        attachment_list: List[UploadFile] = []
    elif isinstance(attachments, list):
        attachment_list = attachments
    else:
        attachment_list = [attachments]

    # Construire le filtre JSON
    tf: dict = {}
    if target_type == MessageTargetType.GRADE:
        tf = {"grade": target_grade or "APPRENTI"}
    elif target_type == MessageTargetType.FUNCTION:
        tf = {"functions": target_functions or []}
    elif target_type == MessageTargetType.GROUP:
        tf = {"group_id": target_group_id}
    elif target_type == MessageTargetType.MANUAL:
        try:
            ids = [int(x.strip()) for x in (target_member_ids or "").split(",") if x.strip()]
        except ValueError:
            ids = []
        tf = {"member_ids": ids}

    target_filter_json = json.dumps(tf) if tf else None

    # Pour une réponse : on garde le lien parent_id pour le fil de discussion,
    # mais les destinataires suivent le ciblage réellement soumis (le compose
    # pré-remplit target_member_ids avec l'expéditeur seul pour "Répondre",
    # ou expéditeur + autres destinataires pour "Répondre à tous").
    if parent_id and not await db.get(Message, parent_id):
        parent_id = None
    recipient_ids = await _resolve_recipients(db, target_type, target_filter_json, member.id)

    if not recipient_ids:
        raise HTTPException(400, "Aucun destinataire trouvé pour ce ciblage")

    # Garde-fou anti double-soumission (double-clic, retour arrière + renvoi,
    # requête réseau relancée) : même expéditeur, même contenu, < 15s.
    dup_check = await db.execute(
        select(Message.id).where(
            Message.sender_id == member.id,
            Message.subject == subject.strip(),
            Message.body == body.strip(),
            Message.sent_at.isnot(None),
            Message.sent_at >= datetime.now() - timedelta(seconds=15),
        ).limit(1)
    )
    if dup_check.scalar_one_or_none():
        return RedirectResponse(url="/messages/sent", status_code=303)

    final_visio = (visio_url or "").strip() or None
    if final_visio and not final_visio.startswith(("http://", "https://")):
        final_visio = "https://" + final_visio

    # Le corps vient de l'éditeur riche (Quill) : on garde le HTML pour l'affichage
    # et une version texte brut pour les aperçus / notifications email & push.
    body_html_clean = body.strip()
    if body_html_clean == "<p><br></p>":
        body_html_clean = ""
    body_plain = _strip_html_tags(body_html_clean)

    # Créer le message
    msg = Message(
        subject=subject.strip(),
        body=body_plain,
        body_html=body_html_clean or None,
        sender_id=member.id,
        target_type=MessageTargetType(target_type),
        target_filter=target_filter_json,
        parent_id=parent_id,
        visio_url=final_visio,
        sent_at=datetime.now(),
    )
    db.add(msg)
    await db.flush()

    # Créer les destinataires
    now = datetime.now()
    for mid in recipient_ids:
        db.add(MessageRecipient(
            message_id=msg.id,
            member_id=mid,
            delivered_at=now,
        ))

    # ── Pièces jointes ────────────────────────────────────────────────────
    for upload in attachment_list:
        if not upload.filename:
            continue
        ext = Path(upload.filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            continue  # extension non autorisée — on ignore silencieusement
        content = await upload.read()
        if len(content) > MAX_FILE_SIZE:
            continue  # trop lourd — on ignore
        stored_name = f"{msg.id}_{uuid.uuid4().hex}{ext}"
        (UPLOAD_DIR / stored_name).write_bytes(content)
        db.add(MessageAttachment(
            message_id=msg.id,
            filename=upload.filename,
            stored_name=stored_name,
            mime_type=upload.content_type or "application/octet-stream",
            size_bytes=len(content),
        ))

    await db.commit()

    # ── Notifications email ───────────────────────────────────────────────
    sender_name = f"{'S∴' if member.civility == 'S' else 'F∴'} {member.last_name} {member.first_name}"
    settings = get_settings()
    portal_url = settings.portal_url.rstrip("/") or f"https://{settings.lodge_domain}"

    # Charger les membres destinataires pour leurs emails
    if recipient_ids:
        rm = await db.execute(
            select(Member).where(Member.id.in_(recipient_ids))
        )
        dest_members = rm.scalars().all()
        dest_emails = [
            dest.email for dest in dest_members
            if dest.email and getattr(dest, "email_notifications", True)
        ]
        if dest_emails:
            import asyncio
            asyncio.create_task(_notify_recipients_paced(  # noqa — fire & forget
                recipient_emails=dest_emails,
                sender_name=sender_name,
                subject=msg.subject,
                body=msg.body,
                message_id=msg.id,
                portal_base_url=portal_url,
            ))

    # ── Push notifications aux destinataires ─────────────────────────────
    try:
        from app.services.push import send_push_broadcast
        push_body = " ".join((msg.body or "").split())[:140]
        await send_push_broadcast(
            db, recipient_ids,
            f"✉ {sender_name}",
            f"{msg.subject} — {push_body}"[:160],
            f"/messages/{msg.id}",
        )
    except Exception:
        pass

    return RedirectResponse(url="/messages/sent", status_code=303)


@router.get("/{message_id}/attachment/{attachment_id}")
async def download_attachment(
    message_id: int,
    attachment_id: int,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Téléchargement sécurisé d'une pièce jointe (expéditeur, destinataire ou admin)."""
    user, member = ctx

    att = await db.get(MessageAttachment, attachment_id)
    if not att or att.message_id != message_id:
        raise HTTPException(404)

    msg = await db.get(Message, message_id, options=[selectinload(Message.recipients)])
    if not msg:
        raise HTTPException(404)

    # Vérification d'accès
    is_sender = msg.sender_id == member.id
    is_recipient = any(r.member_id == member.id for r in msg.recipients)
    if not (user.is_admin or is_sender or is_recipient):
        raise HTTPException(403)

    file_path = UPLOAD_DIR / att.stored_name
    if not file_path.exists():
        raise HTTPException(404, "Fichier introuvable sur le serveur")

    return FileResponse(
        path=str(file_path),
        filename=att.filename,
        media_type=att.mime_type or "application/octet-stream",
    )


def _unlink_message_files(msg: Message) -> None:
    """Supprime du disque les pièces jointes d'un message (avant suppression DB)."""
    for att in msg.attachments:
        try:
            (UPLOAD_DIR / att.stored_name).unlink(missing_ok=True)
        except OSError:
            pass


@router.get("/{message_id}/attachment/{attachment_id}/preview")
async def preview_attachment(
    message_id: int,
    attachment_id: int,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Aperçu inline (Content-Disposition: inline) — pour les vignettes image."""
    from fastapi.responses import Response as _Response

    user, member = ctx

    att = await db.get(MessageAttachment, attachment_id)
    if not att or att.message_id != message_id:
        raise HTTPException(404)

    msg = await db.get(Message, message_id, options=[selectinload(Message.recipients)])
    if not msg:
        raise HTTPException(404)

    is_sender = msg.sender_id == member.id
    is_recipient = any(r.member_id == member.id for r in msg.recipients)
    if not (user.is_admin or is_sender or is_recipient):
        raise HTTPException(403)

    file_path = UPLOAD_DIR / att.stored_name
    if not file_path.exists():
        raise HTTPException(404, "Fichier introuvable sur le serveur")

    return _Response(
        content=file_path.read_bytes(),
        media_type=att.mime_type or "application/octet-stream",
        headers={
            "Content-Disposition": f'inline; filename="{att.filename}"',
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.post("/{message_id}/attachment/{attachment_id}/save-to-library")
async def save_attachment_to_library(
    message_id: int,
    attachment_id: int,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Copie une pièce jointe reçue vers l'espace personnel du membre dans la
    Bibliothèque documentaire (Documents), sans toucher au fichier original."""
    user, member = ctx

    att = await db.get(MessageAttachment, attachment_id)
    if not att or att.message_id != message_id:
        raise HTTPException(404)

    msg = await db.get(Message, message_id, options=[selectinload(Message.recipients)])
    if not msg:
        raise HTTPException(404)

    is_sender = msg.sender_id == member.id
    is_recipient = any(r.member_id == member.id for r in msg.recipients)
    if not (user.is_admin or is_sender or is_recipient):
        raise HTTPException(403)

    src_path = UPLOAD_DIR / att.stored_name
    if not src_path.exists():
        raise HTTPException(404, "Fichier introuvable sur le serveur")

    from app.models.documents import DocFolder, Document, DocStatus, MinGrade
    from app.routers.documents import _get_or_create_personal_space, UPLOAD_DIR as DOCS_UPLOAD_DIR

    space = await _get_or_create_personal_space(db)
    r = await db.execute(
        select(DocFolder).where(
            DocFolder.space_id == space.id,
            DocFolder.personal_owner_id == member.id,
            DocFolder.parent_id == None,  # noqa: E711
        )
    )
    folder = r.scalar_one_or_none()
    if not folder:
        folder = DocFolder(
            space_id=space.id,
            name=f"{member.last_name} {member.first_name}",
            min_grade=MinGrade.ALL,
            personal_owner_id=member.id,
            created_by_id=member.id,
        )
        db.add(folder)
        await db.flush()

    ext = Path(att.filename).suffix.lower()
    stored_name = f"{uuid.uuid4().hex}{ext}"
    dest_path = DOCS_UPLOAD_DIR / stored_name
    dest_path.write_bytes(src_path.read_bytes())

    doc = Document(
        folder_id=folder.id,
        name=att.filename,
        original_filename=att.filename,
        mime_type=att.mime_type,
        file_size=att.size_bytes,
        storage_path=str(dest_path),
        status=DocStatus.PUBLISHED,
        author_id=member.id,
    )
    db.add(doc)
    await db.commit()

    return RedirectResponse(url=f"/messages/{message_id}?saved_to_library=1", status_code=303)


@router.post("/trash/empty-trash")
async def empty_trash_2(
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    from sqlalchemy import delete as sql_delete
    await db.execute(sql_delete(MessageRecipient).where(
        MessageRecipient.member_id == member.id,
        MessageRecipient.deleted_at.isnot(None),
    ))
    deleted_sent = await db.execute(
        select(Message)
        .where(
            Message.sender_id == member.id,
            Message.sender_deleted_at.isnot(None),
        )
        .options(selectinload(Message.attachments))
    )
    for msg in deleted_sent.scalars().all():
        _unlink_message_files(msg)
        await db.delete(msg)
    await db.commit()
    return RedirectResponse(url="/messages/trash", status_code=303)


@router.get("/trash", response_class=HTMLResponse)
async def trash_view(
    request: Request,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    r1 = await db.execute(
        select(MessageRecipient)
        .join(Message, Message.id == MessageRecipient.message_id)
        .where(
            MessageRecipient.member_id == member.id,
            MessageRecipient.deleted_at.isnot(None),
            Message.sent_at.isnot(None),
        )
        .options(selectinload(MessageRecipient.message))
        .order_by(MessageRecipient.deleted_at.desc())
    )
    trashed_received = r1.scalars().all()
    r2 = await db.execute(
        select(Message)
        .where(
            Message.sender_id == member.id,
            Message.sender_deleted_at.isnot(None),
        )
        .options(selectinload(Message.recipients))
        .order_by(Message.sender_deleted_at.desc())
    )
    trashed_sent = r2.scalars().all()
    sender_ids = {rec.message.sender_id for rec in trashed_received}
    senders_map: dict[int, object] = {}
    if sender_ids:
        from app.models.identity import Member as _M
        sr = await db.execute(select(_M).where(_M.id.in_(sender_ids)))
        senders_map = {m.id: m for m in sr.scalars().all()}
    unread = await _unread_count(db, member.id)
    return templates.TemplateResponse(request, "pages/messages/trash.html", {
        "current_member": member, "current_user": user,
        "trashed_received": trashed_received, "trashed_sent": trashed_sent,
        "senders_map": senders_map, "unread_count": unread,
        "can_send": _can_send(user, member), "tab": "trash",
    })


@router.get("/{message_id}", response_class=HTMLResponse)
async def message_detail(
    message_id: int,
    request: Request,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx

    msg = await db.get(
        Message, message_id,
        options=[selectinload(Message.recipients), selectinload(Message.attachments)]
    )
    if not msg or not msg.sent_at:
        raise HTTPException(404)

    # Vérifier accès : destinataire ou expéditeur ou admin
    recipient = None
    if msg.sender_id != member.id and not user.is_admin:
        r = await db.execute(
            select(MessageRecipient).where(
                MessageRecipient.message_id == message_id,
                MessageRecipient.member_id == member.id,
            )
        )
        recipient = r.scalar_one_or_none()
        if not recipient:
            raise HTTPException(403)

    # Marquer comme lu
    if recipient and not recipient.read_at:
        recipient.read_at = datetime.now()
        await db.commit()
    elif not recipient and msg.sender_id != member.id:
        # Double vérification
        r2 = await db.execute(
            select(MessageRecipient).where(
                MessageRecipient.message_id == message_id,
                MessageRecipient.member_id == member.id,
            )
        )
        rec2 = r2.scalar_one_or_none()
        if rec2 and not rec2.read_at:
            rec2.read_at = datetime.now()
            await db.commit()

    # Expéditeur
    sender = await db.get(Member, msg.sender_id)

    # Destinataires avec membres (si expéditeur ou admin)
    recipients_detail = []
    if msg.sender_id == member.id or user.is_admin:
        recipient_ids = [rec.member_id for rec in msg.recipients]
        if recipient_ids:
            rm = await db.execute(select(Member).where(Member.id.in_(recipient_ids)))
            members_map = {m.id: m for m in rm.scalars().all()}
            recipients_detail = [
                {"member": members_map.get(rec.member_id), "rec": rec}
                for rec in msg.recipients
            ]

    unread = await _unread_count(db, member.id)

    # Label du destinataire courant
    recipient_label = None
    if msg.sender_id != member.id:
        for r in msg.recipients:
            if r.member_id == member.id:
                recipient_label = r.label
                break

    return templates.TemplateResponse(request, "pages/messages/detail.html", {
        "current_member": member,
        "current_user": user,
        "msg": msg,
        "body_html_display": _normalize_message_links(msg.body_html),
        "sender": sender,
        "recipients_detail": recipients_detail,
        "is_sender": msg.sender_id == member.id,
        "target_description": _target_description(msg.target_type, msg.target_filter),
        "unread_count": unread,
        "can_send": _can_send(user, member),
        "attachments": msg.attachments,
        "recipient_label": recipient_label,
        "all_labels": [],  # labels existants (chargés si besoin)
        "saved_to_library": request.query_params.get("saved_to_library"),
    })


@router.get("/api/unread")
async def messages_unread_count(
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    count = await _unread_count(db, member.id)
    from fastapi.responses import JSONResponse
    return JSONResponse({"total": count})


@router.post("/{message_id}/delete")
async def delete_message(
    message_id: int,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Déplace vers la corbeille (soft delete) pour l'expéditeur ou le destinataire."""
    user, member = ctx
    msg = await db.get(Message, message_id, options=[selectinload(Message.recipients)])
    if not msg:
        raise HTTPException(404)

    now = datetime.now()
    is_sender = msg.sender_id == member.id

    # Supprime toujours SA PROPRE copie (expéditeur → sender_deleted_at,
    # destinataire → sa ligne recipient) — être admin ne change pas de quel
    # côté on se trouve, sinon un admin qui supprime un message reçu efface
    # à tort la copie de l'expéditeur au lieu de la sienne (le message reste
    # visible dans ses Reçus).
    if is_sender:
        msg.sender_deleted_at = now
    else:
        # Destinataire → soft delete sur son enregistrement recipient
        r = await db.execute(
            select(MessageRecipient).where(
                MessageRecipient.message_id == message_id,
                MessageRecipient.member_id == member.id,
            )
        )
        rec = r.scalar_one_or_none()
        if rec:
            rec.deleted_at = now
        elif user.is_admin:
            # Admin ni expéditeur ni destinataire (modération) : masque la
            # copie expéditeur, seul champ disponible pour ce message.
            msg.sender_deleted_at = now
        else:
            raise HTTPException(403)

    await db.commit()

    if is_sender:
        return RedirectResponse(url="/messages/sent", status_code=303)
    return RedirectResponse(url="/messages/", status_code=303)


@router.post("/bulk-delete")
async def bulk_delete_messages(
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    message_ids: Annotated[str, Form()] = "",
    origin: Annotated[str, Form()] = "inbox",
):
    """Déplace plusieurs messages vers la corbeille en une seule action (sélection par cases)."""
    user, member = ctx
    try:
        ids = {int(x) for x in message_ids.split(",") if x.strip()}
    except ValueError:
        ids = set()

    now = datetime.now()
    for mid in ids:
        msg = await db.get(Message, mid)
        if not msg:
            continue
        # Supprime toujours SA PROPRE copie — cf. delete_message() pour le
        # détail du bug que ça corrige (admin destinataire non pris en compte).
        is_sender = msg.sender_id == member.id
        if is_sender:
            msg.sender_deleted_at = now
        else:
            r = await db.execute(
                select(MessageRecipient).where(
                    MessageRecipient.message_id == mid,
                    MessageRecipient.member_id == member.id,
                )
            )
            rec = r.scalar_one_or_none()
            if rec:
                rec.deleted_at = now
            elif user.is_admin:
                msg.sender_deleted_at = now

    await db.commit()
    return RedirectResponse(
        url="/messages/sent" if origin == "sent" else "/messages/",
        status_code=303,
    )


@router.post("/{message_id}/restore")
async def restore_message(
    message_id: int,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Restaure un message depuis la corbeille."""
    user, member = ctx
    msg = await db.get(Message, message_id)
    if not msg:
        raise HTTPException(404)

    if msg.sender_id == member.id or user.is_admin:
        msg.sender_deleted_at = None
    else:
        r = await db.execute(
            select(MessageRecipient).where(
                MessageRecipient.message_id == message_id,
                MessageRecipient.member_id == member.id,
            )
        )
        rec = r.scalar_one_or_none()
        if rec:
            rec.deleted_at = None

    await db.commit()
    return RedirectResponse(url="/messages/trash", status_code=303)


@router.post("/{message_id}/label")
async def set_label(
    message_id: int,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    label: str = Form(""),
):
    """Définit un label sur un message reçu."""
    user, member = ctx
    r = await db.execute(
        select(MessageRecipient).where(
            MessageRecipient.message_id == message_id,
            MessageRecipient.member_id == member.id,
        )
    )
    rec = r.scalar_one_or_none()
    if rec:
        rec.label = label.strip()[:50] or None
        await db.commit()
    return RedirectResponse(url=f"/messages/{message_id}", status_code=303)
