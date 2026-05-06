"""Router Membres — CRUD complet"""
from typing import Annotated, Optional
from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import (
    get_current_user, require_auth, require_admin,
    hash_password, can_manage_members,
)
from app.models.identity import (
    Member, User, MasonicGrade, MemberStatus, LodgeFunction,
    MemberResponsibility, ResponsibilityType,
)
from app.models.lodge import MasonicYear

router = APIRouter(prefix="/members", tags=["members"])
templates = Jinja2Templates(directory="app/templates")


def _grade_label(g: MasonicGrade) -> str:
    return {"APPRENTI": "Apprenti", "COMPAGNON": "Compagnon", "MAITRE": "Maître"}.get(g, g)


def _function_label(f) -> str:
    labels = {
        "VM":                "Vénérable Maître",
        "PREMIER_S":         "1er Surveillant",
        "SECOND_S":          "2e Surveillant",
        "ORATEUR":           "Orateur",
        "SECRETAIRE":        "Secrétaire",
        "TRESORIER":         "Trésorier",
        "EXPERT":            "Expert",
        "MAITRE_CEREMONIES": "Maître des Cérémonies",
        "HARMONISTE":        "Maître Harmoniste",
        "HOSPITALIER":       "Hospitalier",
        "TUILEUR":           "Tuileur",
        "ARCHITECTE":        "Architecte",
        "MAITRE_BANQUETS":   "Maître des Banquets",
        "FRERE":             "Frère",
    }
    v = f.value if hasattr(f, "value") else str(f)
    return labels.get(v, v)


def _responsibility_label(t) -> str:
    labels = {
        "OFFICE_SECOND":   "Office cumulé",
        "DELEGUE_CONGRES": "Délégué au Congrès",
        "DELEGUE_CONVENT": "Délégué au Convent",
        "DELEGUE_AUTRE":   "Délégué (autre)",
        "COMMISSION":      "Commission",
        "REPRESENTANT":    "Représentant",
        "OTHER":           "Autre responsabilité",
    }
    v = t.value if hasattr(t, "value") else str(t)
    return labels.get(v, v)


def _status_label(s: MemberStatus) -> str:
    return {
        "ACTIVE": "Actif",
        "LEAVE": "En congé",
        "RESIGNED": "Démissionnaire",
        "STRUCK": "Radié",
        "DECEASED": "Décédé",
    }.get(s, s)


# ── Liste des membres ─────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def members_list(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    search: str = "",
    grade: str = "",
    status_filter: str = "",
):
    user, member = ctx

    query = select(Member).order_by(Member.last_name, Member.first_name)
    if search:
        term = f"%{search}%"
        query = query.where(
            or_(Member.last_name.ilike(term), Member.first_name.ilike(term), Member.email.ilike(term))
        )
    if grade:
        query = query.where(Member.masonic_grade == grade)
    if status_filter:
        query = query.where(Member.status == status_filter)

    result = await db.execute(query)
    members = result.scalars().all()

    return templates.TemplateResponse(request, "pages/members/list.html", {
        "current_member": member,
        "current_user": user,
        "members": members,
        "search": search,
        "grade_filter": grade,
        "status_filter": status_filter,
        "grade_label": _grade_label,
        "function_label": _function_label,
        "status_label": _status_label,
        "MasonicGrade": MasonicGrade,
        "MemberStatus": MemberStatus,
        "can_manage": can_manage_members(member) or user.is_admin,
    })


# ── Fiche membre ──────────────────────────────────────────────────────────────

@router.get("/{member_id}", response_class=HTMLResponse)
async def member_detail(
    request: Request,
    member_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, current_member = ctx

    from sqlalchemy.orm import selectinload
    result = await db.execute(
        select(Member)
        .options(selectinload(Member.responsibilities))
        .where(Member.id == member_id)
    )
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Membre introuvable")

    # Récupérer le compte utilisateur associé
    user_result = await db.execute(select(User).where(User.member_id == member_id))
    target_user = user_result.scalar_one_or_none()

    return templates.TemplateResponse(request, "pages/members/detail.html", {
        "current_member": current_member,
        "current_user": user,
        "target": target,
        "target_user": target_user,
        "grade_label": _grade_label,
        "function_label": _function_label,
        "status_label": _status_label,
        "can_manage": can_manage_members(current_member) or user.is_admin,
        "is_own_profile": target.id == current_member.id,
    })


# ── Formulaire création ───────────────────────────────────────────────────────

@router.get("/new/form", response_class=HTMLResponse)
async def member_new_form(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, current_member = ctx
    if not (can_manage_members(current_member) or user.is_admin):
        raise HTTPException(status_code=403, detail="Accès refusé")

    return templates.TemplateResponse(request, "pages/members/form.html", {
        "current_member": current_member,
        "current_user": user,
        "target": None,
        "MasonicGrade": MasonicGrade,
        "MemberStatus": MemberStatus,
        "LodgeFunction": LodgeFunction,
        "grade_label": _grade_label,
        "function_label": _function_label,
        "status_label": _status_label,
        "errors": {},
        "form_action": "/members/new/form",
        "is_new": True,
    })


@router.post("/new/form", response_class=HTMLResponse)
async def member_create(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    last_name:      str = Form(...),
    first_name:     str = Form(...),
    email:          str = Form(...),
    civility:       str = Form(""),
    phone:          str = Form(""),
    masonic_grade:  str = Form("APPRENTI"),
    lodge_function: str = Form("FRERE"),
    member_status:  str = Form("ACTIVE"),
    birth_date:     str = Form(""),
    initiation_date: str = Form(""),
    login:          str = Form(""),
    password:       str = Form(""),
    is_admin:       str = Form(""),
):
    user, current_member = ctx
    if not (can_manage_members(current_member) or user.is_admin):
        raise HTTPException(status_code=403, detail="Accès refusé")

    errors = {}

    # Vérifier email unique
    existing = await db.execute(select(Member).where(Member.email == email))
    if existing.scalar_one_or_none():
        errors["email"] = "Cet email est déjà utilisé"

    if errors:
        return templates.TemplateResponse(request, "pages/members/form.html", {
            "current_member": current_member,
            "current_user": user,
            "target": None,
            "MasonicGrade": MasonicGrade,
            "MemberStatus": MemberStatus,
            "LodgeFunction": LodgeFunction,
            "grade_label": _grade_label,
            "function_label": _function_label,
            "status_label": _status_label,
            "errors": errors,
            "form_action": "/members/new/form",
            "is_new": True,
            "form_data": {
                "last_name": last_name, "first_name": first_name,
                "email": email, "civility": civility, "phone": phone,
                "masonic_grade": masonic_grade, "lodge_function": lodge_function,
                "member_status": member_status,
            },
        }, status_code=422)

    def parse_date(s: str):
        if s:
            try:
                return date.fromisoformat(s)
            except ValueError:
                return None
        return None

    new_member = Member(
        last_name=last_name.strip().upper(),
        first_name=first_name.strip().title(),
        email=email.strip().lower(),
        civility=civility or None,
        phone=phone or None,
        masonic_grade=MasonicGrade(masonic_grade),
        lodge_function=LodgeFunction(lodge_function),
        status=MemberStatus(member_status),
        birth_date=parse_date(birth_date),
        initiation_date=parse_date(initiation_date),
    )
    db.add(new_member)
    await db.flush()  # pour obtenir l'ID

    # Créer un compte utilisateur si login fourni
    if login.strip():
        new_user = User(
            member_id=new_member.id,
            login=login.strip().lower(),
            password_hash=hash_password(password or "changeme123"),
            is_active=True,
            is_admin=bool(is_admin) and user.is_admin,
        )
        db.add(new_user)

    await db.commit()
    return RedirectResponse(url=f"/members/{new_member.id}", status_code=302)


# ── Formulaire édition ────────────────────────────────────────────────────────

@router.get("/{member_id}/edit", response_class=HTMLResponse)
async def member_edit_form(
    request: Request,
    member_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, current_member = ctx
    # Peut éditer : admin, secrétaire/VM, ou son propre profil
    if not (can_manage_members(current_member) or user.is_admin or current_member.id == member_id):
        raise HTTPException(status_code=403, detail="Accès refusé")

    result = await db.execute(select(Member).where(Member.id == member_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Membre introuvable")

    user_result = await db.execute(select(User).where(User.member_id == member_id))
    target_user = user_result.scalar_one_or_none()

    return templates.TemplateResponse(request, "pages/members/form.html", {
        "current_member": current_member,
        "current_user": user,
        "target": target,
        "target_user": target_user,
        "MasonicGrade": MasonicGrade,
        "MemberStatus": MemberStatus,
        "LodgeFunction": LodgeFunction,
        "grade_label": _grade_label,
        "function_label": _function_label,
        "status_label": _status_label,
        "errors": {},
        "form_action": f"/members/{member_id}/edit",
        "is_new": False,
    })


@router.post("/{member_id}/edit", response_class=HTMLResponse)
async def member_update(
    request: Request,
    member_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    last_name:       str = Form(...),
    first_name:      str = Form(...),
    email:           str = Form(...),
    civility:        str = Form(""),
    phone:           str = Form(""),
    masonic_grade:   str = Form("APPRENTI"),
    lodge_function:  str = Form("FRERE"),
    member_status:   str = Form("ACTIVE"),
    birth_date:      str = Form(""),
    initiation_date: str = Form(""),
    companion_date:  str = Form(""),
    master_date:     str = Form(""),
    program_optin:   str = Form(""),
):
    user, current_member = ctx
    if not (can_manage_members(current_member) or user.is_admin or current_member.id == member_id):
        raise HTTPException(status_code=403, detail="Accès refusé")

    result = await db.execute(select(Member).where(Member.id == member_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Membre introuvable")

    errors = {}
    # Vérifier email unique (sauf si inchangé)
    if email != target.email:
        existing = await db.execute(select(Member).where(Member.email == email, Member.id != member_id))
        if existing.scalar_one_or_none():
            errors["email"] = "Cet email est déjà utilisé"

    if errors:
        user_result = await db.execute(select(User).where(User.member_id == member_id))
        target_user = user_result.scalar_one_or_none()
        return templates.TemplateResponse(request, "pages/members/form.html", {
            "current_member": current_member,
            "current_user": user,
            "target": target,
            "target_user": target_user,
            "MasonicGrade": MasonicGrade,
            "MemberStatus": MemberStatus,
            "LodgeFunction": LodgeFunction,
            "grade_label": _grade_label,
            "function_label": _function_label,
            "status_label": _status_label,
            "errors": errors,
            "form_action": f"/members/{member_id}/edit",
            "is_new": False,
        }, status_code=422)

    def parse_date(s: str):
        if s:
            try:
                return date.fromisoformat(s)
            except ValueError:
                return None
        return None

    # Mise à jour des champs
    target.last_name     = last_name.strip().upper()
    target.first_name    = first_name.strip().title()
    target.email         = email.strip().lower()
    target.civility      = civility or None
    target.phone         = phone or None
    target.program_optin = bool(program_optin)

    # Seuls admin/VM/Secrétaire peuvent changer grade/statut/fonction
    if can_manage_members(current_member) or user.is_admin:
        target.masonic_grade   = MasonicGrade(masonic_grade)
        target.lodge_function  = LodgeFunction(lodge_function)
        target.status          = MemberStatus(member_status)
        target.initiation_date = parse_date(initiation_date)
        target.companion_date  = parse_date(companion_date)
        target.master_date     = parse_date(master_date)

    target.birth_date = parse_date(birth_date)

    await db.commit()
    return RedirectResponse(url=f"/members/{member_id}", status_code=302)


# ── Activation / Désactivation du compte ──────────────────────────────────────

@router.post("/{member_id}/toggle-user", response_class=HTMLResponse)
async def toggle_user_account(
    request: Request,
    member_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, current_member = ctx
    if not user.is_admin:
        raise HTTPException(status_code=403)

    user_result = await db.execute(select(User).where(User.member_id == member_id))
    target_user = user_result.scalar_one_or_none()
    if target_user:
        target_user.is_active = not target_user.is_active
        await db.commit()

    return RedirectResponse(url=f"/members/{member_id}", status_code=302)


# ── Responsabilités ───────────────────────────────────────────────────────────

@router.get("/{member_id}/responsibilities", response_class=HTMLResponse)
async def responsibilities_page(
    request: Request,
    member_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, current_member = ctx
    if not (can_manage_members(current_member) or user.is_admin or current_member.id == member_id):
        raise HTTPException(status_code=403)

    result = await db.execute(select(Member).where(Member.id == member_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404)

    # Charger les responsabilités avec l'année maçonnique
    resp_result = await db.execute(
        select(MemberResponsibility)
        .where(MemberResponsibility.member_id == member_id)
        .order_by(MemberResponsibility.is_active.desc(), MemberResponsibility.type)
    )
    responsibilities = resp_result.scalars().all()

    # Années maçonniques pour le sélecteur
    years_result = await db.execute(
        select(MasonicYear).order_by(MasonicYear.start_date.desc())
    )
    years = years_result.scalars().all()
    current_year = next((y for y in years if y.is_current), years[0] if years else None)

    return templates.TemplateResponse(request, "pages/members/responsibilities.html", {
        "current_member": current_member,
        "current_user": user,
        "target": target,
        "responsibilities": responsibilities,
        "years": years,
        "current_year": current_year,
        "ResponsibilityType": ResponsibilityType,
        "LodgeFunction": LodgeFunction,
        "responsibility_label": _responsibility_label,
        "function_label": _function_label,
        "can_manage": can_manage_members(current_member) or user.is_admin,
    })


@router.post("/{member_id}/responsibilities/add", response_class=HTMLResponse)
async def responsibility_add(
    request: Request,
    member_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    resp_type:       str = Form(...),
    label:           str = Form(...),
    lodge_function:  str = Form(""),
    masonic_year_id: str = Form(""),
    start_date:      str = Form(""),
    end_date:        str = Form(""),
    notes:           str = Form(""),
):
    user, current_member = ctx
    if not (can_manage_members(current_member) or user.is_admin):
        raise HTTPException(status_code=403)

    def parse_date(s: str):
        if s:
            try:
                return date.fromisoformat(s)
            except ValueError:
                return None
        return None

    resp = MemberResponsibility(
        member_id=member_id,
        type=ResponsibilityType(resp_type),
        label=label.strip(),
        lodge_function=LodgeFunction(lodge_function) if lodge_function else None,
        masonic_year_id=int(masonic_year_id) if masonic_year_id else None,
        start_date=parse_date(start_date),
        end_date=parse_date(end_date),
        notes=notes or None,
        is_active=True,
    )
    db.add(resp)
    await db.commit()
    return RedirectResponse(url=f"/members/{member_id}/responsibilities", status_code=302)


@router.post("/{member_id}/responsibilities/{resp_id}/toggle", response_class=HTMLResponse)
async def responsibility_toggle(
    request: Request,
    member_id: int,
    resp_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, current_member = ctx
    if not (can_manage_members(current_member) or user.is_admin):
        raise HTTPException(status_code=403)

    result = await db.execute(
        select(MemberResponsibility).where(
            MemberResponsibility.id == resp_id,
            MemberResponsibility.member_id == member_id,
        )
    )
    resp = result.scalar_one_or_none()
    if resp:
        resp.is_active = not resp.is_active
        await db.commit()

    return RedirectResponse(url=f"/members/{member_id}/responsibilities", status_code=302)


@router.post("/{member_id}/responsibilities/{resp_id}/delete", response_class=HTMLResponse)
async def responsibility_delete(
    request: Request,
    member_id: int,
    resp_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, current_member = ctx
    if not (can_manage_members(current_member) or user.is_admin):
        raise HTTPException(status_code=403)

    result = await db.execute(
        select(MemberResponsibility).where(
            MemberResponsibility.id == resp_id,
            MemberResponsibility.member_id == member_id,
        )
    )
    resp = result.scalar_one_or_none()
    if resp:
        await db.delete(resp)
        await db.commit()

    return RedirectResponse(url=f"/members/{member_id}/responsibilities", status_code=302)
