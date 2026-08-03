"""Centre de notifications — agrège les événements récents pertinents pour un
membre (messages non lus, planches publiées, sondages ouverts, activité forum)
sans table d'événements dédiée : tout est recalculé à la lecture, à l'image de
la recherche globale (app/routers/search.py)."""
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import Member
from app.models.messaging import Message, MessageRecipient
from app.models.planches import Planche, PlancheStatus
from app.models.forum import ForumSubject
from app.models.content import Poll, PollVote

WINDOW_DAYS = 30
MAX_EVENTS = 20


async def get_notification_events(db: AsyncSession, user, member: Member, limit: int = MAX_EVENTS) -> list[dict]:
    events: list[dict] = []
    since = datetime.now() - timedelta(days=WINDOW_DAYS)
    seen_at: Optional[datetime] = member.notifications_seen_at

    # ── Messages non lus ─────────────────────────────────────────────────
    if member.notif_messages:
        r = await db.execute(
            select(Message)
            .join(MessageRecipient, MessageRecipient.message_id == Message.id)
            .where(
                MessageRecipient.member_id == member.id,
                MessageRecipient.read_at.is_(None),
                MessageRecipient.deleted_at.is_(None),
                Message.sent_at.isnot(None),
            )
            .order_by(Message.sent_at.desc())
            .limit(limit)
        )
        for msg in r.scalars().all():
            events.append({
                "type": "message", "icon": "ti-mail", "label": msg.subject,
                "sub": "Nouveau message", "url": f"/messages/{msg.id}", "ts": msg.sent_at,
            })

    # ── Planches publiées récemment (filtrées par grade) ────────────────
    if member.notif_planches:
        from app.routers.planches import _can_read
        r = await db.execute(
            select(Planche)
            .where(
                Planche.status == PlancheStatus.PUBLIE,
                Planche.published_at.isnot(None),
                Planche.published_at >= since,
            )
            .order_by(Planche.published_at.desc())
            .limit(limit)
        )
        for p in r.scalars().all():
            if not _can_read(member, p):
                continue
            events.append({
                "type": "planche", "icon": "ti-feather", "label": p.title,
                "sub": "Nouvelle planche", "url": f"/planches/{p.id}", "ts": p.published_at,
            })

    # ── Sondages ouverts non votés ───────────────────────────────────────
    if member.notif_polls:
        from app.routers.polls import _can_access, _is_open
        r = await db.execute(
            select(Poll).where(Poll.created_at >= since).order_by(Poll.created_at.desc()).limit(limit * 2)
        )
        polls = r.scalars().all()
        if polls:
            voted_r = await db.execute(
                select(PollVote.poll_id).where(
                    PollVote.member_id == member.id,
                    PollVote.poll_id.in_([p.id for p in polls]),
                )
            )
            voted_ids = {row[0] for row in voted_r.all()}
            for p in polls:
                if p.id in voted_ids or not _is_open(p):
                    continue
                if not await _can_access(p, member, user.is_admin, db):
                    continue
                events.append({
                    "type": "poll", "icon": "ti-chart-bar", "label": p.title,
                    "sub": "Sondage ouvert", "url": f"/polls/{p.id}", "ts": p.created_at,
                })

    # ── Activité forum récente ───────────────────────────────────────────
    if member.notif_forum:
        r = await db.execute(
            select(ForumSubject)
            .where(ForumSubject.last_message_at.isnot(None), ForumSubject.last_message_at >= since)
            .order_by(ForumSubject.last_message_at.desc())
            .limit(limit)
        )
        for s in r.scalars().all():
            events.append({
                "type": "forum", "icon": "ti-messages", "label": s.title,
                "sub": "Activité forum", "url": f"/forum/t/{s.id}", "ts": s.last_message_at,
            })

    events.sort(key=lambda e: e["ts"], reverse=True)
    events = events[:limit]
    for e in events:
        e["is_new"] = seen_at is None or e["ts"] > seen_at
        e["ts_str"] = e["ts"].strftime("%d/%m %H:%M")
    return events


async def get_notifications_count(db: AsyncSession, user, member: Member) -> int:
    """Compte les notifications non vues via COUNT SQL directs — sans charger d'objets."""
    seen_at = member.notifications_seen_at
    since = datetime.now() - timedelta(days=WINDOW_DAYS)
    count = 0

    # Messages non lus (toujours "new" car jamais vus = unread)
    if member.notif_messages:
        r = await db.execute(
            select(func.count()).select_from(Message)
            .join(MessageRecipient, MessageRecipient.message_id == Message.id)
            .where(
                MessageRecipient.member_id == member.id,
                MessageRecipient.read_at.is_(None),
                MessageRecipient.deleted_at.is_(None),
                Message.sent_at.isnot(None),
            )
        )
        count += r.scalar_one() or 0

    if seen_at is None:
        # Rien vu encore → tout est nouveau, pas besoin de compter plus précisément
        return min(count + 5, 9)

    # Planches publiées après seen_at (filtre grade omis pour éviter N+1 — approximation ok pour le badge)
    if member.notif_planches:
        r = await db.execute(
            select(func.count()).select_from(Planche)
            .where(
                Planche.status == PlancheStatus.PUBLIE,
                Planche.published_at.isnot(None),
                Planche.published_at > seen_at,
                Planche.published_at >= since,
            )
        )
        count += r.scalar_one() or 0

    # Sondages ouverts non votés créés après seen_at
    if member.notif_polls:
        voted_r = await db.execute(
            select(PollVote.poll_id).where(PollVote.member_id == member.id)
        )
        voted_ids = {row[0] for row in voted_r.all()}
        r = await db.execute(
            select(Poll).where(
                Poll.created_at > seen_at,
                Poll.created_at >= since,
                Poll.id.not_in(voted_ids) if voted_ids else True,
            )
        )
        for p in r.scalars().all():
            from app.routers.polls import _is_open
            if _is_open(p):
                count += 1

    # Forum — nouveaux sujets actifs après seen_at
    if member.notif_forum:
        r = await db.execute(
            select(func.count()).select_from(ForumSubject)
            .where(
                ForumSubject.last_message_at.isnot(None),
                ForumSubject.last_message_at > seen_at,
                ForumSubject.last_message_at >= since,
            )
        )
        count += r.scalar_one() or 0

    return count
