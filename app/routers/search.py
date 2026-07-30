"""Recherche globale — agrège membres, documents, forum, messages, tenues et planches."""
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import require_auth
from app.models.identity import Member
from app.models.documents import Document, DocStatus
from app.models.forum import ForumSubject, ForumMessage
from app.models.messaging import Message, MessageRecipient
from app.models.meetings import Meeting
from app.models.planches import Planche

router = APIRouter(tags=["search"])
from app.template_engine import templates

RESULTS_PER_CATEGORY = 8


@router.get("/search", response_class=HTMLResponse)
async def global_search(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    q: str = "",
):
    """Recherche transversale dans les modules du portail, filtrée selon les
    droits d'accès du membre connecté (mêmes règles que chaque module)."""
    user, member = ctx
    q = q.strip()
    categories = []

    if len(q) >= 2:
        like = f"%{q}%"

        # ── Membres ──────────────────────────────────────────────────────
        r = await db.execute(
            select(Member)
            .where(or_(Member.first_name.ilike(like), Member.last_name.ilike(like), Member.email.ilike(like)))
            .limit(RESULTS_PER_CATEGORY)
        )
        members_hits = r.scalars().all()
        if members_hits:
            categories.append({
                "key": "members", "label": "Membres", "icon": "ti-users",
                "entries": [{"label": f"{m.last_name} {m.first_name}", "sub": m.email, "url": f"/members/{m.id}"} for m in members_hits],
            })

        # ── Documents (recherche full-text existante) ───────────────────
        from app.services.doc_index import search_documents
        from app.routers.documents import _can_access
        doc_hits = await search_documents(db, q, limit=30)
        if doc_hits:
            doc_ids = [h["doc_id"] for h in doc_hits]
            docs_r = await db.execute(
                select(Document).options(selectinload(Document.folder))
                .where(Document.id.in_(doc_ids), Document.status == DocStatus.PUBLISHED)
            )
            docs = {d.id: d for d in docs_r.scalars().all()}
            doc_items = []
            for h in doc_hits:
                doc = docs.get(h["doc_id"])
                if not doc or not doc.folder:
                    continue
                folder = doc.folder
                if not await _can_access(
                    member, user, folder.min_grade, folder.group_id, db,
                    personal_owner_id=folder.personal_owner_id,
                ):
                    continue
                doc_items.append({"label": doc.name, "sub": folder.name, "url": f"/documents/file/{doc.id}/view"})
                if len(doc_items) >= RESULTS_PER_CATEGORY:
                    break
            if doc_items:
                categories.append({"key": "documents", "label": "Bibliothèque", "icon": "ti-folder", "entries": doc_items})

        # ── Forum (sujets + messages) ────────────────────────────────────
        r = await db.execute(
            select(ForumSubject)
            .where(ForumSubject.title.ilike(like))
            .order_by(ForumSubject.last_message_at.desc().nullslast())
            .limit(RESULTS_PER_CATEGORY)
        )
        subject_hits = r.scalars().all()
        r2 = await db.execute(
            select(ForumMessage)
            .where(ForumMessage.content_html.ilike(like), ForumMessage.deleted_at.is_(None))
            .order_by(ForumMessage.created_at.desc())
            .limit(RESULTS_PER_CATEGORY)
        )
        message_hits = r2.scalars().all()
        forum_items = [{"label": s.title, "sub": "Sujet", "url": f"/forum/t/{s.id}"} for s in subject_hits]
        seen_subjects = {s.id for s in subject_hits}
        for msg in message_hits:
            if msg.subject_id in seen_subjects:
                continue
            forum_items.append({"label": _strip_html(msg.content_html)[:120], "sub": "Message", "url": f"/forum/t/{msg.subject_id}#msg-{msg.id}"})
        if forum_items:
            categories.append({"key": "forum", "label": "Forum", "icon": "ti-messages", "entries": forum_items[:RESULTS_PER_CATEGORY]})

        # ── Messages (reçus ou envoyés par le membre) ───────────────────
        r = await db.execute(
            select(Message)
            .outerjoin(MessageRecipient, MessageRecipient.message_id == Message.id)
            .where(
                or_(Message.subject.ilike(like), Message.body.ilike(like)),
                or_(
                    and_(Message.sender_id == member.id, Message.sender_deleted_at.is_(None)),
                    and_(MessageRecipient.member_id == member.id, MessageRecipient.deleted_at.is_(None)),
                ),
            )
            .order_by(Message.sent_at.desc().nullslast())
            .distinct()
            .limit(RESULTS_PER_CATEGORY)
        )
        msg_hits = r.scalars().all()
        if msg_hits:
            categories.append({
                "key": "messages", "label": "Messages", "icon": "ti-mail",
                "entries": [{"label": m.subject, "sub": m.sent_at.strftime("%d/%m/%Y") if m.sent_at else "", "url": f"/messages/{m.id}"} for m in msg_hits],
            })

        # ── Tenues ───────────────────────────────────────────────────────
        r = await db.execute(
            select(Meeting)
            .where(or_(Meeting.title.ilike(like), Meeting.theme.ilike(like)))
            .order_by(Meeting.meeting_date.desc())
            .limit(RESULTS_PER_CATEGORY)
        )
        meeting_hits = r.scalars().all()
        if meeting_hits:
            categories.append({
                "key": "meetings", "label": "Tenues", "icon": "ti-building-arch",
                "entries": [{"label": m.title or (m.theme or "Tenue"), "sub": m.meeting_date.strftime("%d/%m/%Y"), "url": f"/meetings/{m.id}"} for m in meeting_hits],
            })

        # ── Planches (filtrées par grade) ───────────────────────────────
        from app.routers.planches import _can_read
        r = await db.execute(
            select(Planche)
            .where(or_(Planche.title.ilike(like), Planche.content.ilike(like)), Planche.status == "PUBLIE")
            .order_by(Planche.published_at.desc().nullslast())
            .limit(30)
        )
        planche_hits = [p for p in r.scalars().all() if _can_read(member, p)][:RESULTS_PER_CATEGORY]
        if planche_hits:
            categories.append({
                "key": "planches", "label": "Planches", "icon": "ti-feather",
                "entries": [{"label": p.title, "sub": p.author.last_name + " " + p.author.first_name if p.author else "", "url": f"/planches/{p.id}"} for p in planche_hits],
            })

    return templates.TemplateResponse(request, "pages/search/results.html", {
        "current_user": user,
        "current_member": member,
        "q": q,
        "categories": categories,
    })


def _strip_html(html: str) -> str:
    import re
    return re.sub(r"<[^>]+>", " ", html or "").strip()
