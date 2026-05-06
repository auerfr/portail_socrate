"""Router Paramètres — configuration loge, VM, Secrétaire, temple"""
from typing import Annotated
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import require_admin
from app.models.lodge import LodgeSettings, LodgeOffice
from app.models.identity import Member, MemberStatus, LodgeFunction

router = APIRouter(prefix="/settings", tags=["settings"])
templates = Jinja2Templates(directory="app/templates")


async def _get_lodge(db: AsyncSession) -> LodgeSettings | None:
    r = await db.execute(select(LodgeSettings).limit(1))
    return r.scalar_one_or_none()


async def _get_offices(db: AsyncSession) -> list[LodgeOffice]:
    r = await db.execute(select(LodgeOffice).order_by(LodgeOffice.sort_order, LodgeOffice.id))
    return r.scalars().all()


async def _get_active_members(db: AsyncSession) -> list[Member]:
    r = await db.execute(
        select(Member)
        .where(Member.status == MemberStatus.ACTIVE)
        .order_by(Member.last_name)
    )
    return r.scalars().all()


# Offices rituels affichables (hors FRERE)
OFFICES = [
    ("VM",               "V∴M∴ — Vénérable Maître"),
    ("PREMIER_S",        "1er S∴ — Premier Surveillant"),
    ("SECOND_S",         "2e S∴ — Second Surveillant"),
    ("ORATEUR",          "Or∴ — Orateur"),
    ("SECRETAIRE",       "Sec∴ — Secrétaire"),
    ("TRESORIER",        "Tréso∴ — Trésorier"),
    ("EXPERT",           "Expert"),
    ("MAITRE_CEREMONIES","M∴C∴ — Maître des Cérémonies"),
    ("HARMONISTE",       "M∴H∴ — Maître Harmoniste"),
    ("HOSPITALIER",      "Hosp∴ — Hospitalier"),
    ("TUILEUR",          "Tuileur"),
    ("ARCHITECTE",       "Arch∴ — Architecte"),
    ("MAITRE_BANQUETS",  "M∴B∴ — Maître des Banquets"),
]


@router.get("/", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    lodge = await _get_lodge(db)
    members = await _get_active_members(db)
    offices = await _get_offices(db)

    return templates.TemplateResponse(request, "pages/settings/index.html", {
        "current_member": member,
        "current_user": user,
        "lodge": lodge,
        "members": members,
        "offices": offices,
        "is_admin": user.is_admin,
        "saved": request.query_params.get("saved"),
    })


@router.post("/lodge", response_class=HTMLResponse)
async def settings_save_lodge(
    request: Request,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    form = await request.form()

    lodge = await _get_lodge(db)
    if not lodge:
        lodge = LodgeSettings()
        db.add(lodge)

    # Identité
    lodge.name         = form.get("name", lodge.name or "").strip()
    lodge.orient_city  = form.get("orient_city", lodge.orient_city or "").strip()
    lodge.obedience    = form.get("obedience", lodge.obedience or "").strip()
    lodge.rite         = form.get("rite", "").strip() or None
    lodge.loge_number  = form.get("loge_number", "").strip() or None

    # Temple
    lodge.temple_name    = form.get("temple_name", "").strip() or None
    lodge.temple_note    = form.get("temple_note", "").strip() or None
    lodge.temple_address = form.get("temple_address", "").strip() or None

    # VM — depuis membre ou champs libres
    vm_member_id = form.get("vm_member_id", "").strip()
    if vm_member_id.isdigit():
        lodge.vm_member_id = int(vm_member_id)
        vm = await db.get(Member, int(vm_member_id))
        if vm:
            # Auto-remplir depuis le membre si les champs libres sont vides
            if not form.get("vm_name_display", "").strip():
                lodge.vm_name_display = f"{vm.first_name} {vm.last_name[0]}∴"
            if not form.get("vm_email_display", "").strip():
                lodge.vm_email_display = vm.email
    else:
        lodge.vm_member_id = None

    lodge.vm_name_display  = form.get("vm_name_display", "").strip() or lodge.vm_name_display
    lodge.vm_email_display = form.get("vm_email_display", "").strip() or lodge.vm_email_display
    lodge.vm_phone         = form.get("vm_phone", "").strip() or None

    # Secrétaire
    sec_member_id = form.get("secretary_member_id", "").strip()
    if sec_member_id.isdigit():
        lodge.secretary_member_id = int(sec_member_id)
        sec = await db.get(Member, int(sec_member_id))
        if sec:
            if not form.get("secretary_name_display", "").strip():
                lodge.secretary_name_display = f"{sec.first_name} {sec.last_name[0]}∴"
            if not form.get("secretary_email_display", "").strip():
                lodge.secretary_email_display = sec.email
    else:
        lodge.secretary_member_id = None

    lodge.secretary_name_display  = form.get("secretary_name_display", "").strip() or lodge.secretary_name_display
    lodge.secretary_email_display = form.get("secretary_email_display", "").strip() or lodge.secretary_email_display

    # OJ & horaires
    lodge.common_agenda     = form.get("common_agenda", "").strip() or None
    lodge.standard_schedule = form.get("standard_schedule", "").strip() or None
    lodge.chantiers_info    = form.get("chantiers_info", "").strip() or None

    await db.commit()
    return RedirectResponse(url="/settings/?saved=1", status_code=303)


@router.post("/officers", response_class=HTMLResponse)
async def settings_save_officers(
    request: Request,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Sauvegarde labels + affectations des offices, et crée les nouveaux."""
    user, member = ctx
    form = await request.form()

    # ── Offices existants ───────────────────────────────────────────────────
    # Collecter tous les ids soumis
    existing_ids = [
        int(v) for k, v in form.multi_items()
        if k == "office_id"
    ]

    for oid in existing_ids:
        office = await db.get(LodgeOffice, oid)
        if not office:
            continue
        # Suppression demandée ?
        if form.get(f"delete_{oid}"):
            await db.delete(office)
            continue
        office.label = form.get(f"label_{oid}", office.label).strip() or office.label
        raw_mid = form.get(f"member_{oid}", "").strip()
        office.member_id = int(raw_mid) if raw_mid.isdigit() else None

    # ── Nouveaux offices (ajoutés dynamiquement) ────────────────────────────
    max_order_r = await db.execute(
        select(LodgeOffice.sort_order).order_by(LodgeOffice.sort_order.desc()).limit(1)
    )
    max_order = (max_order_r.scalar() or 0)

    new_labels = form.getlist("new_label")
    new_members = form.getlist("new_member")
    for i, label in enumerate(new_labels):
        label = label.strip()
        if not label:
            continue
        raw_mid = new_members[i].strip() if i < len(new_members) else ""
        db.add(LodgeOffice(
            label=label,
            sort_order=max_order + 10 + i,
            member_id=int(raw_mid) if raw_mid.isdigit() else None,
        ))

    await db.commit()
    return RedirectResponse(url="/settings/?saved=1", status_code=303)
