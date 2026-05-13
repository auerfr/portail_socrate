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
    MailingList, MailingListMember, MailingListExternal, MailingListType,
    MailingCampaign, MailingDelivery, CampaignStatus, DeliveryStatus,
)
from app.models.lodge import ExternalContact
from app.models.documents import Document, DocStatus
from app.services.mailing import (
    resolve_recipients, send_campaign_async, launch_send_task,
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

    # Compteurs par liste (membres + externes confondus = "destinataires")
    counts: dict[int, int] = {}
    for ml in lists:
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

    # Membres uniquement (pour l'affichage séparé de cette page)
    if ml.list_type == MailingListType.STATIC:
        rm = await db.execute(
            select(Member)
            .join(MailingListMember, MailingListMember.member_id == Member.id)
            .where(
                MailingListMember.list_id == ml.id,
                MailingListMember.unsubscribed_at.is_(None),
            )
            .order_by(Member.last_name, Member.first_name)
        )
        member_recipients = list(rm.scalars().all())
    else:
        # Dynamic : on appelle resolve_recipients puis on filtre
        all_r = await resolve_recipients(db, ml)
        member_ids = [r.contact_id for r in all_r if r.kind == "m"]
        member_recipients = []
        if member_ids:
            mr = await db.execute(
                select(Member).where(Member.id.in_(member_ids))
                .order_by(Member.last_name, Member.first_name)
            )
            member_recipients = list(mr.scalars().all())
    # Pour les compteurs templates qui attendent 'recipients'
    recipients = member_recipients

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
        member_recipient_ids = {r.contact_id for r in recipients if r.kind == "m"}
        all_active = [m for m in all_active if m.id not in member_recipient_ids]

    # Contacts externes déjà inscrits + ceux disponibles à ajouter
    sub_e = await db.execute(
        select(ExternalContact)
        .join(MailingListExternal, MailingListExternal.external_id == ExternalContact.id)
        .where(
            MailingListExternal.list_id == ml.id,
            MailingListExternal.unsubscribed_at.is_(None),
        )
        .order_by(ExternalContact.name)
    )
    subscribed_externals = list(sub_e.scalars().all())
    sub_ext_ids = {e.id for e in subscribed_externals}
    all_ext = (await db.execute(
        select(ExternalContact).where(ExternalContact.is_active == True)  # noqa: E712
        .order_by(ExternalContact.name)
    )).scalars().all()
    available_externals = [e for e in all_ext if e.id not in sub_ext_ids]

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
        "subscribed_externals": subscribed_externals,
        "available_externals": available_externals,
        "campaigns": campaigns,
        "CampaignStatus": CampaignStatus,
        "MailingListType": MailingListType,
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


@router.post("/lists/{list_id}/edit")
async def list_edit(
    list_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    name: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    list_type: Annotated[str, Form()] = "STATIC",
    criteria_grade: Annotated[list[str], Form()] = None,
    criteria_lodge_function: Annotated[list[str], Form()] = None,
    criteria_status: Annotated[list[str], Form()] = None,
    criteria_group_ids: Annotated[list[str], Form()] = None,
):
    user, member = ctx
    if not _can_send(user, member):
        raise HTTPException(403)
    ml = await db.get(MailingList, list_id)
    if not ml:
        raise HTTPException(404)
    # Listes système : on autorise renommer + description, mais pas changement
    # de type ni de critères (intégrité)
    ml.name = name.strip()
    ml.description = description.strip() or None
    if not ml.is_system:
        if list_type in MailingListType.__members__:
            ml.list_type = MailingListType(list_type)
        if ml.list_type == MailingListType.DYNAMIC:
            crit = {}
            if criteria_status:    crit["status"] = criteria_status
            if criteria_grade:     crit["grade"] = criteria_grade
            if criteria_lodge_function: crit["lodge_function"] = criteria_lodge_function
            if criteria_group_ids:
                crit["group_ids"] = [int(g) for g in criteria_group_ids if g.isdigit()]
            ml.criteria = crit or None
        else:
            ml.criteria = None
    await db.commit()
    return RedirectResponse(url=f"/mailing/lists/{list_id}?_msg=edited", status_code=303)


@router.post("/lists/{list_id}/delete")
async def list_delete(
    list_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    confirm: Annotated[str, Form()] = "",
):
    user, member = ctx
    if not user.is_admin:
        raise HTTPException(403, "Admin uniquement")
    ml = await db.get(MailingList, list_id)
    if not ml:
        raise HTTPException(404)
    if ml.is_system and confirm != "YES":
        raise HTTPException(400, "Suppression d'une liste système : confirmation explicite requise (confirm=YES)")
    await db.delete(ml)
    await db.commit()
    return RedirectResponse(url="/mailing/", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
#  Contacts externes — ajout / suppression / création rapide / import CSV
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/lists/{list_id}/externals/add")
async def list_external_add(
    list_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    external_id: Annotated[int, Form()],
):
    user, member = ctx
    if not _can_send(user, member):
        raise HTTPException(403)
    ml = await db.get(MailingList, list_id)
    if not ml:
        raise HTTPException(404)
    r = await db.execute(
        select(MailingListExternal).where(
            MailingListExternal.list_id == list_id,
            MailingListExternal.external_id == external_id,
        )
    )
    existing = r.scalar_one_or_none()
    if existing:
        existing.unsubscribed_at = None
    else:
        db.add(MailingListExternal(list_id=list_id, external_id=external_id))
    await db.commit()
    return RedirectResponse(url=f"/mailing/lists/{list_id}", status_code=303)


@router.post("/lists/{list_id}/externals/{external_id}/remove")
async def list_external_remove(
    list_id: int, external_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not _can_send(user, member):
        raise HTTPException(403)
    r = await db.execute(
        select(MailingListExternal).where(
            MailingListExternal.list_id == list_id,
            MailingListExternal.external_id == external_id,
        )
    )
    row = r.scalar_one_or_none()
    if row:
        await db.delete(row)
        await db.commit()
    return RedirectResponse(url=f"/mailing/lists/{list_id}", status_code=303)


@router.post("/lists/{list_id}/externals/quick-add")
async def list_external_quick_add(
    list_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    first_name: Annotated[str, Form()],
    last_name: Annotated[str, Form()],
    email: Annotated[str, Form()],
    lodge_name: Annotated[str, Form()] = "",
    orient: Annotated[str, Form()] = "",
):
    """Crée un nouvel ExternalContact + l'ajoute à la liste, en un clic.

    Champs : Prénom + Nom + Email + Loge + Orient.
    Le champ legacy `name` est rempli automatiquement (Prénom + Nom).
    """
    user, member = ctx
    if not _can_send(user, member):
        raise HTTPException(403)
    ml = await db.get(MailingList, list_id)
    if not ml:
        raise HTTPException(404)

    fname = first_name.strip()
    lname = last_name.strip()
    email = email.strip().lower()
    full_name = f"{fname} {lname}".strip()
    if not full_name or "@" not in email:
        raise HTTPException(400, "Au moins nom (ou prénom) + email valides requis")

    # Vérifie si l'email existe déjà
    er = await db.execute(select(ExternalContact).where(ExternalContact.email == email))
    ext = er.scalar_one_or_none()
    if not ext:
        ext = ExternalContact(
            name=full_name,
            first_name=fname or None,
            last_name=lname or None,
            email=email,
            lodge_name=lodge_name.strip() or None,
            orient=orient.strip() or None,
            contact_type="EXTERNAL",
            is_active=True,
        )
        db.add(ext)
        await db.flush()
    else:
        # Met à jour les nouveaux champs si vides
        if fname and not ext.first_name: ext.first_name = fname
        if lname and not ext.last_name:  ext.last_name = lname
        if lodge_name.strip() and not ext.lodge_name: ext.lodge_name = lodge_name.strip()
        if orient.strip() and not ext.orient:        ext.orient = orient.strip()

    # Lien à la liste
    r = await db.execute(
        select(MailingListExternal).where(
            MailingListExternal.list_id == list_id,
            MailingListExternal.external_id == ext.id,
        )
    )
    existing = r.scalar_one_or_none()
    if existing:
        existing.unsubscribed_at = None
    else:
        db.add(MailingListExternal(list_id=list_id, external_id=ext.id))
    await db.commit()
    return RedirectResponse(url=f"/mailing/lists/{list_id}?_msg=ext_added", status_code=303)


@router.post("/lists/{list_id}/externals/import-csv")
async def list_external_import_csv(
    list_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Import en masse de contacts externes via CSV.

    Format attendu (5 colonnes) :
        Prénom;Nom;Email;Loge;Orient

    Séparateur ';' ou ',' auto-détecté. Première ligne d'entête détectée.
    Encodage UTF-8 ou Latin-1.
    """
    import csv as csvmod
    import io
    user, member = ctx
    if not _can_send(user, member):
        raise HTTPException(403)
    ml = await db.get(MailingList, list_id)
    if not ml:
        raise HTTPException(404)

    form = await request.form()
    upload = form.get("csvfile")
    if not upload or not hasattr(upload, "read"):
        raise HTTPException(400, "Fichier CSV requis")

    raw = await upload.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    # Détecter le séparateur
    sample = text[:1024]
    delim = ";" if sample.count(";") > sample.count(",") else ","

    reader = csvmod.reader(io.StringIO(text), delimiter=delim)
    added = 0
    skipped = 0
    for i, row in enumerate(reader):
        if not row or all((not c.strip()) for c in row):
            continue
        # Skip ligne d'entête (heuristique)
        if i == 0:
            joined = " ".join(row[:3]).lower()
            if any(h in joined for h in ("prénom", "prenom", "first", "email", "nom", "name")):
                continue
        # Format flexible :
        #   - 5 cols : Prénom; Nom; Email; Loge; Orient
        #   - 4 cols : Prénom; Nom; Email; Loge (orient vide)
        #   - 3 cols : Prénom; Nom; Email (loge/orient vides)
        #   - 2 cols (legacy) : Nom complet; Email
        fname, lname, email, lodge_n, orient_n = "", "", "", "", ""
        if len(row) >= 5:
            fname  = row[0].strip()
            lname  = row[1].strip()
            email  = row[2].strip().lower()
            lodge_n = row[3].strip()
            orient_n = row[4].strip()
        elif len(row) == 4:
            fname  = row[0].strip()
            lname  = row[1].strip()
            email  = row[2].strip().lower()
            lodge_n = row[3].strip()
        elif len(row) == 3:
            fname  = row[0].strip()
            lname  = row[1].strip()
            email  = row[2].strip().lower()
        elif len(row) == 2:
            # legacy : "Nom complet; Email"
            name_blob = row[0].strip()
            email = row[1].strip().lower()
            if " " in name_blob:
                fname, lname = name_blob.split(" ", 1)
            else:
                lname = name_blob

        full_name = f"{fname} {lname}".strip()
        if not full_name or "@" not in email:
            skipped += 1
            continue
        # Existing ?
        er = await db.execute(select(ExternalContact).where(ExternalContact.email == email))
        ext = er.scalar_one_or_none()
        if not ext:
            ext = ExternalContact(
                name=full_name,
                first_name=fname or None,
                last_name=lname or None,
                email=email,
                lodge_name=lodge_n or None,
                orient=orient_n or None,
                contact_type="EXTERNAL", is_active=True,
            )
            db.add(ext)
            await db.flush()
        else:
            # Compléter les champs manquants sans écraser
            if fname and not ext.first_name: ext.first_name = fname
            if lname and not ext.last_name:  ext.last_name = lname
            if lodge_n and not ext.lodge_name: ext.lodge_name = lodge_n
            if orient_n and not ext.orient:    ext.orient = orient_n
            if not ext.name or ext.name.strip() == "": ext.name = full_name
        # Lien liste
        lr = await db.execute(
            select(MailingListExternal).where(
                MailingListExternal.list_id == list_id,
                MailingListExternal.external_id == ext.id,
            )
        )
        existing = lr.scalar_one_or_none()
        if existing:
            existing.unsubscribed_at = None
        else:
            db.add(MailingListExternal(list_id=list_id, external_id=ext.id))
        added += 1
    await db.commit()
    return RedirectResponse(
        url=f"/mailing/lists/{list_id}?_msg=imported&n={added}&skipped={skipped}",
        status_code=303,
    )


@router.post("/campaigns/{campaign_id}/retry")
async def campaign_retry(
    campaign_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Relance l'envoi d'une campagne bloquée ou en échec.

    - Skippe les destinataires déjà SENT (idempotence)
    - Recrée des deliveries PENDING pour ceux qui n'ont pas reçu
    """
    user, member = ctx
    if not _can_send(user, member):
        raise HTTPException(403)
    campaign = await db.get(MailingCampaign, campaign_id)
    if not campaign:
        raise HTTPException(404)
    if campaign.status == CampaignStatus.DRAFT:
        raise HTTPException(400, "Cette campagne n'a jamais été envoyée — utilisez Envoyer")
    # Remet en SENDING + worker
    campaign.status = CampaignStatus.SENDING
    await db.commit()
    launch_send_task(campaign.id)
    return RedirectResponse(url=f"/mailing/campaigns/{campaign_id}?_msg=retrying", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
#  Tracking pixel + clics (pas d'auth — accessible depuis les emails)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/track/open/{token}")
async def track_open(token: str, db: Annotated[AsyncSession, Depends(get_db)]):
    """Pixel 1×1 de tracking d'ouverture."""
    from app.services.mailing import verify_tracking_token
    from fastapi.responses import Response as _Resp
    parsed = verify_tracking_token(token)
    if parsed:
        delivery_id, _ = parsed
        d = await db.get(MailingDelivery, delivery_id)
        if d and not d.opened_at:
            d.opened_at = datetime.utcnow()
            # Incrémenter le compteur sur la campagne
            c = await db.get(MailingCampaign, d.campaign_id)
            if c:
                c.opened_count = (c.opened_count or 0) + 1
            await db.commit()
    # GIF 1×1 transparent
    gif = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!"
           b"\xf9\x04\x00\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;")
    return _Resp(content=gif, media_type="image/gif",
                 headers={"Cache-Control": "no-cache, no-store"})


@router.get("/track/click/{token}")
async def track_click(
    token: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    url: str = "",
):
    """Redirect + enregistrement de clic."""
    from app.services.mailing import verify_tracking_token
    parsed = verify_tracking_token(token)
    if parsed:
        delivery_id, _ = parsed
        d = await db.get(MailingDelivery, delivery_id)
        if d:
            d.clicked_at = d.clicked_at or datetime.utcnow()
            d.click_count = (d.click_count or 0) + 1
            c = await db.get(MailingCampaign, d.campaign_id)
            if c:
                c.clicked_count = (c.clicked_count or 0) + 1
            await db.commit()
    target = url or "/"
    # Sécurité basique anti open-redirect
    if not target.startswith("http"):
        target = "/"
    return RedirectResponse(url=target, status_code=302)


# ─────────────────────────────────────────────────────────────────────────────
#  Planification d'envoi
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/campaigns/{campaign_id}/schedule")
async def campaign_schedule(
    campaign_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    scheduled_at: Annotated[str, Form()],
):
    """Planifie l'envoi à une date/heure précise."""
    user, member = ctx
    if not _can_send(user, member):
        raise HTTPException(403)
    campaign = await db.get(MailingCampaign, campaign_id)
    if not campaign:
        raise HTTPException(404)
    try:
        dt = datetime.fromisoformat(scheduled_at)
    except ValueError:
        raise HTTPException(400, "Format datetime invalide")
    campaign.scheduled_at = dt
    campaign.status = CampaignStatus.DRAFT
    await db.commit()
    return RedirectResponse(url=f"/mailing/campaigns/{campaign_id}?_msg=scheduled", status_code=303)


@router.get("/contacts-template.csv")
async def contacts_template_csv(
    ctx: Annotated[tuple, Depends(require_auth)],
):
    """Modèle CSV vide à télécharger comme point de départ pour l'import."""
    from fastapi.responses import Response
    user, member = ctx
    if not _can_send(user, member):
        raise HTTPException(403)
    csv_content = (
        "Prénom;Nom;Email;Loge;Orient\n"
        "Jean;Dupont;jean.dupont@example.fr;Les Compagnons du Devoir;Strasbourg\n"
        "Marie;Curie;marie.curie@example.fr;Athena;Paris\n"
    )
    # BOM UTF-8 pour Excel
    body = "﻿" + csv_content
    return Response(
        content=body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="modele-contacts-externes.csv"'},
    )


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

    # Documents publiables depuis la GED pour PJ — avec filtre whitelist
    from app.services.confidentiality import get_config as get_conf
    conf = await get_conf(db=db)
    docs_q = select(Document).where(
        Document.status == DocStatus.PUBLISHED,
        Document.storage_path.isnot(None),
    )
    if conf.get("pj_whitelist_enabled"):
        allowed = conf.get("pj_allowed_folder_ids") or []
        if allowed:
            docs_q = docs_q.where(Document.folder_id.in_(allowed))
        else:
            # whitelist activée mais aucun dossier coché => aucun PJ autorisé
            docs_q = docs_q.where(Document.id == -1)
    docs = (await db.execute(docs_q.order_by(desc(Document.updated_at)).limit(100))).scalars().all()

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

    # Pièces jointes JSON (avec validation whitelist)
    from app.services.confidentiality import get_config as get_conf
    conf = await get_conf(db=db)
    whitelist_on = conf.get("pj_whitelist_enabled")
    allowed_folder_ids = set(conf.get("pj_allowed_folder_ids") or [])

    attachments = []
    if attachment_doc_ids:
        for s in attachment_doc_ids:
            if s.isdigit():
                doc = await db.get(Document, int(s))
                if not doc:
                    continue
                # Validation whitelist côté serveur (anti-trafiquage formulaire)
                if whitelist_on and doc.folder_id not in allowed_folder_ids:
                    raise HTTPException(
                        403,
                        f"Document « {doc.name} » non autorisé en pièce jointe "
                        "(dossier hors whitelist). Voir /admin/confidentiality."
                    )
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
        # Lancer le worker async avec rétention de la task (anti-GC)
        launch_send_task(campaign.id)
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
    list_id, kind, contact_id = parsed
    mlist = await db.get(MailingList, list_id)
    if not mlist:
        return templates.TemplateResponse(request, "pages/mailing/unsubscribe.html", {
            "error": "Liste introuvable.", "token": token,
        }, status_code=404)

    # Cible (member ou external) + statut désinscription
    target_name = ""
    target_civility = ""
    already = False
    if kind == "m":
        m = await db.get(Member, contact_id)
        if not m:
            return templates.TemplateResponse(request, "pages/mailing/unsubscribe.html", {
                "error": "Membre introuvable.", "token": token,
            }, status_code=404)
        target_name = f"{m.first_name} {m.last_name}"
        target_civility = m.civility or ""
        r = await db.execute(
            select(MailingListMember).where(
                MailingListMember.list_id == list_id,
                MailingListMember.member_id == contact_id,
            )
        )
        existing = r.scalar_one_or_none()
        already = existing and existing.unsubscribed_at is not None
    else:
        e = await db.get(ExternalContact, contact_id)
        if not e:
            return templates.TemplateResponse(request, "pages/mailing/unsubscribe.html", {
                "error": "Contact introuvable.", "token": token,
            }, status_code=404)
        target_name = e.name
        r = await db.execute(
            select(MailingListExternal).where(
                MailingListExternal.list_id == list_id,
                MailingListExternal.external_id == contact_id,
            )
        )
        existing = r.scalar_one_or_none()
        already = existing and existing.unsubscribed_at is not None

    return templates.TemplateResponse(request, "pages/mailing/unsubscribe.html", {
        "mlist": mlist, "target_name": target_name,
        "target_civility": target_civility,
        "token": token,
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
    list_id, kind, contact_id = parsed

    if kind == "m":
        r = await db.execute(
            select(MailingListMember).where(
                MailingListMember.list_id == list_id,
                MailingListMember.member_id == contact_id,
            )
        )
        row = r.scalar_one_or_none()
        if row:
            row.unsubscribed_at = datetime.utcnow()
        else:
            db.add(MailingListMember(
                list_id=list_id, member_id=contact_id,
                unsubscribed_at=datetime.utcnow(),
            ))
    else:
        r = await db.execute(
            select(MailingListExternal).where(
                MailingListExternal.list_id == list_id,
                MailingListExternal.external_id == contact_id,
            )
        )
        row = r.scalar_one_or_none()
        if row:
            row.unsubscribed_at = datetime.utcnow()
        else:
            db.add(MailingListExternal(
                list_id=list_id, external_id=contact_id,
                unsubscribed_at=datetime.utcnow(),
            ))
    await db.commit()
    return RedirectResponse(url=f"/mailing/unsubscribe/{token}?_done=1", status_code=303)
