"""Router Paramètres — configuration loge, VM, Secrétaire, temple"""
import uuid
from pathlib import Path
from typing import Annotated, Optional
from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

LOGO_DIR = Path("app/static/uploads/logo")
LOGO_DIR.mkdir(parents=True, exist_ok=True)
_LOGO_ALLOWED = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import get_db
from app.dependencies import require_admin, require_auth
from app.models.lodge import LodgeSettings, LodgeOffice, ExternalContact
from app.models.identity import Member, MemberStatus, LodgeFunction, User

ENV_FILE = Path(__file__).parent.parent.parent / ".env"

router = APIRouter(prefix="/settings", tags=["settings"])

# Mapping label (sous-chaîne insensible à la casse) → LodgeFunction
_LABEL_FUNCTION_MAP = [
    ("vénérable", LodgeFunction.VM),
    ("v.m.", LodgeFunction.VM),
    ("premier surv", LodgeFunction.PREMIER_S),
    ("1er s.", LodgeFunction.PREMIER_S),
    ("second surv", LodgeFunction.SECOND_S),
    ("2e s.", LodgeFunction.SECOND_S),
    ("orateur", LodgeFunction.ORATEUR),
    ("secrétaire", LodgeFunction.SECRETAIRE),
    ("trésorier", LodgeFunction.TRESORIER),
    ("expert", LodgeFunction.EXPERT),
    ("cérémonies", LodgeFunction.MAITRE_CEREMONIES),
    ("harmoniste", LodgeFunction.HARMONISTE),
    ("hospitalier", LodgeFunction.HOSPITALIER),
    ("tuileur", LodgeFunction.TUILEUR),
    ("couvreur", LodgeFunction.TUILEUR),
    ("architecte", LodgeFunction.ARCHITECTE),
    ("banquets", LodgeFunction.MAITRE_BANQUETS),
]


def _detect_function(label: str) -> LodgeFunction | None:
    """Détecte la LodgeFunction à partir du libellé d'un office."""
    normalized = label.lower().replace("∴", ".").replace(":", "")
    for keyword, fn in _LABEL_FUNCTION_MAP:
        if keyword in normalized:
            return fn
    return None
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
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    from app.dependencies import can_manage_members
    if not user.is_admin and not can_manage_members(member):
        raise HTTPException(403)
    lodge = await _get_lodge(db)
    members = await _get_active_members(db)
    offices = await _get_offices(db)

    cfg = get_settings()

    # Tous les utilisateurs avec leur fiche membre pour gestion super-admins
    r_users = await db.execute(
        select(User).order_by(User.login)
    )
    all_users = r_users.scalars().all()
    member_by_id = {m.id: m for m in members}
    # Charger aussi les membres non actifs pour avoir les noms des admins
    r_all_members = await db.execute(select(Member).order_by(Member.last_name))
    all_member_map = {m.id: m for m in r_all_members.scalars().all()}

    r_contacts = await db.execute(select(ExternalContact).order_by(ExternalContact.contact_type, ExternalContact.name))
    external_contacts = r_contacts.scalars().all()

    from app.dependencies import can_manage_members
    can_manage_contacts = user.is_admin or can_manage_members(member)

    return templates.TemplateResponse(request, "pages/settings/index.html", {
        "current_member": member,
        "current_user": user,
        "lodge": lodge,
        "members": members,
        "offices": offices,
        "is_admin": user.is_admin,
        "can_manage_contacts": can_manage_contacts,
        "all_users": all_users,
        "all_member_map": all_member_map,
        "external_contacts": external_contacts,
        "saved": request.query_params.get("saved"),
        "smtp_saved": request.query_params.get("smtp_saved"),
        "smtp_ok":    request.query_params.get("smtp_ok"),
        "smtp_fail":  request.query_params.get("smtp_fail"),
        "smtp_err":   request.query_params.get("smtp_err"),
        "smtp_host": cfg.smtp_host,
        "smtp_port": cfg.smtp_port,
        "smtp_user": cfg.smtp_user,
        "smtp_from": cfg.smtp_from,
        "smtp_secure": cfg.smtp_secure,
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

    # Seuils assiduité
    tw = form.get("attendance_threshold_warn", "").strip()
    td = form.get("attendance_threshold_danger", "").strip()
    if tw.isdigit():
        lodge.attendance_threshold_warn = max(1, min(100, int(tw)))
    if td.isdigit():
        lodge.attendance_threshold_danger = max(1, min(100, int(td)))

    # Visio
    lodge.visio_provider   = form.get("visio_provider", "").strip() or None
    lodge.visio_server_url = form.get("visio_server_url", "").strip() or None
    lodge.visio_room_prefix = form.get("visio_room_prefix", "").strip() or None

    # Logo upload
    logo_file = form.get("logo_file")
    if logo_file and getattr(logo_file, "filename", None):
        ext = Path(logo_file.filename).suffix.lower()
        if ext in _LOGO_ALLOWED:
            content = await logo_file.read()
            if content:
                filename = f"logo_{uuid.uuid4().hex}{ext}"
                (LOGO_DIR / filename).write_bytes(content)
                lodge.logo_url = f"/static/uploads/logo/{filename}"

    await db.commit()
    return RedirectResponse(url="/settings/?saved=lodge", status_code=303)


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

    await db.flush()

    # ── Synchroniser Member.lodge_function depuis les offices ───────────────
    # 1. Remettre à FRERE tous les membres qui avaient une fonction reconnue
    known_fns = {fn for _, fn in _LABEL_FUNCTION_MAP}
    r_reset = await db.execute(select(Member).where(Member.lodge_function.in_(known_fns)))
    for m in r_reset.scalars().all():
        m.lodge_function = LodgeFunction.FRERE
    # 2. Re-assigner selon les libellés des offices actuels
    r_offices_all = await db.execute(select(LodgeOffice))
    for office in r_offices_all.scalars().all():
        if not office.member_id:
            continue
        fn = _detect_function(office.label)
        if fn:
            mem = await db.get(Member, office.member_id)
            if mem:
                mem.lodge_function = fn

    await db.commit()
    return RedirectResponse(url="/settings/?saved=officers", status_code=303)


# ── Configuration SMTP (admin seulement) ──────────────────────────────────

def _update_env(key: str, value: str) -> None:
    """Met à jour (ou ajoute) une clé dans le fichier .env sans toucher aux autres."""
    if not ENV_FILE.exists():
        ENV_FILE.write_text(f"{key}={value}\n", encoding="utf-8")
        return
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    found = False
    new_lines = []
    for line in lines:
        if line.startswith(f"{key}=") or line.startswith(f"{key} ="):
            new_lines.append(f"{key}={value}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


@router.post("/smtp")
async def settings_save_smtp(
    request: Request,
    ctx: Annotated[object, Depends(require_admin)],
    smtp_host: str = Form(""),
    smtp_port: str = Form("465"),
    smtp_user: str = Form(""),
    smtp_pass: str = Form(""),
    smtp_secure: str = Form("ssl"),
    smtp_from: str = Form(""),
):
    """Écrit les paramètres SMTP dans le .env."""
    if smtp_host.strip():
        _update_env("SMTP_HOST", smtp_host.strip())
    if smtp_port.strip():
        _update_env("SMTP_PORT", smtp_port.strip())
    if smtp_user.strip():
        _update_env("SMTP_USER", smtp_user.strip())
        _update_env("SMTP_FROM", smtp_from.strip() or smtp_user.strip())
    if smtp_pass.strip():          # ne pas écraser si vide = "ne pas changer"
        _update_env("SMTP_PASS", smtp_pass.strip())
    if smtp_secure in ("ssl", "tls", "none"):
        _update_env("SMTP_SECURE", smtp_secure)

    # Invalider le cache settings pour la session courante
    get_settings.cache_clear()

    return RedirectResponse(url="/settings/?smtp_saved=1", status_code=303)


@router.post("/smtp/test")
async def settings_test_smtp(
    request: Request,
    ctx: Annotated[object, Depends(require_admin)],
    test_to: Optional[str] = Form(None),
):
    """Envoie un email de test à l'adresse spécifiée (ou celle du membre)."""
    user, member = ctx
    from app.services.email import _send_raw
    from urllib.parse import quote

    recipient = (test_to or "").strip() or member.email
    if not recipient:
        return RedirectResponse(url="/settings/?smtp_fail=1&smtp_err=Aucune+adresse+email+configurée", status_code=303)

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;padding:24px;">
      <h2 style="color:#2340b0;">✅ Configuration SMTP opérationnelle</h2>
      <p style="color:#374151;">
        Ce message de test a été envoyé depuis le <strong>Portail Socrate</strong>
        à l'adresse <strong>{recipient}</strong>.
      </p>
      <p style="color:#6b7280;font-size:13px;">
        Serveur : {get_settings().smtp_host}:{get_settings().smtp_port}
        ({get_settings().smtp_secure.upper()})<br>
        Compte : {get_settings().smtp_user}
      </p>
    </div>"""

    text = (
        f"Test SMTP — Portail Socrate\n\n"
        f"Configuration opérationnelle.\n"
        f"Serveur : {get_settings().smtp_host}:{get_settings().smtp_port}\n"
        f"Compte  : {get_settings().smtp_user}"
    )

    ok, err = await _send_raw(
        to=recipient,
        subject="[Portail Socrate] Test de configuration SMTP",
        html=html,
        text=text,
    )
    if ok:
        enc = quote(recipient)
        return RedirectResponse(url=f"/settings/?smtp_ok=1&smtp_to={enc}", status_code=303)
    else:
        err_enc = quote(str(err)[:200])
        return RedirectResponse(url=f"/settings/?smtp_fail=1&smtp_err={err_enc}", status_code=303)


# ── Correspondants externes ───────────────────────────────────────────────

@router.post("/external-contacts/add")
async def external_contact_add(
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    name: str = Form(...),
    email: str = Form(...),
    organization: str = Form(""),
    contact_type: str = Form("EXTERNAL"),
    notes: str = Form(""),
):
    user, member = ctx
    from app.dependencies import can_manage_members
    if not (user.is_admin or can_manage_members(member)):
        raise HTTPException(403)
    db.add(ExternalContact(
        name=name.strip(),
        email=email.strip().lower(),
        organization=organization.strip() or None,
        contact_type=contact_type if contact_type in ("EXTERNAL", "VISITOR") else "EXTERNAL",
        notes=notes.strip() or None,
        is_active=True,
    ))
    await db.commit()
    return RedirectResponse(url="/settings/?saved=contacts", status_code=303)


@router.post("/external-contacts/{contact_id}/delete")
async def external_contact_delete(
    contact_id: int,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    from app.dependencies import can_manage_members
    if not (user.is_admin or can_manage_members(member)):
        raise HTTPException(403)
    contact = await db.get(ExternalContact, contact_id)
    if contact:
        await db.delete(contact)
        await db.commit()
    return RedirectResponse(url="/settings/?saved=contacts", status_code=303)


@router.post("/external-contacts/{contact_id}/edit")
async def external_contact_edit(
    contact_id: int,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    name: str = Form(...),
    email: str = Form(...),
    organization: str = Form(""),
    contact_type: str = Form("EXTERNAL"),
    notes: str = Form(""),
):
    user, member = ctx
    from app.dependencies import can_manage_members
    if not (user.is_admin or can_manage_members(member)):
        raise HTTPException(403)
    contact = await db.get(ExternalContact, contact_id)
    if not contact:
        raise HTTPException(404)
    contact.name = name.strip()
    contact.email = email.strip().lower()
    contact.organization = organization.strip() or None
    contact.contact_type = contact_type if contact_type in ("EXTERNAL", "VISITOR") else "EXTERNAL"
    contact.notes = notes.strip() or None
    await db.commit()
    return RedirectResponse(url="/settings/?saved=contacts", status_code=303)


@router.post("/external-contacts/{contact_id}/toggle")
async def external_contact_toggle(
    contact_id: int,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    from app.dependencies import can_manage_members
    if not (user.is_admin or can_manage_members(member)):
        raise HTTPException(403)
    contact = await db.get(ExternalContact, contact_id)
    if contact:
        contact.is_active = not contact.is_active
        await db.commit()
    return RedirectResponse(url="/settings/?saved=contacts", status_code=303)


# ── Préférences personnelles (accessible à tous les membres) ───────────────

@router.get("/notifications", response_class=HTMLResponse)
async def notifications_prefs(
    request: Request,
    ctx: Annotated[object, Depends(require_auth)],
):
    user, member = ctx
    return templates.TemplateResponse(request, "pages/settings/notifications.html", {
        "current_member": member,
        "current_user": user,
        "saved": request.query_params.get("saved"),
    })


@router.post("/notifications")
async def notifications_prefs_save(
    request: Request,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    email_notifications: Optional[str] = Form(None),
):
    user, member = ctx
    member.email_notifications = email_notifications == "on"
    await db.commit()
    return RedirectResponse(url="/settings/notifications?saved=1", status_code=303)


@router.post("/users/{user_id}/toggle-admin")
async def toggle_admin(
    request: Request,
    user_id: int,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    current_user, _ = ctx
    target = await db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404)
    if target.id == current_user.id:
        raise HTTPException(status_code=400, detail="Impossible de modifier son propre statut admin.")
    target.is_admin = not target.is_admin
    await db.commit()
    return RedirectResponse(url="/settings/?saved=admin", status_code=303)

