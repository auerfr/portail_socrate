"""Router Chat — Messagerie instantanée (remplace Telegram)"""
import re
from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from markupsafe import Markup, escape as _escape
from sqlalchemy import select, delete, func as sql_func, or_, and_
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_auth
from app.models.identity import Member, MasonicGrade, LodgeFunction, MemberStatus
from app.models.chat import (
    ChatChannel, ChatChannelMember, ChatMessage, ChatRead,
    ChannelType, MessageContentType,
)
from app.models.groups import LodgeGroup

router = APIRouter(prefix="/chat", tags=["chat"])
from app.template_engine import templates

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
        elif ch.created_by_id == member.id:
            # Créateur d'un canal : toujours accès, quel que soit le type/filtre
            accessible.append(ch)
        elif ch.type == ChannelType.GRADE:
            required_order = GRADE_ORDER.get(MasonicGrade(ch.grade_filter), 0) if ch.grade_filter else 0
            if member_grade_order >= required_order:
                accessible.append(ch)
        elif ch.type == ChannelType.FUNCTION:
            if member.lodge_function in (LodgeFunction.VM, LodgeFunction.SECRETAIRE):
                accessible.append(ch)  # VM et Secrétaire voient tous les canaux officiers
            elif ch.function_filter:
                if member.lodge_function.value == ch.function_filter:
                    accessible.append(ch)
            else:
                # Pas de filtre → visible par tous les officiers (lodge_function ≠ FRERE)
                if member.lodge_function != LodgeFunction.FRERE:
                    accessible.append(ch)
        elif ch.type == ChannelType.DIRECT:
            if ch.id in member_channel_ids:
                accessible.append(ch)
        elif ch.type == ChannelType.COMMISSION:
            if ch.id in member_channel_ids:
                accessible.append(ch)  # membre explicite (priorité sur le filtre groupe)
            elif ch.lodge_group_id:
                from app.routers.groups import resolve_group_member_ids
                grp = await db.get(LodgeGroup, ch.lodge_group_id)
                if grp:
                    ids = await resolve_group_member_ids(db, grp)
                    if member.id in ids:
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

    lodge_groups: list[LodgeGroup] = []
    if can_manage:
        lg_r = await db.execute(select(LodgeGroup).order_by(LodgeGroup.name))
        lodge_groups = lg_r.scalars().all()

    return templates.TemplateResponse(request, "pages/chat/index.html", {
        "current_member": member,
        "current_user": user,
        "channels": channels,
        "active_channel": None,
        "messages": [],
        "unread": unread,
        "active_members": active_members,
        "can_manage": can_manage,
        "can_manage_channel": False,
        "channel_members": [],
        "channel_admin_ids": set(),
        "lodge_groups": lodge_groups,
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

    # Membres du canal courant (COMMISSION sans restriction de groupe)
    channel_members: list[Member] = []
    channel_member_rows: list[ChatChannelMember] = []
    if channel.type == ChannelType.COMMISSION and not channel.lodge_group_id:
        cm_r = await db.execute(
            select(ChatChannelMember)
            .options(selectinload(ChatChannelMember.member))
            .where(ChatChannelMember.channel_id == channel.id)
        )
        channel_member_rows = cm_r.scalars().all()
        channel_members = [cm.member for cm in channel_member_rows]

    # Admin de canal : créateur ou membre marqué is_admin
    is_channel_admin = (
        channel.created_by_id == member.id
        or any(cm.member_id == member.id and cm.is_admin for cm in channel_member_rows)
    )
    can_manage_channel = can_manage or is_channel_admin

    # Admins du canal pour affichage
    channel_admin_ids = {
        cm.member_id for cm in channel_member_rows if cm.is_admin
    } | ({channel.created_by_id} if channel.created_by_id else set())

    # Groupes de loge pour la restriction (admins)
    lodge_groups: list[LodgeGroup] = []
    if can_manage:
        lg_r = await db.execute(select(LodgeGroup).order_by(LodgeGroup.name))
        lodge_groups = lg_r.scalars().all()

    # Statut de lecture des autres membres (coches ✓/✓✓)
    read_max_r = await db.execute(
        select(sql_func.max(ChatRead.last_read_message_id))
        .where(
            ChatRead.channel_id == channel_id,
            ChatRead.member_id != member.id,
        )
    )
    max_other_read_id = read_max_r.scalar() or 0

    return templates.TemplateResponse(request, "pages/chat/index.html", {
        "current_member": member,
        "current_user": user,
        "channels": channels,
        "active_channel": channel,
        "messages": messages,
        "unread": unread,
        "active_members": active_members,
        "can_manage": can_manage,
        "can_manage_channel": can_manage_channel,
        "channel_members": channel_members,
        "channel_admin_ids": channel_admin_ids,
        "lodge_groups": lodge_groups,
        "max_other_read_id": max_other_read_id,
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

    # Statut de lecture des autres (pour mise à jour des coches côté client)
    read_max_r = await db.execute(
        select(sql_func.max(ChatRead.last_read_message_id))
        .where(
            ChatRead.channel_id == channel_id,
            ChatRead.member_id != member.id,
        )
    )
    others_max_read = read_max_r.scalar() or 0

    def _msg_json(m: ChatMessage) -> dict:
        reply = None
        if m.reply_to:
            reply = {
                "id": m.reply_to.id,
                "sender": f"{m.reply_to.sender.last_name} {m.reply_to.sender.first_name}",
                "preview": (m.reply_to.content or "")[:80],
            }
        from app.services.presence import presence_status
        return {
            "id": m.id,
            "sender_id": m.sender_id,
            "sender_name": f"{m.sender.last_name} {m.sender.first_name}",
            "sender_initials": f"{m.sender.first_name[0]}{m.sender.last_name[0]}",
            "is_mine": m.sender_id == member.id,
            "content": m.content or "",
            "content_type": m.content_type.value,
            "created_at": m.created_at.strftime("%H:%M"),
            "created_date": m.created_at.strftime("%d/%m/%Y"),
            "reply": reply,
            "presence": presence_status(m.sender),
        }

    return JSONResponse({
        "messages": [_msg_json(m) for m in new_msgs],
        "last_id": new_msgs[-1].id if new_msgs else since_id,
        "others_max_read": others_max_read,
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

    # ── Push notifications aux membres du canal (sauf l'expéditeur) ──────
    try:
        from app.services.push import send_push_broadcast
        members_r = await db.execute(
            select(ChatChannelMember.member_id).where(ChatChannelMember.channel_id == channel_id)
        )
        member_ids = [row[0] for row in members_r.all() if row[0] != member.id]
        if member_ids:
            sender_name = f"{member.last_name} {member.first_name}"
            push_body = " ".join(content.split())[:140]
            await send_push_broadcast(
                db, member_ids,
                f"💬 {channel.name} — {sender_name}",
                push_body,
                f"/chat/{channel_id}",
            )
    except Exception:
        pass

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


# ── Ajouter un membre à un canal ─────────────────────────────────────────────

@router.post("/channels/{channel_id}/members/add")
async def channel_add_member(
    channel_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    member_id: int = Form(...),
):
    user, member = ctx
    channel = await db.get(ChatChannel, channel_id)
    if not channel:
        raise HTTPException(404)
    can_manage = user.is_admin or member.lodge_function in (LodgeFunction.VM, LodgeFunction.SECRETAIRE)
    if not can_manage and channel.created_by_id != member.id:
        raise HTTPException(403)
    exists = (await db.execute(
        select(ChatChannelMember).where(
            ChatChannelMember.channel_id == channel_id,
            ChatChannelMember.member_id == member_id,
        )
    )).scalar_one_or_none()
    if not exists:
        db.add(ChatChannelMember(channel_id=channel_id, member_id=member_id))
        await db.commit()
    return RedirectResponse(url=f"/chat/{channel_id}", status_code=303)


# ── Promouvoir/rétrograder admin canal ───────────────────────────────────────

@router.post("/channels/{channel_id}/members/admin/{target_member_id}")
async def channel_toggle_admin(
    channel_id: int,
    target_member_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    is_admin: str = Form("0"),
):
    user, member = ctx
    channel = await db.get(ChatChannel, channel_id)
    if not channel:
        raise HTTPException(404)
    can_manage = user.is_admin or member.lodge_function in (LodgeFunction.VM, LodgeFunction.SECRETAIRE)
    if not can_manage and channel.created_by_id != member.id:
        raise HTTPException(403)
    cm = (await db.execute(
        select(ChatChannelMember).where(
            ChatChannelMember.channel_id == channel_id,
            ChatChannelMember.member_id == target_member_id,
        )
    )).scalar_one_or_none()
    if cm:
        cm.is_admin = is_admin in ("1", "true", "on")
        await db.commit()
    return RedirectResponse(url=f"/chat/{channel_id}", status_code=303)


# ── Retirer un membre d'un canal ──────────────────────────────────────────────

@router.post("/channels/{channel_id}/members/remove/{target_member_id}")
async def channel_remove_member(
    channel_id: int,
    target_member_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    channel = await db.get(ChatChannel, channel_id)
    if not channel:
        raise HTTPException(404)
    can_manage = user.is_admin or member.lodge_function in (LodgeFunction.VM, LodgeFunction.SECRETAIRE)
    if not can_manage and channel.created_by_id != member.id:
        raise HTTPException(403)
    await db.execute(
        delete(ChatChannelMember).where(
            ChatChannelMember.channel_id == channel_id,
            ChatChannelMember.member_id == target_member_id,
        )
    )
    await db.commit()
    return RedirectResponse(url=f"/chat/{channel_id}", status_code=303)


# ── Modifier un canal (nom, groupe de restriction) ────────────────────────────

@router.post("/channels/{channel_id}/edit")
async def channel_edit(
    channel_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    name: str = Form(""),
    description: str = Form(""),
    lodge_group_id: Optional[int] = Form(None),
):
    user, member = ctx
    channel = await db.get(ChatChannel, channel_id)
    if not channel:
        raise HTTPException(404)
    can_manage = user.is_admin or member.lodge_function in (LodgeFunction.VM, LodgeFunction.SECRETAIRE)
    if not can_manage and channel.created_by_id != member.id:
        raise HTTPException(403)
    if name.strip():
        channel.name = name.strip()
    channel.description = description.strip() or None
    channel.lodge_group_id = lodge_group_id if lodge_group_id and lodge_group_id > 0 else None
    await db.commit()
    return RedirectResponse(url=f"/chat/{channel_id}", status_code=303)


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
    lodge_group_id: Optional[int] = Form(None),
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
        lodge_group_id=lodge_group_id if lodge_group_id and lodge_group_id > 0 else None,
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
    lodge_group_id: Optional[int] = Form(None),
):
    user, member = ctx
    name = name.strip()
    if not name:
        return RedirectResponse(url="/chat/", status_code=303)

    channel = ChatChannel(
        name=name,
        description=description.strip() or None,
        type=ChannelType.COMMISSION,
        lodge_group_id=lodge_group_id if lodge_group_id and lodge_group_id > 0 else None,
        created_by_id=member.id,
    )
    db.add(channel)
    await db.flush()

    db.add(ChatChannelMember(channel_id=channel.id, member_id=member.id, is_admin=True))
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
