"""Router Forum & Discussions — style Framavox/Loomio.

Architecture:
- ForumTheme : catégorie (Group)
- ForumSubject : fil de discussion (Thread)
- ForumMessage : post (Comment)
- ForumDecision : proposition de décision inline (Proposal)
- ForumStance : position d'un membre (4 valeurs ✓✗⊘⛔ + raison)
"""
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional, Union

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select, func, desc, delete
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_auth, can_manage_members
from app.models.identity import Member, MemberStatus, LodgeFunction
from app.models.groups import LodgeGroup, GroupMembership, GroupType
from app.models.forum import (
    ForumTheme, ForumSubject, ForumMessage,
    ForumSubscription, ForumDecision, ForumStance, StancePosition,
    ForumAttachment, AttachmentKind,
)
from app.models.documents import Document, DocStatus, DocFolder
from app.utils.tz import to_paris

UPLOAD_DIR = Path("uploads/forum")
MAX_UPLOAD_SIZE = 25 * 1024 * 1024  # 25 Mo

ALLOWED_LINK_PREFIXES = ("http://", "https://")

router = APIRouter(prefix="/forum", tags=["forum"])
from app.template_engine import templates

_GRADE_ORDER = {"APPRENTI": 1, "COMPAGNON": 2, "MAITRE": 3}

_OFFICER_FUNCTIONS = {
    LodgeFunction.VM, LodgeFunction.PREMIER_S, LodgeFunction.SECOND_S,
    LodgeFunction.ORATEUR, LodgeFunction.SECRETAIRE, LodgeFunction.TRESORIER,
    LodgeFunction.EXPERT, LodgeFunction.MAITRE_CEREMONIES, LodgeFunction.HARMONISTE,
    LodgeFunction.HOSPITALIER, LodgeFunction.TUILEUR, LodgeFunction.ARCHITECTE,
    LodgeFunction.MAITRE_BANQUETS,
}


def _is_admin_or_manager(user, member) -> bool:
    return bool(getattr(user, "is_admin", False) or can_manage_members(member))


async def _check_group_access(member: Member, group_id: int, db: AsyncSession) -> bool:
    group = await db.get(LodgeGroup, group_id)
    if not group:
        return False
    if group.group_type == GroupType.GRADE:
        if group.grade_filter is None:
            return True
        return bool(member.masonic_grade and member.masonic_grade.value == group.grade_filter)
    elif group.group_type == GroupType.COUNCIL:
        return member.lodge_function in _OFFICER_FUNCTIONS
    elif group.group_type == GroupType.PAIR:
        import json
        functions = set(json.loads(group.function_filter or "[]"))
        return bool(member.lodge_function and member.lodge_function.value in functions)
    else:
        r = await db.execute(
            select(GroupMembership).where(
                GroupMembership.group_id == group_id,
                GroupMembership.member_id == member.id,
            )
        )
        return r.scalar_one_or_none() is not None


async def _can_access_theme(theme: ForumTheme, member: Member, is_admin: bool, db: AsyncSession) -> bool:
    if is_admin:
        return True
    if theme.group_id:
        return await _check_group_access(member, theme.group_id, db)
    if theme.min_grade:
        return _GRADE_ORDER.get(member.masonic_grade.value, 0) >= _GRADE_ORDER.get(theme.min_grade, 0)
    return True


async def _load_groups(db: AsyncSession) -> list[LodgeGroup]:
    r = await db.execute(select(LodgeGroup).order_by(LodgeGroup.name))
    return r.scalars().all()


def _parse_target(target: str) -> tuple[Optional[str], Optional[int]]:
    if not target:
        return None, None
    if target.startswith("group:"):
        try:
            return None, int(target[6:])
        except ValueError:
            return None, None
    return target, None


def _target_value(theme: ForumTheme) -> str:
    if theme.group_id:
        return f"group:{theme.group_id}"
    return theme.min_grade or ""


async def _attach_to_message(
    db: AsyncSession,
    message_id: int,
    files: Optional[list[UploadFile]],
    links_raw: str,
    document_ids_raw: str,
):
    """Crée les ForumAttachment pour un message :
    - files : uploads
    - links_raw : URLs séparées par sauts de ligne (optionnel libellé après ' | ')
    - document_ids_raw : IDs GED séparés par virgules
    """
    # Files
    if files:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        for f in files:
            if not f or not f.filename:
                continue
            data = await f.read()
            if not data:
                continue
            if len(data) > MAX_UPLOAD_SIZE:
                continue  # silencieusement ignoré
            ext = Path(f.filename).suffix.lower()
            fname = f"forum_{uuid.uuid4().hex}{ext}"
            dest = UPLOAD_DIR / fname
            dest.write_bytes(data)
            db.add(ForumAttachment(
                message_id=message_id,
                kind=AttachmentKind.FILE,
                label=f.filename,
                storage_path=str(dest),
                original_filename=f.filename,
                mime_type=f.content_type,
                file_size=len(data),
            ))

    # Liens
    if links_raw and links_raw.strip():
        for line in links_raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if "|" in line:
                url, _, lab = line.partition("|")
                url, lab = url.strip(), lab.strip()
            else:
                url, lab = line, None
            if not url.startswith(ALLOWED_LINK_PREFIXES):
                continue
            db.add(ForumAttachment(
                message_id=message_id,
                kind=AttachmentKind.LINK,
                label=lab or url,
                url=url,
            ))

    # Documents GED
    if document_ids_raw and document_ids_raw.strip():
        ids = []
        for tok in document_ids_raw.replace(";", ",").split(","):
            tok = tok.strip()
            if tok.isdigit():
                ids.append(int(tok))
        if ids:
            r = await db.execute(select(Document).where(Document.id.in_(ids)))
            for doc in r.scalars().all():
                db.add(ForumAttachment(
                    message_id=message_id,
                    kind=AttachmentKind.DOCUMENT,
                    label=doc.name,
                    document_id=doc.id,
                ))


# ─────────────────────────────────────────────────────────────────────────────
#  INDEX — liste des catégories + activité récente
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def forum_index(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx

    # Catégories ordonnées, filtrées par ciblage (grade/groupe)
    r = await db.execute(
        select(ForumTheme).order_by(ForumTheme.order_position, ForumTheme.id)
    )
    all_themes = r.scalars().all()
    themes = [th for th in all_themes if await _can_access_theme(th, member, user.is_admin, db)]

    # Comptes par thème — 3 requêtes fixes au lieu de 3×N
    sub_counts_r = await db.execute(
        select(ForumSubject.theme_id, func.count(ForumSubject.id))
        .group_by(ForumSubject.theme_id)
    )
    sub_counts = {row[0]: row[1] for row in sub_counts_r.all()}

    msg_counts_r = await db.execute(
        select(ForumSubject.theme_id, func.count(ForumMessage.id))
        .join(ForumMessage, ForumMessage.subject_id == ForumSubject.id)
        .group_by(ForumSubject.theme_id)
    )
    msg_counts = {row[0]: row[1] for row in msg_counts_r.all()}

    # Dernier sujet par thème : on charge tous les sujets triés, on garde le 1er par thème
    all_subs_r = await db.execute(
        select(ForumSubject)
        .order_by(desc(ForumSubject.last_message_at), desc(ForumSubject.created_at))
    )
    _last_by_theme: dict[int, ForumSubject] = {}
    for s in all_subs_r.scalars().all():
        if s.theme_id not in _last_by_theme:
            _last_by_theme[s.theme_id] = s

    theme_stats: dict[int, dict] = {
        th.id: {
            "subjects": sub_counts.get(th.id, 0),
            "messages": msg_counts.get(th.id, 0),
            "last_subject": _last_by_theme.get(th.id),
        }
        for th in themes
    }

    # Activité récente : 10 derniers sujets actifs
    r2 = await db.execute(
        select(ForumSubject)
        .options(selectinload(ForumSubject.theme))
        .order_by(desc(ForumSubject.last_message_at), desc(ForumSubject.created_at))
        .limit(10)
    )
    recent_subjects = r2.scalars().all()

    return templates.TemplateResponse(request, "pages/forum/index.html", {
        "current_user": user,
        "current_member": member,
        "themes": themes,
        "theme_stats": theme_stats,
        "recent_subjects": recent_subjects,
        "can_manage": _is_admin_or_manager(user, member),
        "groups": await _load_groups(db),
    })


# ─────────────────────────────────────────────────────────────────────────────
#  CATÉGORIE — liste des sujets
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/c/{theme_id}", response_class=HTMLResponse)
async def forum_category(
    theme_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    th = (await db.execute(
        select(ForumTheme).where(ForumTheme.id == theme_id)
    )).scalar_one_or_none()
    if not th:
        raise HTTPException(404, "Catégorie introuvable")
    if not await _can_access_theme(th, member, user.is_admin, db):
        raise HTTPException(403, "Catégorie réservée")

    r = await db.execute(
        select(ForumSubject)
        .where(ForumSubject.theme_id == theme_id)
        .order_by(desc(ForumSubject.is_pinned), desc(ForumSubject.last_message_at), desc(ForumSubject.created_at))
    )
    subjects = r.scalars().all()

    # Compteurs messages par sujet — 1 GROUP BY au lieu de 1 COUNT×N
    subject_ids = [s.id for s in subjects]
    counts: dict[int, int] = {}
    if subject_ids:
        counts_r = await db.execute(
            select(ForumMessage.subject_id, func.count(ForumMessage.id))
            .where(ForumMessage.subject_id.in_(subject_ids))
            .group_by(ForumMessage.subject_id)
        )
        counts = {row[0]: row[1] for row in counts_r.all()}

    # Auteurs (création)
    author_ids = {s.created_by_id for s in subjects if s.created_by_id}
    authors: dict[int, Member] = {}
    if author_ids:
        ar = await db.execute(select(Member).where(Member.id.in_(author_ids)))
        for m in ar.scalars().all():
            authors[m.id] = m

    return templates.TemplateResponse(request, "pages/forum/category.html", {
        "current_user": user,
        "current_member": member,
        "theme": th,
        "subjects": subjects,
        "counts": counts,
        "authors": authors,
        "can_manage": _is_admin_or_manager(user, member),
    })


# ─────────────────────────────────────────────────────────────────────────────
#  SUJET — détail (messages + décisions inline)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/t/{subject_id}", response_class=HTMLResponse)
async def forum_thread(
    subject_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    s = (await db.execute(
        select(ForumSubject)
        .options(selectinload(ForumSubject.theme))
        .where(ForumSubject.id == subject_id)
    )).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Sujet introuvable")
    if not await _can_access_theme(s.theme, member, user.is_admin, db):
        raise HTTPException(403, "Catégorie réservée")

    # Messages + attachments
    mr = await db.execute(
        select(ForumMessage)
        .options(selectinload(ForumMessage.attachments))
        .where(ForumMessage.subject_id == subject_id, ForumMessage.deleted_at.is_(None))
        .order_by(ForumMessage.created_at)
    )
    messages = mr.scalars().all()

    # Décisions (avec stances)
    dr = await db.execute(
        select(ForumDecision)
        .options(selectinload(ForumDecision.stances))
        .where(ForumDecision.subject_id == subject_id)
        .order_by(desc(ForumDecision.created_at))
    )
    decisions = dr.scalars().all()

    # Auteurs
    author_ids = {m.created_by_id for m in messages if m.created_by_id}
    author_ids |= {d.created_by_id for d in decisions if d.created_by_id}
    if s.created_by_id:
        author_ids.add(s.created_by_id)
    for d in decisions:
        for st in d.stances:
            author_ids.add(st.member_id)
    authors: dict[int, Member] = {}
    if author_ids:
        ar = await db.execute(select(Member).where(Member.id.in_(author_ids)))
        for m in ar.scalars().all():
            authors[m.id] = m

    # Stances structurées par décision
    decision_data = []
    for d in decisions:
        counts = {p.value: 0 for p in StancePosition}
        my_stance = None
        for st in d.stances:
            counts[st.position.value if hasattr(st.position, "value") else st.position] += 1
            if st.member_id == member.id:
                my_stance = st
        decision_data.append({
            "decision": d,
            "counts": counts,
            "total": sum(counts.values()),
            "my_stance": my_stance,
            "is_open": d.closed_at is None,
        })

    # Abonnement
    sub_r = await db.execute(
        select(ForumSubscription).where(
            ForumSubscription.subject_id == subject_id,
            ForumSubscription.member_id == member.id,
        )
    )
    is_subscribed = sub_r.scalar_one_or_none() is not None

    return templates.TemplateResponse(request, "pages/forum/thread.html", {
        "current_user": user,
        "current_member": member,
        "subject": s,
        "messages": messages,
        "decision_data": decision_data,
        "authors": authors,
        "is_subscribed": is_subscribed,
        "can_manage": _is_admin_or_manager(user, member),
        "StancePosition": StancePosition,
    })


# ─────────────────────────────────────────────────────────────────────────────
#  Page nouveau sujet
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
async def forum_new_form(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    theme: Optional[int] = None,
):
    user, member = ctx
    r = await db.execute(
        select(ForumTheme).order_by(ForumTheme.order_position, ForumTheme.id)
    )
    all_themes = r.scalars().all()
    themes = [th for th in all_themes if await _can_access_theme(th, member, user.is_admin, db)]
    return templates.TemplateResponse(request, "pages/forum/new.html", {
        "current_user": user,
        "current_member": member,
        "themes": themes,
        "preselected_theme": theme,
    })


# ─────────────────────────────────────────────────────────────────────────────
#  Création sujet
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/new")
async def forum_create_subject(
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    theme_id: int = Form(...),
    title: str = Form(...),
    content: str = Form(...),
    files: Union[list[UploadFile], UploadFile, None] = File(default_factory=list),
    links: str = Form(""),
    document_ids: str = Form(""),
):
    user, member = ctx

    # <input type="file" multiple> soumis sans fichier envoie une seule partie
    # vide — Starlette la remonte comme un UploadFile isolé, pas une liste.
    if files is None:
        files = []
    elif not isinstance(files, list):
        files = [files]

    th = (await db.execute(
        select(ForumTheme).where(ForumTheme.id == theme_id)
    )).scalar_one_or_none()
    if not th:
        raise HTTPException(404, "Catégorie introuvable")
    if not await _can_access_theme(th, member, user.is_admin, db):
        raise HTTPException(403, "Catégorie réservée")

    now = datetime.utcnow()
    s = ForumSubject(
        theme_id=theme_id,
        title=title.strip(),
        created_by_id=member.id,
        last_message_at=now,
    )
    db.add(s)
    await db.flush()

    msg = ForumMessage(
        subject_id=s.id,
        content_html=content.strip(),
        created_by_id=member.id,
    )
    db.add(msg)
    await db.flush()

    await _attach_to_message(db, msg.id, files, links, document_ids)

    # Auto-abonnement de l'auteur
    db.add(ForumSubscription(
        subject_id=s.id, member_id=member.id, notify_by_email=True,
    ))

    await db.commit()
    return RedirectResponse(url=f"/forum/t/{s.id}", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
#  Répondre à un sujet
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/t/{subject_id}/post")
async def forum_post(
    subject_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    content: str = Form(...),
    parent_id: Optional[int] = Form(None),
    files: Union[list[UploadFile], UploadFile, None] = File(default_factory=list),
    links: str = Form(""),
    document_ids: str = Form(""),
):
    user, member = ctx

    # <input type="file" multiple> soumis sans fichier envoie une seule partie
    # vide — Starlette la remonte comme un UploadFile isolé, pas une liste.
    if files is None:
        files = []
    elif not isinstance(files, list):
        files = [files]

    s = (await db.execute(
        select(ForumSubject).options(selectinload(ForumSubject.theme)).where(ForumSubject.id == subject_id)
    )).scalar_one_or_none()
    if not s:
        raise HTTPException(404)
    if not await _can_access_theme(s.theme, member, user.is_admin, db):
        raise HTTPException(403, "Catégorie réservée")
    if s.is_locked and not _is_admin_or_manager(user, member):
        raise HTTPException(403, "Sujet verrouillé")

    text = content.strip()
    if not text:
        raise HTTPException(400, "Message vide")

    msg = ForumMessage(
        subject_id=subject_id,
        parent_id=parent_id,
        content_html=text,
        created_by_id=member.id,
    )
    db.add(msg)
    await db.flush()
    await _attach_to_message(db, msg.id, files, links, document_ids)
    s.last_message_at = datetime.utcnow()

    # Auto-abonnement à la réponse
    existing = (await db.execute(
        select(ForumSubscription).where(
            ForumSubscription.subject_id == subject_id,
            ForumSubscription.member_id == member.id,
        )
    )).scalar_one_or_none()
    if not existing:
        db.add(ForumSubscription(
            subject_id=subject_id, member_id=member.id, notify_by_email=True,
        ))

    await db.commit()

    # Push aux abonnés (sauf l'auteur)
    try:
        from app.services.push import send_push_broadcast
        sr = await db.execute(
            select(ForumSubscription.member_id).where(
                ForumSubscription.subject_id == subject_id,
                ForumSubscription.member_id != member.id,
            )
        )
        ids = [row[0] for row in sr.all()]
        if ids:
            preview = (text[:140] + "…") if len(text) > 140 else text
            await send_push_broadcast(
                db, ids,
                f"💬 {s.title}",
                f"{member.first_name or 'Un membre'} : {preview}",
                f"/forum/t/{subject_id}",
            )
    except Exception:
        pass

    return RedirectResponse(url=f"/forum/t/{subject_id}#m-{msg.id}", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
#  Modération d'un message (auteur ou admin/gestionnaire)
# ─────────────────────────────────────────────────────────────────────────────

def _can_moderate_message(msg: ForumMessage, user, member) -> bool:
    return bool(
        (member and msg.created_by_id == member.id) or _is_admin_or_manager(user, member)
    )


@router.get("/messages/{message_id}/edit", response_class=HTMLResponse)
async def forum_message_edit_form(
    message_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    msg = await db.get(ForumMessage, message_id)
    if not msg or msg.deleted_at:
        raise HTTPException(404)
    if not _can_moderate_message(msg, user, member):
        raise HTTPException(403)
    return templates.TemplateResponse(request, "pages/forum/message_edit.html", {
        "current_user": user,
        "current_member": member,
        "msg": msg,
    })


@router.post("/messages/{message_id}/edit")
async def forum_message_edit(
    message_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    content: str = Form(...),
):
    user, member = ctx
    msg = await db.get(ForumMessage, message_id)
    if not msg or msg.deleted_at:
        raise HTTPException(404)
    if not _can_moderate_message(msg, user, member):
        raise HTTPException(403)
    text = content.strip()
    if not text:
        raise HTTPException(400, "Message vide")
    msg.content_html = text
    await db.commit()
    return RedirectResponse(url=f"/forum/t/{msg.subject_id}#m-{msg.id}", status_code=303)


@router.post("/messages/{message_id}/delete")
async def forum_message_delete(
    message_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    msg = await db.get(ForumMessage, message_id)
    if not msg or msg.deleted_at:
        raise HTTPException(404)
    if not _can_moderate_message(msg, user, member):
        raise HTTPException(403)
    subject_id = msg.subject_id
    msg.deleted_at = datetime.utcnow()
    await db.commit()
    return RedirectResponse(url=f"/forum/t/{subject_id}", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
#  Décision (proposition de vote inline)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/t/{subject_id}/decision")
async def forum_propose_decision(
    subject_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    title: str = Form(...),
    description: str = Form(""),
    closes_at: str = Form(""),
):
    user, member = ctx
    s = (await db.execute(
        select(ForumSubject).where(ForumSubject.id == subject_id)
    )).scalar_one_or_none()
    if not s:
        raise HTTPException(404)
    if s.is_locked and not _is_admin_or_manager(user, member):
        raise HTTPException(403, "Sujet verrouillé")

    closes = None
    if closes_at.strip():
        try:
            closes = datetime.fromisoformat(closes_at.strip())
        except ValueError:
            pass

    d = ForumDecision(
        subject_id=subject_id,
        title=title.strip(),
        description_html=description.strip() or None,
        closes_at=closes,
        created_by_id=member.id,
    )
    db.add(d)
    s.last_message_at = datetime.utcnow()
    await db.commit()
    await db.refresh(d)

    # Push aux abonnés
    try:
        from app.services.push import send_push_broadcast
        sr = await db.execute(
            select(ForumSubscription.member_id).where(
                ForumSubscription.subject_id == subject_id,
                ForumSubscription.member_id != member.id,
            )
        )
        ids = [row[0] for row in sr.all()]
        if ids:
            await send_push_broadcast(
                db, ids,
                f"🗳️ Nouvelle décision proposée",
                d.title[:140],
                f"/forum/t/{subject_id}#d-{d.id}",
            )
    except Exception:
        pass

    return RedirectResponse(url=f"/forum/t/{subject_id}#d-{d.id}", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
#  Stance (vote sur décision)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/decisions/{decision_id}/stance")
async def forum_set_stance(
    decision_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    position: str = Form(...),
    reason: str = Form(""),
):
    user, member = ctx
    d = (await db.execute(
        select(ForumDecision).where(ForumDecision.id == decision_id)
    )).scalar_one_or_none()
    if not d:
        raise HTTPException(404)
    if d.closed_at is not None:
        raise HTTPException(403, "Décision clôturée")

    try:
        pos = StancePosition(position)
    except ValueError:
        raise HTTPException(400, "Position invalide")

    # Upsert
    existing = (await db.execute(
        select(ForumStance).where(
            ForumStance.decision_id == decision_id,
            ForumStance.member_id == member.id,
        )
    )).scalar_one_or_none()

    if existing:
        existing.position = pos
        existing.reason = reason.strip() or None
        existing.updated_at = datetime.utcnow()
    else:
        db.add(ForumStance(
            decision_id=decision_id,
            member_id=member.id,
            position=pos,
            reason=reason.strip() or None,
        ))

    await db.commit()
    return RedirectResponse(url=f"/forum/t/{d.subject_id}#d-{decision_id}", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
#  Clôturer une décision (proposeur ou admin)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/decisions/{decision_id}/close")
async def forum_close_decision(
    decision_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    outcome: str = Form(""),
):
    user, member = ctx
    d = (await db.execute(
        select(ForumDecision).where(ForumDecision.id == decision_id)
    )).scalar_one_or_none()
    if not d:
        raise HTTPException(404)
    if d.created_by_id != member.id and not _is_admin_or_manager(user, member):
        raise HTTPException(403, "Réservé au proposeur ou à un admin")

    d.closed_at = datetime.utcnow()
    if outcome.strip():
        d.outcome_html = outcome.strip()
    await db.commit()

    # Push aux abonnés
    try:
        from app.services.push import send_push_broadcast
        sr = await db.execute(
            select(ForumSubscription.member_id).where(
                ForumSubscription.subject_id == d.subject_id,
                ForumSubscription.member_id != member.id,
            )
        )
        ids = [row[0] for row in sr.all()]
        if ids:
            await send_push_broadcast(
                db, ids,
                f"✅ Décision clôturée",
                d.title[:140],
                f"/forum/t/{d.subject_id}#d-{d.id}",
            )
    except Exception:
        pass

    return RedirectResponse(url=f"/forum/t/{d.subject_id}#d-{decision_id}", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
#  Gestion catégories (admin)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/themes/new")
async def forum_create_theme(
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    name: str = Form(...),
    color: str = Form(""),
    description: str = Form(""),
    order_position: int = Form(0),
    target: str = Form(""),
):
    user, member = ctx
    if not _is_admin_or_manager(user, member):
        raise HTTPException(403)

    min_grade, target_group_id = _parse_target(target)
    th = ForumTheme(
        name=name.strip(),
        color=color.strip() or None,
        description=description.strip() or None,
        order_position=order_position,
        created_by_id=member.id,
        min_grade=min_grade,
        group_id=target_group_id,
    )
    db.add(th)
    await db.commit()
    return RedirectResponse(url="/forum/", status_code=303)


@router.get("/themes/{theme_id}/edit", response_class=HTMLResponse)
async def forum_theme_edit_form(
    theme_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not _is_admin_or_manager(user, member):
        raise HTTPException(403)
    th = await db.get(ForumTheme, theme_id)
    if not th:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "pages/forum/theme_edit.html", {
        "current_user": user,
        "current_member": member,
        "theme": th,
        "groups": await _load_groups(db),
        "target_value": _target_value(th),
    })


@router.post("/themes/{theme_id}/edit")
async def forum_theme_edit(
    theme_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    name: str = Form(...),
    color: str = Form(""),
    description: str = Form(""),
    order_position: int = Form(0),
    target: str = Form(""),
):
    user, member = ctx
    if not _is_admin_or_manager(user, member):
        raise HTTPException(403)
    th = await db.get(ForumTheme, theme_id)
    if not th:
        raise HTTPException(404)

    min_grade, target_group_id = _parse_target(target)
    th.name = name.strip()
    th.color = color.strip() or None
    th.description = description.strip() or None
    th.order_position = order_position
    th.min_grade = min_grade
    th.group_id = target_group_id
    await db.commit()
    return RedirectResponse(url="/forum/", status_code=303)


@router.post("/themes/{theme_id}/delete")
async def forum_delete_theme(
    theme_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not _is_admin_or_manager(user, member):
        raise HTTPException(403)
    th = (await db.execute(
        select(ForumTheme).where(ForumTheme.id == theme_id)
    )).scalar_one_or_none()
    if not th:
        raise HTTPException(404)
    await db.delete(th)
    await db.commit()
    return RedirectResponse(url="/forum/", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
#  Modération sujet (pin / lock / delete)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/t/{subject_id}/pin")
async def forum_toggle_pin(
    subject_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not _is_admin_or_manager(user, member):
        raise HTTPException(403)
    s = (await db.execute(
        select(ForumSubject).where(ForumSubject.id == subject_id)
    )).scalar_one_or_none()
    if not s:
        raise HTTPException(404)
    s.is_pinned = not s.is_pinned
    await db.commit()
    return RedirectResponse(url=f"/forum/t/{subject_id}", status_code=303)


@router.post("/t/{subject_id}/lock")
async def forum_toggle_lock(
    subject_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not _is_admin_or_manager(user, member):
        raise HTTPException(403)
    s = (await db.execute(
        select(ForumSubject).where(ForumSubject.id == subject_id)
    )).scalar_one_or_none()
    if not s:
        raise HTTPException(404)
    s.is_locked = not s.is_locked
    await db.commit()
    return RedirectResponse(url=f"/forum/t/{subject_id}", status_code=303)


@router.post("/t/{subject_id}/delete")
async def forum_delete_subject(
    subject_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    s = (await db.execute(
        select(ForumSubject).where(ForumSubject.id == subject_id)
    )).scalar_one_or_none()
    if not s:
        raise HTTPException(404)
    if not (_is_admin_or_manager(user, member) or s.created_by_id == member.id):
        raise HTTPException(403)
    theme_id = s.theme_id

    # Suppression manuelle des dépendances (évite la composite PK + FK
    # qui pose problème avec le cascade ORM).
    # 1) Stances liées aux décisions du sujet
    await db.execute(
        delete(ForumStance).where(
            ForumStance.decision_id.in_(
                select(ForumDecision.id).where(ForumDecision.subject_id == subject_id)
            )
        )
    )
    # 2) Décisions
    await db.execute(delete(ForumDecision).where(ForumDecision.subject_id == subject_id))
    # 3) Pièces jointes des messages
    await db.execute(
        delete(ForumAttachment).where(
            ForumAttachment.message_id.in_(
                select(ForumMessage.id).where(ForumMessage.subject_id == subject_id)
            )
        )
    )
    # 4) Messages
    await db.execute(delete(ForumMessage).where(ForumMessage.subject_id == subject_id))
    # 5) Abonnements
    await db.execute(delete(ForumSubscription).where(ForumSubscription.subject_id == subject_id))
    # 6) Sujet
    await db.execute(delete(ForumSubject).where(ForumSubject.id == subject_id))
    await db.commit()
    return RedirectResponse(url=f"/forum/c/{theme_id}", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
#  Abonnement (subscribe / unsubscribe)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/t/{subject_id}/subscribe")
async def forum_subscribe(
    subject_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    existing = (await db.execute(
        select(ForumSubscription).where(
            ForumSubscription.subject_id == subject_id,
            ForumSubscription.member_id == member.id,
        )
    )).scalar_one_or_none()
    if not existing:
        db.add(ForumSubscription(
            subject_id=subject_id, member_id=member.id, notify_by_email=True,
        ))
        await db.commit()
    return RedirectResponse(url=f"/forum/t/{subject_id}", status_code=303)


@router.post("/t/{subject_id}/unsubscribe")
async def forum_unsubscribe(
    subject_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    existing = (await db.execute(
        select(ForumSubscription).where(
            ForumSubscription.subject_id == subject_id,
            ForumSubscription.member_id == member.id,
        )
    )).scalar_one_or_none()
    if existing:
        await db.delete(existing)
        await db.commit()
    return RedirectResponse(url=f"/forum/t/{subject_id}", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
#  Téléchargement d'une pièce jointe
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/attachments/{att_id}/download")
async def forum_attachment_download(
    att_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    att = (await db.execute(
        select(ForumAttachment).where(ForumAttachment.id == att_id)
    )).scalar_one_or_none()
    if not att or att.kind != AttachmentKind.FILE:
        raise HTTPException(404)
    if not att.storage_path or not os.path.exists(att.storage_path):
        raise HTTPException(404, "Fichier introuvable sur le disque")
    return FileResponse(
        att.storage_path,
        media_type=att.mime_type or "application/octet-stream",
        filename=att.original_filename or "fichier",
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Export PDF d'un fil — synthèse imprimable
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/t/{subject_id}/export.pdf")
async def forum_export_pdf(
    subject_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    import io
    import re as _re
    from fastapi.responses import StreamingResponse
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
    )

    user, member = ctx

    s = (await db.execute(
        select(ForumSubject)
        .options(selectinload(ForumSubject.theme))
        .where(ForumSubject.id == subject_id)
    )).scalar_one_or_none()
    if not s:
        raise HTTPException(404)

    mr = await db.execute(
        select(ForumMessage)
        .options(selectinload(ForumMessage.attachments))
        .where(ForumMessage.subject_id == subject_id, ForumMessage.deleted_at.is_(None))
        .order_by(ForumMessage.created_at)
    )
    messages = mr.scalars().all()

    dr = await db.execute(
        select(ForumDecision)
        .options(selectinload(ForumDecision.stances))
        .where(ForumDecision.subject_id == subject_id)
        .order_by(ForumDecision.created_at)
    )
    decisions = dr.scalars().all()

    ids = {m.created_by_id for m in messages if m.created_by_id}
    ids |= {d.created_by_id for d in decisions if d.created_by_id}
    if s.created_by_id:
        ids.add(s.created_by_id)
    for d in decisions:
        for st in d.stances:
            ids.add(st.member_id)
    authors: dict[int, Member] = {}
    if ids:
        ar = await db.execute(select(Member).where(Member.id.in_(ids)))
        for m in ar.scalars().all():
            authors[m.id] = m

    def _author_name(mid):
        a = authors.get(mid)
        if not a:
            return "—"
        return f"{a.last_name or ''} {a.first_name or ''}".strip() or "—"

    def _strip(html: str) -> str:
        if not html:
            return ""
        text = _re.sub(r"<br\s*/?>", "\n", html, flags=_re.I)
        text = _re.sub(r"</p\s*>", "\n", text, flags=_re.I)
        text = _re.sub(r"<[^>]+>", " ", text)
        text = (text.replace("&", "&amp;")
                    .replace("<", "&lt;").replace(">", "&gt;"))
        return text.strip()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm,
        title=s.title, author="Portail Socrate",
    )

    TEAL_DARK  = colors.HexColor("#1a5252")
    TEAL       = colors.HexColor("#2c7a7b")
    GRAY       = colors.HexColor("#374151")
    GRAY_LIGHT = colors.HexColor("#9ca3af")
    BG_DEC     = colors.HexColor("#ecfdf5")
    BG_DEC_BR  = colors.HexColor("#a7f3d0")

    styles = getSampleStyleSheet()
    H1     = ParagraphStyle("H1", parent=styles["Normal"], fontSize=16, textColor=TEAL_DARK,
                             fontName="Helvetica-Bold", spaceAfter=4, leading=20)
    META   = ParagraphStyle("META", parent=styles["Normal"], fontSize=9, textColor=GRAY_LIGHT,
                             fontName="Helvetica", spaceAfter=4)
    BODY   = ParagraphStyle("BODY", parent=styles["Normal"], fontSize=10, textColor=GRAY,
                             fontName="Helvetica", leading=14, spaceAfter=4)
    AUTHOR = ParagraphStyle("AUTHOR", parent=styles["Normal"], fontSize=10, textColor=TEAL_DARK,
                             fontName="Helvetica-Bold", leading=12)
    TIME   = ParagraphStyle("TIME", parent=styles["Normal"], fontSize=8, textColor=GRAY_LIGHT,
                             fontName="Helvetica", alignment=2)
    DEC_T  = ParagraphStyle("DT", parent=styles["Normal"], fontSize=12, textColor=TEAL_DARK,
                             fontName="Helvetica-Bold", leading=15, spaceAfter=4)
    SECTION= ParagraphStyle("SEC", parent=styles["Normal"], fontSize=11, textColor=TEAL_DARK,
                             fontName="Helvetica-Bold", leading=14, spaceBefore=10, spaceAfter=6)
    SMALL  = ParagraphStyle("SM", parent=styles["Normal"], fontSize=8, textColor=GRAY,
                             fontName="Helvetica", leading=10)

    story = []
    story.append(Paragraph(s.title, H1))
    story.append(Paragraph(
        f"Catégorie : {s.theme.name if s.theme else '—'} · "
        f"Créé par {_author_name(s.created_by_id)} le {to_paris(s.created_at).strftime('%d/%m/%Y à %H:%M')} · "
        f"{len(messages)} message(s) · {len(decisions)} décision(s)",
        META,
    ))
    story.append(HRFlowable(width="100%", color=TEAL, thickness=1.2, spaceBefore=4, spaceAfter=10))

    if messages:
        story.append(Paragraph("Échanges", SECTION))
        for m in messages:
            head = Table(
                [[Paragraph(_author_name(m.created_by_id), AUTHOR),
                  Paragraph(to_paris(m.created_at).strftime('%d/%m/%Y %H:%M'), TIME)]],
                colWidths=[doc.width * 0.6, doc.width * 0.4],
            )
            head.setStyle(TableStyle([
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]))
            story.append(head)
            text = _strip(m.content_html) or "—"
            for para in text.split("\n"):
                if para.strip():
                    story.append(Paragraph(para, BODY))
            if m.attachments:
                bits = []
                for a in m.attachments:
                    k = a.kind.value if hasattr(a.kind, "value") else a.kind
                    if k == "FILE":
                        bits.append(f"[Fichier] {a.original_filename or a.label or 'fichier'}")
                    elif k == "LINK":
                        bits.append(f"[Lien] {a.label or a.url}")
                    elif k == "DOCUMENT":
                        bits.append(f"[GED] {a.label}")
                story.append(Paragraph(" · ".join(bits), SMALL))
            story.append(HRFlowable(width="100%", color=colors.HexColor("#e5e7eb"),
                                     thickness=0.4, spaceBefore=4, spaceAfter=8))

    if decisions:
        story.append(Paragraph("Décisions", SECTION))
        for d in decisions:
            counts = {p.value: 0 for p in StancePosition}
            for st in d.stances:
                k = st.position.value if hasattr(st.position, "value") else st.position
                counts[k] = counts.get(k, 0) + 1
            total = sum(counts.values())

            status_lbl = "Clôturée" if d.closed_at else "En cours"
            meta = (f"Proposée par {_author_name(d.created_by_id)} le "
                    f"{to_paris(d.created_at).strftime('%d/%m/%Y')}  ·  Statut : {status_lbl}")
            if d.closed_at:
                meta += f" (le {to_paris(d.closed_at).strftime('%d/%m/%Y')})"

            inner = [
                [Paragraph(d.title, DEC_T)],
                [Paragraph(meta, SMALL)],
            ]
            if d.description_html:
                inner.append([Paragraph(_strip(d.description_html), BODY)])

            inner.append([Paragraph(
                f"<b>Résultats</b> — {total} vote(s) :  "
                f"Accord : <b>{counts.get('AGREE', 0)}</b>  ·  "
                f"Désaccord : <b>{counts.get('DISAGREE', 0)}</b>  ·  "
                f"Abstention : <b>{counts.get('ABSTAIN', 0)}</b>  ·  "
                f"Bloque : <b>{counts.get('BLOCK', 0)}</b>",
                BODY,
            )])

            if d.stances:
                detail_lines = []
                for st in d.stances:
                    k = st.position.value if hasattr(st.position, "value") else st.position
                    sym = {"AGREE": "[+]", "DISAGREE": "[-]", "ABSTAIN": "[o]", "BLOCK": "[X]"}.get(k, "[?]")
                    line = f"{sym} <b>{_author_name(st.member_id)}</b>"
                    if st.reason:
                        line += f" — {_strip(st.reason)}"
                    detail_lines.append(line)
                inner.append([Paragraph("<br/>".join(detail_lines), SMALL)])

            if d.outcome_html:
                inner.append([Paragraph("<b>Résultat / synthèse :</b>", BODY)])
                inner.append([Paragraph(_strip(d.outcome_html), BODY)])

            tbl = Table(inner, colWidths=[doc.width - 0.4*cm])
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), BG_DEC),
                ("BOX", (0, 0), (-1, -1), 0.6, BG_DEC_BR),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(tbl)
            story.append(Spacer(1, 0.5*cm))

    story.append(Spacer(1, 0.6*cm))
    story.append(HRFlowable(width="100%", color=GRAY_LIGHT, thickness=0.4))
    story.append(Paragraph(
        f"Synthèse générée le {datetime.now().strftime('%d/%m/%Y à %H:%M')} "
        f"par {_author_name(member.id)} — Portail Socrate",
        SMALL,
    ))

    doc.build(story)
    buf.seek(0)

    safe_title = _re.sub(r"[^A-Za-z0-9_-]+", "_", s.title)[:60] or f"forum_{s.id}"
    filename = f"forum_{s.id}_{safe_title}.pdf"
    return StreamingResponse(
        buf, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# ─────────────────────────────────────────────────────────────────────────────
#  API JSON : recherche de documents GED (pour le picker)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/api/documents")
async def forum_search_documents(
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    q: str = "",
):
    """Recherche dans la GED — renvoie une liste JSON [{id, name, folder}] des
    documents publiés ou validés, filtrés par titre."""
    user, member = ctx
    stmt = (
        select(Document, DocFolder.name)
        .join(DocFolder, DocFolder.id == Document.folder_id)
        .where(
            Document.deleted_at.is_(None),
            Document.status.in_([DocStatus.PUBLISHED, DocStatus.VALIDATED]),
        )
        .order_by(desc(Document.updated_at))
        .limit(40)
    )
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(Document.name.ilike(like))
    rows = (await db.execute(stmt)).all()
    return {
        "documents": [
            {
                "id": d.id,
                "name": d.name,
                "folder": folder_name,
                "mime_type": d.mime_type,
                "is_link": bool(d.link_url),
            }
            for d, folder_name in rows
        ]
    }
