"""Router — Gestion des comptes visiteurs (accès portail restreint pour les
frères d'autres loges). Réservé aux officiers habilités (VM/Secrétaire, ou
délégation fine can_manage_guests) — voir app/models/guest.py et
app/dependencies.py (get_current_guest / require_guest_auth / can_manage_guests)
pour l'architecture "deny by default" sur laquelle repose cette fonctionnalité.
"""
import secrets
from datetime import datetime, timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_auth, can_manage_guests
from app.models.guest import GuestAccount, GuestSession
from app.models.identity import MasonicGrade
from app.models.meetings import Visitor
from app.services.email import _send_raw

router = APIRouter(prefix="/gestion-visiteurs", tags=["guests"])
from app.template_engine import templates

MAGIC_LINK_VALIDITY = timedelta(days=7)


def _require_manage(user, member) -> None:
    if not (getattr(user, "is_admin", False) or can_manage_guests(member)):
        raise HTTPException(status_code=403, detail="Droits insuffisants")


async def _send_invitation_email(guest: GuestAccount, base_url: str) -> None:
    link = f"{base_url}/auth/magic/{guest.magic_token}"
    html = f"""<p>Bonjour {guest.first_name},</p>
<p>Vous avez été invité·e à accéder à l'espace visiteur du portail de la loge,
en tant que {guest.civility_symbol} d'une autre loge{(' (' + guest.home_lodge + ')') if guest.home_lodge else ''}.</p>
<p><a href="{link}" style="background:#2c7a7b;color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none;display:inline-block;">
  Accéder à mon espace visiteur →
</a></p>
<p style="color:#6b7280;font-size:13px;">Ce lien est personnel et valable 7 jours. Lors de votre première connexion,
il vous sera demandé de définir un mot de passe et d'activer une double authentification (obligatoire).</p>"""
    text = (
        f"Bonjour {guest.first_name},\n\n"
        "Vous avez été invité·e à accéder à l'espace visiteur du portail de la loge.\n"
        f"Lien personnel (valable 7 jours) : {link}\n\n"
        "Lors de votre première connexion, il vous sera demandé de définir un mot de passe "
        "et d'activer une double authentification (obligatoire)."
    )
    await _send_raw(guest.email, "[Portail Loge] Invitation — accès visiteur", html, text)


def _status_label(guest: GuestAccount) -> str:
    if not guest.is_active:
        return "Désactivé"
    if guest.expires_at and guest.expires_at < datetime.utcnow():
        return "Expiré"
    if not guest.onboarding_completed:
        return "Invitation en attente"
    return "Actif"


@router.get("/", response_class=HTMLResponse)
async def guests_list(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    _require_manage(user, member)

    r = await db.execute(select(GuestAccount).order_by(GuestAccount.created_at.desc()))
    guests = r.scalars().all()
    status_labels = {g.id: _status_label(g) for g in guests}

    return templates.TemplateResponse(request, "pages/guests/list.html", {
        "current_user": user,
        "current_member": member,
        "guests": guests,
        "status_labels": status_labels,
    })


@router.get("/new", response_class=HTMLResponse)
async def guest_new_form(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    _require_manage(user, member)
    return templates.TemplateResponse(request, "pages/guests/form.html", {
        "current_user": user,
        "current_member": member,
        "guest": None,
        "masonic_grades": list(MasonicGrade),
    })


@router.post("/new")
async def guest_create(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    civility: str = Form(...),
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    home_lodge: str = Form(""),
    obedience: str = Form(""),
    masonic_grade: str = Form(...),
    expires_at: str = Form(""),
):
    user, member = ctx
    _require_manage(user, member)

    email = email.strip().lower()
    existing = (
        await db.execute(select(GuestAccount).where(GuestAccount.email == email))
    ).scalar_one_or_none()
    if existing:
        return templates.TemplateResponse(request, "pages/guests/form.html", {
            "current_user": user, "current_member": member, "guest": None,
            "masonic_grades": list(MasonicGrade),
            "error": "Un compte visiteur existe déjà avec cet email.",
        }, status_code=400)

    # Si un visiteur (présence en tenue) existant partage cet email, on relie
    # le nouveau compte à son historique plutôt que de créer un doublon.
    linked_visitor = (
        await db.execute(select(Visitor).where(func.lower(Visitor.email) == email))
    ).scalar_one_or_none()

    guest = GuestAccount(
        civility=civility if civility in ("F", "S") else None,
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        email=email,
        home_lodge=home_lodge.strip() or None,
        obedience=obedience.strip() or None,
        masonic_grade=MasonicGrade(masonic_grade),
        invited_by_id=member.id,
        linked_visitor_id=linked_visitor.id if linked_visitor else None,
        magic_token=secrets.token_urlsafe(32),
        magic_token_expires_at=datetime.utcnow() + MAGIC_LINK_VALIDITY,
    )
    if expires_at.strip():
        try:
            guest.expires_at = datetime.strptime(expires_at.strip(), "%Y-%m-%d")
        except ValueError:
            pass
    db.add(guest)
    await db.commit()

    try:
        await _send_invitation_email(guest, str(request.base_url).rstrip("/"))
    except Exception:
        pass

    try:
        from app.services.audit import log_audit
        await log_audit(
            db, actor_id=member.id, action="GUEST_INVITE",
            target_label=f"{guest.full_name} ({guest.email})",
            request=request, commit=True,
        )
    except Exception:
        pass

    return RedirectResponse(url=f"/gestion-visiteurs/{guest.id}?invited=1", status_code=303)


@router.get("/{guest_id}", response_class=HTMLResponse)
async def guest_detail(
    guest_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    _require_manage(user, member)
    guest = await db.get(GuestAccount, guest_id)
    if not guest:
        raise HTTPException(404)

    sessions_count = (
        await db.execute(
            select(func.count()).select_from(GuestSession).where(GuestSession.guest_account_id == guest.id)
        )
    ).scalar_one()

    visitors = (
        await db.execute(select(Visitor).order_by(Visitor.created_at.desc()).limit(300))
    ).scalars().all()
    linked_visitor = await db.get(Visitor, guest.linked_visitor_id) if guest.linked_visitor_id else None

    return templates.TemplateResponse(request, "pages/guests/detail.html", {
        "current_user": user,
        "current_member": member,
        "guest": guest,
        "status_label": _status_label(guest),
        "sessions_count": sessions_count,
        "invited": request.query_params.get("invited") == "1",
        "visitors": visitors,
        "linked_visitor": linked_visitor,
        "masonic_grades": list(MasonicGrade),
    })


@router.post("/{guest_id}/link-visitor")
async def guest_link_visitor(
    guest_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    visitor_id: str = Form(""),
):
    """Relie (ou délie) manuellement ce compte visiteur à un Visitor existant
    (présence en tenue) — en complément du rapprochement automatique par
    email fait à l'invitation."""
    user, member = ctx
    _require_manage(user, member)
    guest = await db.get(GuestAccount, guest_id)
    if not guest:
        raise HTTPException(404)

    if visitor_id.strip():
        visitor = await db.get(Visitor, int(visitor_id))
        if not visitor:
            raise HTTPException(404, "Visiteur introuvable")
        guest.linked_visitor_id = visitor.id
    else:
        guest.linked_visitor_id = None
    await db.commit()
    return RedirectResponse(url=f"/gestion-visiteurs/{guest.id}", status_code=303)


@router.post("/{guest_id}/edit")
async def guest_edit(
    guest_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    civility: str = Form(...),
    first_name: str = Form(...),
    last_name: str = Form(...),
    home_lodge: str = Form(""),
    obedience: str = Form(""),
    masonic_grade: str = Form(...),
    expires_at: str = Form(""),
):
    user, member = ctx
    _require_manage(user, member)
    guest = await db.get(GuestAccount, guest_id)
    if not guest:
        raise HTTPException(404)

    guest.civility = civility if civility in ("F", "S") else None
    guest.first_name = first_name.strip()
    guest.last_name = last_name.strip()
    guest.home_lodge = home_lodge.strip() or None
    guest.obedience = obedience.strip() or None
    guest.masonic_grade = MasonicGrade(masonic_grade)
    guest.expires_at = None
    if expires_at.strip():
        try:
            guest.expires_at = datetime.strptime(expires_at.strip(), "%Y-%m-%d")
        except ValueError:
            pass
    await db.commit()
    return RedirectResponse(url=f"/gestion-visiteurs/{guest.id}", status_code=303)


@router.post("/{guest_id}/resend")
async def guest_resend_invite(
    guest_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Régénère un nouveau lien magique (invalide l'ancien) et le renvoie."""
    user, member = ctx
    _require_manage(user, member)
    guest = await db.get(GuestAccount, guest_id)
    if not guest:
        raise HTTPException(404)

    guest.magic_token = secrets.token_urlsafe(32)
    guest.magic_token_expires_at = datetime.utcnow() + MAGIC_LINK_VALIDITY
    await db.commit()

    try:
        await _send_invitation_email(guest, str(request.base_url).rstrip("/"))
    except Exception:
        pass

    return RedirectResponse(url=f"/gestion-visiteurs/{guest.id}?invited=1", status_code=303)


@router.post("/{guest_id}/toggle-active")
async def guest_toggle_active(
    guest_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    _require_manage(user, member)
    guest = await db.get(GuestAccount, guest_id)
    if not guest:
        raise HTTPException(404)
    guest.is_active = not guest.is_active
    if not guest.is_active:
        # Désactivation = révocation immédiate des sessions en cours.
        from sqlalchemy import delete
        await db.execute(delete(GuestSession).where(GuestSession.guest_account_id == guest.id))
    await db.commit()
    return RedirectResponse(url=f"/gestion-visiteurs/{guest.id}", status_code=303)


@router.post("/{guest_id}/delete")
async def guest_delete(
    guest_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Suppression définitive — réservée aux invitations jamais utilisées
    (onboarding non terminé). Un compte déjà actif se désactive plutôt
    qu'il ne se supprime, pour préserver son historique (forum, sondages…)."""
    user, member = ctx
    _require_manage(user, member)
    guest = await db.get(GuestAccount, guest_id)
    if not guest:
        raise HTTPException(404)
    if guest.onboarding_completed:
        raise HTTPException(400, "Ce compte a déjà été activé — désactivez-le plutôt que de le supprimer.")
    await db.delete(guest)
    await db.commit()
    return RedirectResponse(url="/gestion-visiteurs/", status_code=303)
