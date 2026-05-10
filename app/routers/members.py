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
    Member, User, MasonicGrade, MemberStatus, LodgeFunction, MembershipType,
    MemberResponsibility, ResponsibilityType, RoleQualifier,
    Group, GroupType,
)
from app.models.lodge import MasonicYear, LodgeOffice
from app.models.finance import MemberContribution, ContributionTier, ContributionStatus

router = APIRouter(prefix="/members", tags=["members"])
templates = Jinja2Templates(directory="app/templates")

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


def _fn_from_label(label: str) -> LodgeFunction:
    normalized = label.lower().replace("∴", ".").replace(":", "")
    for keyword, fn in _LABEL_FUNCTION_MAP:
        if keyword in normalized:
            return fn
    return LodgeFunction.FRERE


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
        "OFFICE_SECOND":     "Office rituel cumulé",
        "DELEGUE_CONVENT":   "Délégué au Convent",
        "DELEGUE_CONGRES":   "Délégué au Congrès",
        "WEBMESTRE":         "Webmestre",
        "CORRESPONDANT_NUM": "Correspondant numérique",
        "COMMISSION":        "Commission",
        "OTHER":             "Autre",
    }
    v = t.value if hasattr(t, "value") else str(t)
    return labels.get(v, v)


def _qualifier_label(q) -> str:
    if q is None:
        return ""
    labels = {
        "TITULAIRE":   "Titulaire",
        "SUPPLEANT_1": "1er suppléant",
        "SUPPLEANT_2": "2e suppléant",
        "PRESIDENT":   "Président",
        "MEMBRE":      "Membre",
    }
    v = q.value if hasattr(q, "value") else str(q)
    return labels.get(v, v)


async def _get_offices(db: AsyncSession) -> list:
    r = await db.execute(select(LodgeOffice).order_by(LodgeOffice.sort_order, LodgeOffice.id))
    return r.scalars().all()


async def _current_office_id(db: AsyncSession, member_id: int) -> int | None:
    r = await db.execute(select(LodgeOffice.id).where(LodgeOffice.member_id == member_id).limit(1))
    return r.scalar_one_or_none()


async def _assign_office(db: AsyncSession, member_id: int, office_id: int | None):
    """Retire le membre de son office actuel, puis l'affecte au nouveau et sync lodge_function."""
    old = await db.execute(select(LodgeOffice).where(LodgeOffice.member_id == member_id))
    for o in old.scalars().all():
        o.member_id = None
    mem = await db.get(Member, member_id)
    if office_id:
        new_office = await db.get(LodgeOffice, office_id)
        if new_office:
            new_office.member_id = member_id
            if mem:
                mem.lodge_function = _fn_from_label(new_office.label)
    else:
        if mem:
            mem.lodge_function = LodgeFunction.FRERE


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

    # Exclure les super-admins (comptes techniques) de la liste
    admin_member_ids = select(User.member_id).where(
        User.is_admin == True, User.member_id.isnot(None)
    )
    query = (
        select(Member)
        .where(Member.id.not_in(admin_member_ids))
        .order_by(Member.last_name, Member.first_name)
    )
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

    # Construire un dict member_id → label d'office
    offices_r = await db.execute(select(LodgeOffice).where(LodgeOffice.member_id.isnot(None)))
    office_by_member = {o.member_id: o.label for o in offices_r.scalars().all()}

    return templates.TemplateResponse(request, "pages/members/list.html", {
        "current_member": member,
        "current_user": user,
        "members": members,
        "office_by_member": office_by_member,
        "search": search,
        "grade_filter": grade,
        "status_filter": status_filter,
        "grade_label": _grade_label,
        "status_label": _status_label,
        "MasonicGrade": MasonicGrade,
        "MembershipType": MembershipType,
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

    # Cotisation de l'année en cours
    from sqlalchemy.orm import selectinload as _sil
    year_r = await db.execute(select(MasonicYear).where(MasonicYear.is_current == True).limit(1))
    current_year = year_r.scalar_one_or_none()
    member_contrib = None
    contrib_tier = None
    if current_year:
        cr = await db.execute(
            select(MemberContribution)
            .options(_sil(MemberContribution.payments), _sil(MemberContribution.quitus))
            .where(
                MemberContribution.member_id == member_id,
                MemberContribution.masonic_year_id == current_year.id,
            )
        )
        member_contrib = cr.scalar_one_or_none()
        if member_contrib:
            tier_r = await db.execute(
                select(ContributionTier).where(ContributionTier.id == member_contrib.tier_id)
            )
            contrib_tier = tier_r.scalar_one_or_none()

    # Libellé de l'office rituel (LodgeOffice) — plus précis que lodge_function
    office_r = await db.execute(select(LodgeOffice).where(LodgeOffice.member_id == target.id).limit(1))
    office = office_r.scalar_one_or_none()
    office_label = office.label if office else None

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
        "current_year": current_year,
        "member_contrib": member_contrib,
        "contrib_tier": contrib_tier,
        "ContributionStatus": ContributionStatus,
        "office_label": office_label,
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

    offices = await _get_offices(db)
    return templates.TemplateResponse(request, "pages/members/form.html", {
        "current_member": current_member,
        "current_user": user,
        "target": None,
        "offices": offices,
        "current_office_id": None,
        "MasonicGrade": MasonicGrade,
        "MembershipType": MembershipType,
        "MemberStatus": MemberStatus,
        "grade_label": _grade_label,
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
    office_id:      str = Form(""),
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
        offices = await _get_offices(db)
        return templates.TemplateResponse(request, "pages/members/form.html", {
            "current_member": current_member,
            "current_user": user,
            "target": None,
            "offices": offices,
            "current_office_id": int(office_id) if office_id.isdigit() else None,
            "MasonicGrade": MasonicGrade,
        "MembershipType": MembershipType,
            "MemberStatus": MemberStatus,
            "grade_label": _grade_label,
            "status_label": _status_label,
            "errors": errors,
            "form_action": "/members/new/form",
            "is_new": True,
            "form_data": {
                "last_name": last_name, "first_name": first_name,
                "email": email, "civility": civility, "phone": phone,
                "masonic_grade": masonic_grade,
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
        status=MemberStatus(member_status),
        birth_date=parse_date(birth_date),
        initiation_date=parse_date(initiation_date),
    )
    db.add(new_member)
    await db.flush()  # pour obtenir l'ID
    await _assign_office(db, new_member.id, int(office_id) if office_id.isdigit() else None)

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

    offices = await _get_offices(db)
    current_office_id = await _current_office_id(db, member_id)
    return templates.TemplateResponse(request, "pages/members/form.html", {
        "current_member": current_member,
        "current_user": user,
        "target": target,
        "target_user": target_user,
        "offices": offices,
        "current_office_id": current_office_id,
        "MasonicGrade": MasonicGrade,
        "MembershipType": MembershipType,
        "MemberStatus": MemberStatus,
        "grade_label": _grade_label,
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
    masonic_grade:    str = Form("APPRENTI"),
    membership_type:  str = Form("APPARTENANCE"),
    office_id:        str = Form(""),
    member_status:    str = Form("ACTIVE"),
    birth_date:       str = Form(""),
    initiation_date:       str = Form(""),
    companion_date:        str = Form(""),
    master_date:           str = Form(""),
    membership_start_date: str = Form(""),
    status_date:           str = Form(""),
    program_optin:         str = Form(""),
    pin_code:              str = Form(""),
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
        offices = await _get_offices(db)
        current_office_id = await _current_office_id(db, member_id)
        return templates.TemplateResponse(request, "pages/members/form.html", {
            "current_member": current_member,
            "current_user": user,
            "target": target,
            "target_user": target_user,
            "offices": offices,
            "current_office_id": current_office_id,
            "MasonicGrade": MasonicGrade,
        "MembershipType": MembershipType,
            "MemberStatus": MemberStatus,
            "grade_label": _grade_label,
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
        prev_status = target.status
        target.masonic_grade          = MasonicGrade(masonic_grade)
        target.membership_type        = MembershipType(membership_type)
        target.status                 = MemberStatus(member_status)
        target.initiation_date        = parse_date(initiation_date)
        target.companion_date         = parse_date(companion_date)
        target.master_date            = parse_date(master_date)
        target.membership_start_date  = parse_date(membership_start_date)
        target.status_date            = parse_date(status_date)
        await _assign_office(db, member_id, int(office_id) if office_id.isdigit() else None)

        # Démission / radiation → exempter cotisations + auto-remplir date de départ
        leaving = {MemberStatus.RESIGNED, MemberStatus.STRUCK, MemberStatus.DECEASED}
        if target.status in leaving and prev_status not in leaving:
            if not target.status_date:
                target.status_date = date.today()
        if target.status in leaving and prev_status not in leaving:
            from app.models.finance import MemberContribution, ContributionStatus
            open_r = await db.execute(
                select(MemberContribution).where(
                    MemberContribution.member_id == member_id,
                    MemberContribution.status.in_([ContributionStatus.PENDING, ContributionStatus.PARTIAL]),
                )
            )
            for c in open_r.scalars().all():
                c.status = ContributionStatus.EXEMPT
                c.notes = (c.notes or "") + f"\nExempté automatiquement — {target.status.value} le {date.today()}"
        if pin_code.strip():
            target.pin_code_hash = hash_password(pin_code.strip())

    target.birth_date = parse_date(birth_date)

    await db.commit()
    return RedirectResponse(url=f"/members/{member_id}", status_code=302)


# ── Reset mot de passe (admin) ────────────────────────────────────────────────

@router.post("/{member_id}/reset-password", response_class=HTMLResponse)
async def reset_member_password(
    member_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    user, current_member = ctx
    if not user.is_admin:
        raise HTTPException(status_code=403)

    if new_password != confirm_password:
        return RedirectResponse(
            url=f"/members/{member_id}/edit?pwd_error=mismatch",
            status_code=302,
        )
    if len(new_password) < 8:
        return RedirectResponse(
            url=f"/members/{member_id}/edit?pwd_error=tooshort",
            status_code=302,
        )

    user_result = await db.execute(select(User).where(User.member_id == member_id))
    target_user = user_result.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="Ce membre n'a pas de compte")

    target_user.password_hash = hash_password(new_password)
    await db.commit()

    return RedirectResponse(url=f"/members/{member_id}/edit?pwd_ok=1", status_code=302)


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

    # Commissions existantes
    groups_result = await db.execute(
        select(Group).where(Group.type == GroupType.COMMISSION).order_by(Group.name)
    )
    commissions = groups_result.scalars().all()

    return templates.TemplateResponse(request, "pages/members/responsibilities.html", {
        "current_member": current_member,
        "current_user": user,
        "target": target,
        "responsibilities": responsibilities,
        "years": years,
        "current_year": current_year,
        "commissions": commissions,
        "ResponsibilityType": ResponsibilityType,
        "RoleQualifier": RoleQualifier,
        "LodgeFunction": LodgeFunction,
        "responsibility_label": _responsibility_label,
        "qualifier_label": _qualifier_label,
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
    qualifier:       str = Form(""),
    label:           str = Form(...),
    lodge_function:  str = Form(""),
    group_id:        str = Form(""),
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
        qualifier=RoleQualifier(qualifier) if qualifier else None,
        label=label.strip(),
        lodge_function=LodgeFunction(lodge_function) if lodge_function else None,
        group_id=int(group_id) if group_id else None,
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


@router.post("/{member_id}/delete")
async def member_delete(
    request: Request,
    member_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, current_member = ctx
    if not ((current_member and can_manage_members(current_member)) or user.is_admin):
        raise HTTPException(status_code=403)

    result = await db.execute(select(Member).where(Member.id == member_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404)

    if current_member and current_member.id == member_id:
        raise HTTPException(status_code=400, detail="Impossible de supprimer votre propre compte.")

    # Delete User account
    u_result = await db.execute(select(User).where(User.member_id == member_id))
    u = u_result.scalar_one_or_none()
    if u:
        await db.delete(u)

    # Delete payments, quitus, then contributions
    from app.models.finance import Payment, Quitus
    c_result = await db.execute(
        select(MemberContribution).where(MemberContribution.member_id == member_id)
    )
    for c in c_result.scalars().all():
        q_result = await db.execute(
            select(Quitus).where(Quitus.contribution_id == c.id)
        )
        for q in q_result.scalars().all():
            await db.delete(q)
        p_result = await db.execute(
            select(Payment).where(Payment.member_contribution_id == c.id)
        )
        for p in p_result.scalars().all():
            await db.delete(p)
        await db.flush()
        await db.delete(c)

    # Delete responsibilities
    r_result = await db.execute(
        select(MemberResponsibility).where(MemberResponsibility.member_id == member_id)
    )
    for r in r_result.scalars().all():
        await db.delete(r)

    # Nullify nullable FK references in lodge offices
    from app.models.lodge import LodgeOffice
    off_result = await db.execute(
        select(LodgeOffice).where(LodgeOffice.member_id == member_id)
    )
    for off in off_result.scalars().all():
        off.member_id = None

    await db.flush()
    await db.delete(target)
    await db.commit()

    return RedirectResponse(url="/members/", status_code=303)
