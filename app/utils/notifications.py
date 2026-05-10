"""Notifications internes — envoie un message interne à un ensemble de membres."""
import json
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import Member, MemberStatus, MasonicGrade
from app.models.messaging import Message, MessageRecipient, MessageTargetType

_GRADE_ORDER = {
    MasonicGrade.APPRENTI: 1,
    MasonicGrade.COMPAGNON: 2,
    MasonicGrade.MAITRE: 3,
}


async def send_notification(
    db: AsyncSession,
    sender_id: int,
    subject: str,
    body: str,
    min_grade: Optional[str] = None,
    target_group_id: Optional[int] = None,
    push_url: str = "/messages",
    push_body: Optional[str] = None,
) -> None:
    """Envoie un message interne à tous les membres actifs éligibles (hors expéditeur).
    Envoie également une notification push aux abonnés."""
    r = await db.execute(
        select(Member).where(Member.status == MemberStatus.ACTIVE)
    )
    all_members = r.scalars().all()

    if target_group_id:
        from app.models.groups import LodgeGroup, GroupMembership, GroupType
        group = await db.get(LodgeGroup, target_group_id)
        if not group:
            return
        if group.group_type == GroupType.GRADE:
            if group.grade_filter:
                recipient_ids = [
                    m.id for m in all_members
                    if m.id != sender_id and m.masonic_grade and m.masonic_grade.value == group.grade_filter
                ]
            else:
                recipient_ids = [m.id for m in all_members if m.id != sender_id]
        elif group.group_type == GroupType.COUNCIL:
            from app.models.identity import LodgeFunction
            OFFICER = {
                LodgeFunction.VM, LodgeFunction.PREMIER_S, LodgeFunction.SECOND_S,
                LodgeFunction.ORATEUR, LodgeFunction.SECRETAIRE, LodgeFunction.TRESORIER,
                LodgeFunction.EXPERT, LodgeFunction.MAITRE_CEREMONIES,
            }
            recipient_ids = [m.id for m in all_members if m.id != sender_id and m.lodge_function in OFFICER]
        elif group.group_type == GroupType.PAIR:
            functions = set(json.loads(group.function_filter or "[]"))
            recipient_ids = [
                m.id for m in all_members
                if m.id != sender_id and m.lodge_function and m.lodge_function.value in functions
            ]
        else:
            gm_r = await db.execute(
                select(GroupMembership.member_id).where(GroupMembership.group_id == target_group_id)
            )
            group_member_ids = {row[0] for row in gm_r.all()}
            recipient_ids = [m.id for m in all_members if m.id != sender_id and m.id in group_member_ids]
        target_type = MessageTargetType.GROUP
        target_filter = json.dumps({"group_id": target_group_id})
    elif min_grade:
        required = _GRADE_ORDER.get(MasonicGrade(min_grade), 1)
        recipient_ids = [
            m.id for m in all_members
            if m.id != sender_id
            and _GRADE_ORDER.get(m.masonic_grade, 0) >= required
        ]
        target_type = MessageTargetType.GRADE
        target_filter = json.dumps({"grade": min_grade})
    else:
        recipient_ids = [m.id for m in all_members if m.id != sender_id]
        target_type = MessageTargetType.ALL
        target_filter = None

    if not recipient_ids:
        return

    msg = Message(
        subject=subject,
        body=body,
        sender_id=sender_id,
        target_type=target_type,
        target_filter=target_filter,
        sent_at=datetime.now(),
    )
    db.add(msg)
    await db.flush()

    now = datetime.now()
    for mid in recipient_ids:
        db.add(MessageRecipient(
            message_id=msg.id,
            member_id=mid,
            delivered_at=now,
        ))

    # ── Push notifications ────────────────────────────────────────────
    try:
        from app.services.push import send_push_broadcast
        # Tronquer le titre (limite OS) et le corps
        push_title = subject[:80]
        pb = (push_body or body or "").strip()
        # Nettoyer les retours à la ligne, tronquer
        pb = " ".join(pb.split())[:140]
        await send_push_broadcast(db, recipient_ids, push_title, pb, push_url)
    except Exception:
        pass  # ne jamais bloquer l'envoi du message interne sur un échec push
