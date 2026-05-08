"""Router Chat — Messagerie instantanée (remplace Telegram)"""
import re
from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape as _escape
from sqlalchemy import select, delete, func as sql_func, or_, and_
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_auth
from app.models.identity import Member, MasonicGrade, LodgeFunction
from app.models.chat import (
    ChatChannel, ChatChannelMember, ChatMessage, ChatRead,
    ChannelType, MessageContentType,
)

router = APIRouter(prefix="/chat", tags=["chat"])
templates = Jinja2Templates(directory="app/templates")

def _render_chat(text: str) -> Markup:
    if not text:
        return Markup("")
    url_pat = re.compile(r"(https?://[^\s]+)")
    parts = []
    last = 0
    for m in url_pat.finditer(text):
        segment = str(_escape(text[last:m.start()]))
        segment = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", segment)
        segment = segment.replace("\n", "<br>")
        parts.append(segment)
        url = m.group(1)
        eu = str(_escape(url))
        parts.append(
            f'<a href="{eu}" target="_blank" rel="noopener" '
            f'class="underline opacity-80 hover:opacity-100 break-all">{eu}</a>'
        )
        last = m.end()
    tail = str(_escape(text[last:]))
    tail = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", tail)
    tail = tail.replace("\n", "<br>")
    parts.append(tail)
    return Markup("".join(parts))

templates.env.filters["render_chat"] = _render_chat

GRADE_ORDER = {
    MasonicGrade.APPRENTI: 1,
    MasonicGrade.COMPAGNON: 2,
    MasonicGrade.MAITRE: 3,
}


async def _accessible_channels(member: Member, db: AsyncSession) -> list[ChatChannel]:
    """Retourne les canaux accessibles pour ce membre."""
    # Canaux où le membre est explicitement inscrit (COMMISSION, DIRECT)
    member_channel_ids_r = await db.execute(
        select(ChatChannelMember.channel_id)
        .where(ChatChannelMember.member_id == member.id)
    )
    member_channel_ids = {row[0] for row in member_channel_ids_r.all()}

    channels_r = await db.execute(
        select(ChatChannel).order_by(ChatChannel.id)
    )
    all_channels = channels_r.scalars().all()

    accessible = []
    member_grade_order = GRADE_ORDER.get(member.masonic_grade, 0)

    for ch in all_channels:
        if ch.type == ChannelType.GENERAL:
            accessible.append(ch)
        elif ch.type == ChannelType.GRADE:
            required_order = GRADE_ORDER.get(MasonicGrade(ch.grade_filter), 0) if ch.grade_filter else 0
            if member_grade_order >= required_order:
                accessible.append(ch)
        elif ch.type == ChannelType.FUNCTION:
            if ch.function_filter and member.lodge_function.value == ch.function_filter:
                accessible.append(ch)
            elif member.lodge_function in (LodgeFunction.VM, LodgeFunction.SECRETAIRE):
                accessible.append(ch)  # officiers voient tout
        elif ch.type in (ChannelType.COMMISSION, ChannelType.DIRECT):
            if ch.id in member_channel_ids:
                accessible.append(ch)

    return accessible


async def _unread_count_per_channel(member_id: int, channel_ids: list[int],
                                     db: AsyncSession) -> dict[int, int]:
    """Nombre de messages non lus par canal."""
    if not channel_ids:
        return {}

    # Dernière lecture par canal
    reads_r = await db.execute(
        select(ChatRead)
        .where(ChatRead.member_id == member_id,
               ChatRead.channel_id.in_(channel_ids))
    )
    reads = {r.channel_id: r.last_read_message_id or 0 for r in reads_r.scalars().all()}

    # Compter messages après dernière lecture (par canal)
    unread = {}
    for ch_id in channel_ids:
        last_read_id = reads.get(ch_id, 0)
        count_r = await db.execute(
            select(sql_func.count(ChatMessage.id))
            .where(
                ChatMessage.channel_id == ch_id,
                ChatMessage.sender_id != member_id,
                ChatMessage.id > last_read_id,
                ChatMessage.is_deleted == False,
            )
        )
        unread[ch_id] = count_r.scalar() or 0

    return unread


async def _mark_read(member_id: int, channel_id: int, last_msg_id: int,
                      db: AsyncSession):
    stmt = sqlite_insert(ChatRead).values(
        channel_id=channel_id,
        member_id=member_id,
        last_read_message_id=last_msg_id,
        last_read_at=datetime.now(),
    ).on_conflict_do_update(
        index_elements=["channel_id", "member_id"],
        set_={"last_read_message_id": last_msg_id, "last_read_at": datetime.now()},
    )
    await db.execute(stmt)
    await db.commit()


# ── Page principale ───────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def chat_home(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    channels = await _accessible_channels(member, db)
    ch_ids = [c.id for c in channels]
    unread = await _unread_count_per_channel(member.id, ch_ids, db)

    active_members_r = await db.execute(
        select(Member).where(Member.status == "ACTIVE", Member.id != member.id).order_by(Member.last_name)
    )
    active_members = active_members_r.scalars().all()

    can_manage = user.is_admin or member.lodge_function in (LodgeFunction.VM, LodgeFunction.SECRETAIRE)

    return templates.TemplateResponse(request, "pages/chat/index.html", {
        "current_member": member,
        "current_user": user,
        "channels": channels,
        "active_channel": None,
        "messages": [],
        "unread": unread,
        "active_members": active_members,
        "can_manage": can_manage,
        "last_msg_id": 0,
    })


# ── Vue canal ─────────────────────────────────────────────────────────────────

@router.get("/{channel_id}", response_class=HTMLResponse)
async def chat_channel(
    request: Request,
    channel_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    channels = await _accessible_channels(member, db)
    channel = next((c for c in channels if c.id == channel_id), None)
    if not channel:
        raise HTTPException(status_code=404, detail="Canal introuvable ou accès refusé")

    # Messages (100 derniers)
    msgs_r = await db.execute(
        select(ChatMessage)
        .options(selectinload(ChatMessage.sender),
                 selectinload(ChatMessage.reply_to).selectinload(ChatMessage.sender))
        .where(ChatMessage.channel_id == channel_id, ChatMessage.is_deleted == False)
        .order_by(ChatMessage.created_at.desc())
        .limit(100)
    )
    messages = list(reversed(msgs_r.scalars().all()))

    # Marquer comme lu
    if messages:
        await _mark_read(member.id, channel_id, messages[-1].id, db)

    # Badges non lus pour sidebar
    ch_ids = [c.id for c in channels]
    unread = await _unread_count_per_channel(member.id, ch_ids, db)

    # Membres pour la liste (DM)
    active_members_r = await db.execute(
        select(Member)
        .where(Member.status == "ACTIVE", Member.id != member.id)
        .order_by(Member.last_name)
    )
    active_members = active_members_r.scalars().all()

    can_manage = user.is_admin or member.lodge_function in (
        LodgeFunction.VM, LodgeFunction.SECRETAIRE
    )

    return templates.TemplateResponse(request, "pages/chat/index.html", {
        "current_member": member,
        "current_user": user,
        "channels": channels,
        "active_channel": channel,
        "messages": messages,
        "unread": unread,
        "active_members": active_members,
        "can_manage": can_manage,
        "last_msg_id": messages[-1].id if messages else 0,
    })


# ── Polling JSON nouveaux messages ────────────────────────────────────────────

@router.get("/{channel_id}/messages")
async def chat_messages_poll(
    request: Request,
    channel_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    since_id: int = 0,
):
    user, member = ctx
    channels = await _accessible_channels(member, db)
    if not any(c.id == channel_id for c in channels):
        raise HTTPException(status_code=403)

    msgs_r = await db.execute(
        select(ChatMessage)
        .options(selectinload(ChatMessage.sender),
                 selectinload(ChatMessage.reply_to).selectinload(ChatMessage.sender))
        .where(
            ChatMessage.channel_id == channel_id,
            ChatMessage.id > since_id,
            ChatMessage.is_deleted == False,
        )
        .order_by(ChatMessage.created_at.asc())
        .limit(50)
    )
    new_msgs = msgs_r.scalars().all()

    if new_msgs:
        await _mark_read(member.id, channel_id, new_msgs[-1].id, db)

    def _msg_json(m: ChatMessage) -> dict:
        reply = None
        if m.reply_to:
            reply = {
                "id": m.reply_to.id,
                "sender": f"{m.reply_to.sender.first_name} {m.reply_to.sender.last_name}",
                "preview": (m.reply_to.content or "")[:80],
            }
        return {
            "id": m.id,
            "sender_id": m.sender_id,
            "sender_name": f"{m.sender.first_name} {m.sender.last_name}",
            "sender_initials": f"{m.sender.first_name[0]}{m.sender.last_name[0]}",
            "is_mine": m.sender_id == member.id,
            "content": m.content or "",
            "content_type": m.content_type.value,
            "created_at": m.created_at.strftime("%H:%M"),
            "created_date": m.created_at.strftime("%d/%m/%Y"),
            "reply": reply,
        }

    return JSONResponse({
        "messages": [_msg_json(m) for m in new_msgs],
        "last_id": new_msgs[-1].id if new_msgs else since_id,
    })


# ── Envoi message ─────────────────────────────────────────────────────────────

@router.post("/{channel_id}/send")
async def chat_send(
    request: Request,
    channel_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    content: str = Form(""),
    reply_to_id: int = Form(0),
):
    user, member = ctx
    channels = await _accessible_channels(member, db)
    if not any(c.id == channel_id for c in channels):
        raise HTTPException(status_code=403)

    content = content.strip()
    if not content:
        return RedirectResponse(url=f"/chat/{channel_id}", status_code=303)

    channel = next(c for c in channels if c.id == channel_id)
    if channel.is_readonly and not (user.is_admin or member.lodge_function == LodgeFunction.VM):
        raise HTTPException(status_code=403, detail="Canal en lecture seule")

    msg = ChatMessage(
        channel_id=channel_id,
        sender_id=member.id,
        content=content,
        content_type=MessageContentType.TEXT,
        reply_to_id=reply_to_id if reply_to_id else None,
    )
    db.add(msg)
    await db.commit()

    return RedirectResponse(url=f"/chat/{channel_id}", status_code=303)


# ── Supprimer un message ─────────────────────────────────────────────────────

@router.post("/messages/{msg_id}/delete")
async def delete_message(
    msg_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    msg = await db.get(ChatMessage, msg_id)
    if not msg or msg.is_deleted:
        raise HTTPException(status_code=404)
    if msg.sender_id != member.id and not (user.is_admin or member.lodge_function in (LodgeFunction.VM, LodgeFunction.SECRETAIRE)):
        raise HTTPException(status_code=403)
    msg.is_deleted = True
    msg.content = ""
    await db.commit()
    return JSONResponse({"ok": True})


# ── Supprimer un canal (admin/VM) ────────────────────────────────────────────

@router.post("/channels/{channel_id}/delete")
async def delete_channel(
    channel_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not (user.is_admin or member.lodge_function in (LodgeFunction.VM, LodgeFunction.SECRETAIRE)):
        raise HTTPException(status_code=403)
    channel = await db.get(ChatChannel, channel_id)
    if not channel:
        raise HTTPException(status_code=404)
    await db.execute(delete(ChatRead).where(ChatRead.channel_id == channel_id))
    await db.execute(delete(ChatMessage).where(ChatMessage.channel_id == channel_id))
    await db.execute(delete(ChatChannelMember).where(ChatChannelMember.channel_id == channel_id))
    await db.delete(channel)
    await db.commit()
    return RedirectResponse(url="/chat/", status_code=303)


# ── Créer un canal (admin) ────────────────────────────────────────────────────

@router.post("/channels/new")
async def create_channel(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    name: str = Form(""),
    description: str = Form(""),
    channel_type: str = Form("GENERAL"),
    grade_filter: str = Form(""),
    function_filter: str = Form(""),
    is_readonly: str = Form(""),
):
    user, member = ctx
    if not (user.is_admin or member.lodge_function in (LodgeFunction.VM, LodgeFunction.SECRETAIRE)):
        raise HTTPException(status_code=403)

    name = name.strip()
    if not name:
        return RedirectResponse(url="/chat/", status_code=303)

    try:
        ch_type = ChannelType(channel_type)
    except ValueError:
        ch_type = ChannelType.GENERAL

    channel = ChatChannel(
        name=name,
        description=description.strip() or None,
        type=ch_type,
        grade_filter=grade_filter or None,
        function_filter=function_filter or None,
        is_readonly=bool(is_readonly),
        created_by_id=member.id,
    )
    db.add(channel)
    await db.flush()

    if ch_type in (ChannelType.COMMISSION, ChannelType.DIRECT):
        db.add(ChatChannelMember(channel_id=channel.id, member_id=member.id))

    await db.commit()
    return RedirectResponse(url=f"/chat/{channel.id}", status_code=303)


# ── Créer un groupe libre (tous les membres) ──────────────────────────────────

@router.post("/groups/new")
async def create_group(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    name: str = Form(""),
    description: str = Form(""),
    member_ids: str = Form(""),
):
    user, member = ctx
    name = name.strip()
    if not name:
        return RedirectResponse(url="/chat/", status_code=303)

    channel = ChatChannel(
        name=name,
        description=description.strip() or None,
        type=ChannelType.COMMISSION,
        created_by_id=member.id,
    )
    db.add(channel)
    await db.flush()

    db.add(ChatChannelMember(channel_id=channel.id, member_id=member.id))
    seen = {member.id}
    for mid_str in member_ids.split(","):
        mid_str = mid_str.strip()
        if mid_str.isdigit():
            mid = int(mid_str)
            if mid not in seen:
                seen.add(mid)
                db.add(ChatChannelMember(channel_id=channel.id, member_id=mid))

    await db.commit()
    return RedirectResponse(url=f"/chat/{channel.id}", status_code=303)


# ── Démarrer une discussion directe ──────────────────────────────────────────

@router.post("/dm")
async def start_dm(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    target_id: int = Form(0),
):
    user, member = ctx
    if not target_id or target_id == member.id:
        return RedirectResponse(url="/chat/", status_code=303)

    # Chercher un canal DIRECT existant entre ces deux membres
    my_dm_r = await db.execute(
        select(ChatChannelMember.channel_id)
        .join(ChatChannel, ChatChannel.id == ChatChannelMember.channel_id)
        .where(ChatChannelMember.member_id == member.id, ChatChannel.type == ChannelType.DIRECT)
    )
    my_dm_ids = {row[0] for row in my_dm_r.all()}

    if my_dm_ids:
        existing_r = await db.execute(
            select(ChatChannelMember.channel_id)
            .where(
                ChatChannelMember.member_id == target_id,
                ChatChannelMember.channel_id.in_(my_dm_ids),
            )
        )
        existing_id = existing_r.scalar_one_or_none()
        if existing_id:
            return RedirectResponse(url=f"/chat/{existing_id}", status_code=303)

    target = await db.get(Member, target_id)
    if not target:
        return RedirectResponse(url="/chat/", status_code=303)

    channel = ChatChannel(
        name=f"{member.first_name} & {target.first_name}",
        type=ChannelType.DIRECT,
        created_by_id=member.id,
    )
    db.add(channel)
    await db.flush()
    db.add(ChatChannelMember(channel_id=channel.id, member_id=member.id))
    db.add(ChatChannelMember(channel_id=channel.id, member_id=target.id))
    await db.commit()
    return RedirectResponse(url=f"/chat/{channel.id}", status_code=303)


# ── API unread global ─────────────────────────────────────────────────────────

@router.get("/api/unread")
async def chat_unread_count(
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    channels = await _accessible_channels(member, db)
    ch_ids = [c.id for c in channels]
    unread = await _unread_count_per_channel(member.id, ch_ids, db)
    total = sum(unread.values())
    return JSONResponse({"total": total, "per_channel": unread})
