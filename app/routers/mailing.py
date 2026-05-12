"""Router Listes de diffusion."""
import asyncio
import json
from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_auth
from app.models.identity import Member, MemberStatus, LodgeFunction
from app.models.mailing import (
    MailingList, MailingListMember, MailingListType,
    MailingCampaign, MailingDelivery, CampaignStatus, DeliveryStatus,
)
from app.models.documents import Document, DocStatus
from app.services.mailing import (
    resolve_recipients, send_campaign_async,
    verify_unsubscribe_token, make_unsubscribe_token,
)

router = APIRouter(prefix="/mailing", tags=["mailing"])
templates = Jinja2Templates(directory="app/templates")


def _can_send(user, member) -> bool:
    if user.is_admin:
        return True
    if not member or not member.lodge_function:
        return False
    return member.lodge_function in (
        LodgeFunction.VM, LodgeFunction.SECRETAIRE,
        LodgeFunction.PREMIER_S, LodgeFunction.SECOND_S,
        LodgeFunction.TRESORIER,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Index
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def mailing_index(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not _can_send(user, member):
        raise HTTPException(403, "Accès réservé aux officiers (VM/Surveillants/Secrétaire/Trésorier)")

    lists = (await db.execute(
        select(MailingList).order_by(desc(MailingList.is_system), MailingList.name)
    )).scalars().all()

    # Compteurs par liste (pour les listes statiques uniquement, sinon "dynamique")
    counts: dict[int, int] = {}
    for ml in lists:
        if ml.list_type == MailingListType.STATIC:
            r = await db.execute(
                select(func.count(MailingListMember.member_id)).where(
                    MailingListMember.list_id == ml.id,
                    MailingListMember.unsubscribed_at.is_(None),
                )
            )
            counts[ml.id] = r.scalar() or 0
        else:
            # Dynamic : on calcule
            recipients = await resolve_recipients(db, ml)
            counts[ml.id] = len(recipients)

    # Dernières campagnes (5)
    recent = (await db.execute(
        select(MailingCampaign).order_by(desc(MailingCampaign.created_at)).limit(5)
    )).scalars().all()
    lists_by_id = {ml.id: ml for ml in lists}

    return templates.TemplateResponse(request, "pages/mailing/index.html", {
        "current_user": user, "current_member": member,
        "lists": lists, "counts": counts,
        "recent_campaigns": recent, "lists_by_id": lists_by_id,
        "CampaignStatus": CampaignStatus,
    })


# ─────────────────────────────────────────────────────────────────────────────
#  Création liste
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/lists/new")
async def list_create(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    name: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    list_type: Annotated[str, Form()] = "STATIC",
    # critères pour DYNAMIC
    criteria_grade: Annotated[list[str], Form()] = None,
    criteria_lodge_function: Annotated[list[str], Form()] = None,
    criteria_status: Annotated[list[str], Form()] = None,
    criteria_group_ids: Annotated[list[str], Form()] = None,
):
    user, member = ctx
    if not _can_send(user, member):
        raise HTTPException(403)

    crit = None
    if list_type == "DYNAMIC":
        crit = {}
        if criteria_status:    crit["status"] = criteria_status
        if criteria_grade:     crit["grade"] = criteria_grade
        if criteria_lodge_function: crit["lodge_function"] = criteria_lodge_function
        if criteria_group_ids:
            crit["group_ids"] = [int(g) for g in criteria_group_ids if g.isdigit()]

    ml = MailingList(
        name=name.strip(),
        description=description.strip() or None,
        list_type=MailingListType(list_type) if list_type in MailingListType.__members__ else MailingListType.STATIC,
        criteria=crit,
        created_by_id=member.id,
    )
    db.add(ml)
    await db.commit()
    await db.refresh(ml)
    return RedirectResponse(url=f"/mailing/lists/{ml.id}", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
#  Détail liste
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/lists/{list_id}", response_class=HTMLResponse)
async def list_detail(
    list_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not _can_send(user, member):
        raise HTTPException(403)

    ml = await db.get(MailingList, list_id)
    if not ml:
        raise HTTPException(404)

    # Destinataires actuels (résolus)
    recipients = await resolve_recipients(db, ml)

    # Désinscrits
    rs = await db.execute(
        select(MailingListMember).where(
            MailingListMember.list_id == ml.id,
            MailingListMember.unsubscribed_at.isnot(None),
        )
    )
    unsubscribed_rows = rs.scalars().all()
    unsub_ids = {r.member_id for r in unsubscribed_rows}
    unsub_members = {}
    if unsub_ids:
        mr = await db.execute(select(Member).where(Member.id.in_(unsub_ids)))
        for m in mr.scalars().all():
            unsub_members[m.id] = m

    # Tous les membres actifs pour le sélecteur "ajouter"
    all_active = []
    if ml.list_type == MailingListType.STATIC:
        ar = await db.execute(
            select(Member).where(Member.status == MemberStatus.ACTIVE)
            .order_by(Member.last_name, Member.first_name)
        )
        all_active = list(ar.scalars().all())
        recipient_ids = {m.id for m in recipients}
        all_active = [m for m in all_active if m.id not in recipient_ids]

    # Historique campagnes
    cr = await db.execute(
        select(MailingCampaign).where(MailingCampaign.list_id == ml.id)
        .order_by(desc(MailingCampaign.created_at)).limit(20)
    )
    campaigns = cr.scalars().all()

    return templates.TemplateResponse(request, "pages/mailing/list_detail.html", {
        "current_user": user, "current_member": member,
        "mlist": ml,
        "recipients": recipients,
        "unsubscribed_rows": unsubscribed_rows,
        "unsub_members": unsub_members,
        "all_active": all_active,
        "campaigns": campaigns,
        "CampaignStatus": CampaignStatus,
    })


@router.post("/lists/{list_id}/members/add")
async def list_member_add(
    list_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    member_id: Annotated[int, Form()],
):
    user, member = ctx
    if not _can_send(user, member):
        raise HTTPException(403)
    ml = await db.get(MailingList, list_id)
    if not ml:
        raise HTTPException(404)
    # Vérifie qu'il n'existe pas déjà
    r = await db.execute(
        select(MailingListMember).where(
            MailingListMember.list_id == list_id,
            MailingListMember.member_id == member_id,
        )
    )
    existing = r.scalar_one_or_none()
    if existing:
        existing.unsubscribed_at = None  # réabonnement
    else:
        db.add(MailingListMember(list_id=list_id, member_id=member_id))
    await db.commit()
    return RedirectResponse(url=f"/mailing/lists/{list_id}", status_code=303)


@router.post("/lists/{list_id}/members/{member_id}/remove")
async def list_member_remove(
    list_id: int, member_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not _can_send(user, member):
        raise HTTPException(403)
    r = await db.execute(
        select(MailingListMember).where(
            MailingListMember.list_id == list_id,
            MailingListMember.member_id == member_id,
        )
    )
    row = r.scalar_one_or_none()
    if row:
        # Pour liste statique : suppression. Pour dynamique : on garde avec unsubscribed_at
        ml = await db.get(MailingList, list_id)
        if ml and ml.list_type == MailingListType.DYNAMIC:
            row.unsubscribed_at = datetime.utcnow()
        else:
            await db.delete(row)
        await db.commit()
    return RedirectResponse(url=f"/mailing/lists/{list_id}", status_code=303)


@router.post("/lists/{list_id}/delete")
async def list_delete(
    list_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not user.is_admin:
        raise HTTPException(403, "Admin uniquement")
    ml = await db.get(MailingList, list_id)
    if not ml:
        raise HTTPException(404)
    if ml.is_system:
        raise HTTPException(400, "Liste système non supprimable")
    await db.delete(ml)
    await db.commit()
    return RedirectResponse(url="/mailing/", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
#  Composer + envoyer
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/lists/{list_id}/compose", response_class=HTMLResponse)
async def compose_new(
    list_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    draft_id: Optional[int] = None,
):
    user, member = ctx
    if not _can_send(user, member):
        raise HTTPException(403)
    ml = await db.get(MailingList, list_id)
    if not ml:
        raise HTTPException(404)

    draft = None
    if draft_id:
        draft = await db.get(MailingCampaign, draft_id)
        if draft and draft.list_id != list_id:
            draft = None

    recipients = await resolve_recipients(db, ml)

    # Documents publiables depuis la GED pour PJ
    docs = (await db.execute(
        select(Document).where(
            Document.status == DocStatus.PUBLISHED,
            Document.storage_path.isnot(None),
        ).order_by(desc(Document.updated_at)).limit(100)
    )).scalars().all()

    return templates.TemplateResponse(request, "pages/mailing/compose.html", {
        "current_user": user, "current_member": member,
        "mlist": ml, "recipients": recipients, "docs": docs,
        "draft": draft,
    })


@router.post("/lists/{list_id}/compose")
async def compose_save(
    list_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    subject: Annotated[str, Form()],
    body_md: Annotated[str, Form()],
    reply_to: Annotated[str, Form()] = "",
    attachment_doc_ids: Annotated[list[str], Form()] = None,
    draft_id: Annotated[str, Form()] = "",
    action: Annotated[str, Form()] = "draft",   # draft | test | send
):
    user, member = ctx
    if not _can_send(user, member):
        raise HTTPException(403)
    ml = await db.get(MailingList, list_id)
    if not ml:
        raise HTTPException(404)

    # Pièces jointes JSON
    attachments = []
    if attachment_doc_ids:
        for s in attachment_doc_ids:
            if s.isdigit():
                doc = await db.get(Document, int(s))
                if doc:
                    attachments.append({
                        "doc_id": doc.id,
                        "filename": doc.original_filename or doc.name,
                    })

    # Charger ou créer la campagne
    campaign = None
    if draft_id and draft_id.isdigit():
        campaign = await db.get(MailingCampaign, int(draft_id))
        if campaign and campaign.list_id != list_id:
            campaign = None
    if not campaign:
        campaign = MailingCampaign(
            list_id=list_id,
            subject=subject.strip(),
            body_md=body_md,
            sender_id=member.id,
            status=CampaignStatus.DRAFT,
        )
        db.add(campaign)
    else:
        campaign.subject = subject.strip()
        campaign.body_md = body_md
        campaign.sender_id = member.id

    campaign.reply_to = (reply_to or "").strip() or None
    campaign.attachments = attachments or None

    await db.commit()
    await db.refresh(campaign)

    # Action
    if action == "test":
        # Envoi de test à soi-même (utilise l'email du membre courant)
        from app.services.email import _send_raw
        from app.services.mailing import (
            render_subject, render_body_md, md_to_html, make_html_email,
        )
        from app.models.lodge import LodgeSettings
        from app.services.mailing import _resolve_attachments
        lr = await db.execute(select(LodgeSettings).limit(1))
        lodge = lr.scalar_one_or_none()
        lodge_name = lodge.name if lodge and lodge.name else "Portail Socrate"
        if member.email:
            subj = "[TEST] " + render_subject(campaign.subject, member)
            body_md_r = render_body_md(campaign.body_md, member)
            html_inner = md_to_html(body_md_r)
            html = make_html_email(html_inner, "#test", ml.name, lodge_name)
            try:
                atts = await _resolve_attachments(db, campaign.attachments)
                await _send_raw(
                    to=member.email, subject=subj, html=html, text=body_md_r,
                    attachments=atts or None,
                )
            except Exception:
                pass
        return RedirectResponse(
            url=f"/mailing/lists/{list_id}/compose?draft_id={campaign.id}&_msg=test",
            status_code=303,
        )

    if action == "send":
        # Lancer le worker async — la requête revient tout de suite
        base = f"{request_scheme()}://{request_host()}"
        asyncio.ensure_future(send_campaign_async(campaign.id))
        return RedirectResponse(
            url=f"/mailing/campaigns/{campaign.id}?_msg=sending",
            status_code=303,
        )

    # action == "draft" par défaut
    return RedirectResponse(
        url=f"/mailing/lists/{list_id}/compose?draft_id={campaign.id}&_msg=saved",
        status_code=303,
    )


# Helpers pour récupérer scheme/host — fallback simples
def request_scheme() -> str:
    return "http"


def request_host() -> str:
    return "127.0.0.1:8000"


# ─────────────────────────────────────────────────────────────────────────────
#  Détail campagne
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
async def campaign_detail(
    campaign_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not _can_send(user, member):
        raise HTTPException(403)

    campaign = await db.get(MailingCampaign, campaign_id)
    if not campaign:
        raise HTTPException(404)
    mlist = await db.get(MailingList, campaign.list_id)

    # Livraisons (avec nom du membre)
    dr = await db.execute(
        select(MailingDelivery).where(MailingDelivery.campaign_id == campaign_id)
        .order_by(MailingDelivery.id)
    )
    deliveries = dr.scalars().all()

    member_ids = {d.member_id for d in deliveries if d.member_id}
    members_cache: dict[int, Member] = {}
    if member_ids:
        mr = await db.execute(select(Member).where(Member.id.in_(member_ids)))
        for m in mr.scalars().all():
            members_cache[m.id] = m

    return templates.TemplateResponse(request, "pages/mailing/campaign_detail.html", {
        "current_user": user, "current_member": member,
        "campaign": campaign, "mlist": mlist,
        "deliveries": deliveries, "members_cache": members_cache,
        "CampaignStatus": CampaignStatus,
        "DeliveryStatus": DeliveryStatus,
    })


# ─────────────────────────────────────────────────────────────────────────────
#  Désinscription publique (lien signé HMAC, pas besoin d'auth)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/unsubscribe/{token}", response_class=HTMLResponse)
async def unsubscribe_form(
    token: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    parsed = verify_unsubscribe_token(token)
    if not parsed:
        return templates.TemplateResponse(request, "pages/mailing/unsubscribe.html", {
            "error": "Lien de désinscription invalide ou expiré.",
            "token": token,
        }, status_code=400)
    list_id, member_id = parsed
    mlist = await db.get(MailingList, list_id)
    target = await db.get(Member, member_id)
    if not mlist or not target:
        return templates.TemplateResponse(request, "pages/mailing/unsubscribe.html", {
            "error": "Liste ou membre introuvable.", "token": token,
        }, status_code=404)
    # Déjà désinscrit ?
    r = await db.execute(
        select(MailingListMember).where(
            MailingListMember.list_id == list_id,
            MailingListMember.member_id == member_id,
        )
    )
    existing = r.scalar_one_or_none()
    already = existing and existing.unsubscribed_at is not None

    return templates.TemplateResponse(request, "pages/mailing/unsubscribe.html", {
        "mlist": mlist, "target": target, "token": token,
        "already_unsubscribed": already,
    })


@router.post("/unsubscribe/{token}")
async def unsubscribe_confirm(
    token: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    parsed = verify_unsubscribe_token(token)
    if not parsed:
        raise HTTPException(400, "Lien invalide")
    list_id, member_id = parsed

    r = await db.execute(
        select(MailingListMember).where(
            MailingListMember.list_id == list_id,
            MailingListMember.member_id == member_id,
        )
    )
    row = r.scalar_one_or_none()
    if row:
        row.unsubscribed_at = datetime.utcnow()
    else:
        db.add(MailingListMember(
            list_id=list_id, member_id=member_id,
            unsubscribed_at=datetime.utcnow(),
        ))
    await db.commit()
    return RedirectResponse(url=f"/mailing/unsubscribe/{token}?_done=1", status_code=303)
