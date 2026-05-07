"""Router — Messagerie interne ciblée"""
import json
from datetime import datetime
from typing import Annotated, Optional, List

from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import require_auth
from app.models.identity import Member, MemberStatus, LodgeFunction, MasonicGrade
from app.models.messaging import Message, MessageRecipient, MessageTargetType

router = APIRouter(prefix="/messages", tags=["messages"])
templates = Jinja2Templates(directory="app/templates")

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
    return user.is_admin or member.lodge_function in SENDER_FUNCTIONS


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

    elif target_type == MessageTargetType.MANUAL:
        ids = set(tf.get("member_ids", []))
        return [m.id for m in all_members if m.id in ids and m.id != sender_id]

    return []


def _target_description(target_type: str, target_filter: Optional[str]) -> str:
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
):
    user, member = ctx
    per_page = 20
    offset = (page - 1) * per_page

    # Messages reçus
    r = await db.execute(
        select(MessageRecipient)
        .join(Message, Message.id == MessageRecipient.message_id)
        .where(
            MessageRecipient.member_id == member.id,
            Message.sent_at.isnot(None),
        )
        .options(selectinload(MessageRecipient.message))
        .order_by(Message.sent_at.desc())
        .offset(offset).limit(per_page)
    )
    received = r.scalars().all()

    # Total pour pagination
    r_total = await db.execute(
        select(func.count(MessageRecipient.id))
        .join(Message, Message.id == MessageRecipient.message_id)
        .where(
            MessageRecipient.member_id == member.id,
            Message.sent_at.isnot(None),
        )
    )
    total = r_total.scalar_one() or 0

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
        "can_send": _can_send(user, member),
        "tab": "inbox",
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

    r = await db.execute(
        select(Message)
        .where(Message.sender_id == member.id, Message.sent_at.isnot(None))
        .options(selectinload(Message.recipients))
        .order_by(Message.sent_at.desc())
        .offset(offset).limit(per_page)
    )
    sent = r.scalars().all()

    r_total = await db.execute(
        select(func.count(Message.id))
        .where(Message.sender_id == member.id, Message.sent_at.isnot(None))
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
):
    user, member = ctx
    if not _can_send(user, member):
        raise HTTPException(403, "Accès réservé aux officiers")

    all_members = await _get_active_members(db)
    unread = await _unread_count(db, member.id)

    return templates.TemplateResponse(request, "pages/messages/compose.html", {
        "current_member": member,
        "current_user": user,
        "all_members": all_members,
        "function_labels": FUNCTION_LABELS,
        "grade_labels": GRADE_LABELS,
        "unread_count": unread,
        "can_send": True,
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
    target_member_ids: Annotated[Optional[str], Form()] = None,
):
    user, member = ctx
    if not _can_send(user, member):
        raise HTTPException(403)

    # Construire le filtre JSON
    tf: dict = {}
    if target_type == MessageTargetType.GRADE:
        tf = {"grade": target_grade or "APPRENTI"}
    elif target_type == MessageTargetType.FUNCTION:
        tf = {"functions": target_functions or []}
    elif target_type == MessageTargetType.MANUAL:
        try:
            ids = [int(x.strip()) for x in (target_member_ids or "").split(",") if x.strip()]
        except ValueError:
            ids = []
        tf = {"member_ids": ids}

    target_filter_json = json.dumps(tf) if tf else None

    # Résoudre les destinataires
    recipient_ids = await _resolve_recipients(
        db, target_type, target_filter_json, member.id
    )

    if not recipient_ids:
        raise HTTPException(400, "Aucun destinataire trouvé pour ce ciblage")

    # Créer le message
    msg = Message(
        subject=subject.strip(),
        body=body.strip(),
        sender_id=member.id,
        target_type=MessageTargetType(target_type),
        target_filter=target_filter_json,
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

    await db.commit()
    return RedirectResponse(url="/messages/sent", status_code=303)


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
        options=[selectinload(Message.recipients)]
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

    return templates.TemplateResponse(request, "pages/messages/detail.html", {
        "current_member": member,
        "current_user": user,
        "msg": msg,
        "sender": sender,
        "recipients_detail": recipients_detail,
        "is_sender": msg.sender_id == member.id,
        "target_description": _target_description(msg.target_type, msg.target_filter),
        "unread_count": unread,
        "can_send": _can_send(user, member),
    })


@router.post("/{message_id}/delete")
async def delete_message(
    message_id: int,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    msg = await db.get(Message, message_id, options=[selectinload(Message.recipients)])
    if not msg:
        raise HTTPException(404)

    is_sender = msg.sender_id == member.id
    anyone_read = any(r.read_at for r in msg.recipients)

    if not (user.is_admin or (is_sender and not anyone_read)):
        raise HTTPException(403, "Suppression impossible : message déjà lu")

    await db.delete(msg)
    await db.commit()

    if is_sender:
        return RedirectResponse(url="/messages/sent", status_code=303)
    return RedirectResponse(url="/messages/", status_code=303)
