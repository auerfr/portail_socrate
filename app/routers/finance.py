"""Router Finance — Cotisations, budget, paiements"""
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import require_auth, require_admin
from app.models.finance import (
    BudgetLine, BudgetLineType, ContributionConfig, ContributionTier,
    MemberContribution, ContributionStatus, Payment, PaymentMethod,
)
from app.models.identity import Member, MemberStatus
from app.models.lodge import MasonicYear

router = APIRouter(prefix="/finance", tags=["finance"])
templates = Jinja2Templates(directory="app/templates")

# ── Coefficients fixes ─────────────────────────────────────────────────────
TIER_COEFFICIENTS = {1: 0.4, 2: 0.7, 3: 1.0, 4: 1.3, 5: 1.6}
TIER_LABELS = {
    1: "Très aménagée",
    2: "Aménagée",
    3: "Référence",
    4: "Confortable",
    5: "Très confortable",
}

BUDGET_TYPE_LABELS = {
    "CHARGE_FIXE": "Charge fixe",
    "CAPITATION":  "Capitation",
    "PROJET":      "Projet",
    "RESERVE":     "Réserve",
    "AUTRE":       "Autre",
}

METHOD_LABELS = {
    "CASH":     "Espèces",
    "TRANSFER": "Virement",
    "CHECK":    "Chèque",
    "OTHER":    "Autre",
}


def _round2(v: float) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def _get_current_year(db: AsyncSession) -> Optional[MasonicYear]:
    r = await db.execute(select(MasonicYear).where(MasonicYear.is_current == True))
    return r.scalar_one_or_none()


async def _get_or_create_config(db: AsyncSession, year_id: int) -> ContributionConfig:
    r = await db.execute(
        select(ContributionConfig).where(ContributionConfig.masonic_year_id == year_id)
    )
    cfg = r.scalar_one_or_none()
    if not cfg:
        cfg = ContributionConfig(
            masonic_year_id=year_id,
            reference_amount=0,
            national_capitation_rate=0,
            regional_capitation_rate=0,
            active_members_count=0,
        )
        db.add(cfg)
        await db.flush()
        # Créer les 5 tiers
        for n, coeff in TIER_COEFFICIENTS.items():
            db.add(ContributionTier(config_id=cfg.id, tier_number=n,
                                    label=TIER_LABELS[n], coefficient=coeff, amount=0))
        await db.flush()
    return cfg


def _recompute_tiers(cfg: ContributionConfig) -> None:
    """Recalcule les montants des 5 tiers depuis le reference_amount."""
    ref = float(cfg.reference_amount or 0)
    for tier in cfg.tiers:
        tier.amount = float(_round2(ref * float(tier.coefficient)))


async def _compute_t3_from_budget(db: AsyncSession, year_id: int,
                                   cfg: ContributionConfig) -> Decimal:
    """
    T3 = total charges / Σ(coeff_i × count_i)
    où count_i = nombre de membres actifs en tranche i.
    Si aucun membre affecté → on divise par le nb de membres actifs (tous en T3).
    """
    # Total charges
    r = await db.execute(
        select(func.sum(BudgetLine.amount))
        .where(BudgetLine.masonic_year_id == year_id)
    )
    total_charges = float(r.scalar_one() or 0)

    # Nombre de membres actifs
    r2 = await db.execute(
        select(func.count(Member.id)).where(Member.status == MemberStatus.ACTIVE)
    )
    active_count = r2.scalar_one() or 1

    # Distribution par tranche (depuis member_contributions)
    tier_counts: dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    r3 = await db.execute(
        select(ContributionTier.tier_number, func.count(MemberContribution.id))
        .join(MemberContribution, MemberContribution.tier_id == ContributionTier.id)
        .where(MemberContribution.masonic_year_id == year_id)
        .group_by(ContributionTier.tier_number)
    )
    assigned_total = 0
    for tier_num, cnt in r3.all():
        tier_counts[tier_num] = cnt
        assigned_total += cnt

    # Membres non affectés → T3
    tier_counts[3] += max(0, active_count - assigned_total)

    # Σ coeff × count
    denom = sum(TIER_COEFFICIENTS[n] * c for n, c in tier_counts.items())
    if denom <= 0:
        denom = active_count  # fallback

    cfg.active_members_count = active_count

    # Déduire capitation du total avant de calculer T3
    capitation_total = (float(cfg.national_capitation_rate) +
                        float(cfg.regional_capitation_rate)) * active_count
    net_charges = max(0, total_charges - capitation_total)

    return _round2(net_charges / denom)


# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/", response_class=HTMLResponse)
async def finance_dashboard(
    request: Request,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    year = await _get_current_year(db)
    if not year:
        return templates.TemplateResponse(request, "pages/finance/dashboard.html", {
            "current_member": member, "current_user": user,
            "year": None, "stats": None,
        })

    cfg = await _get_or_create_config(db, year.id)
    await db.refresh(cfg, ["tiers"])

    # Statistiques de paiement
    r = await db.execute(
        select(MemberContribution)
        .where(MemberContribution.masonic_year_id == year.id)
        .options(selectinload(MemberContribution.payments),
                 selectinload(MemberContribution.quitus))
    )
    contributions = r.scalars().all()

    total_due  = sum(float(c.total_amount) for c in contributions)
    total_paid = sum(c.amount_paid for c in contributions)
    paid_count   = sum(1 for c in contributions if c.status in (
        ContributionStatus.PAID, ContributionStatus.EXEMPT))
    pending_count = len(contributions) - paid_count

    # Total charges budget
    r2 = await db.execute(
        select(func.sum(BudgetLine.amount))
        .where(BudgetLine.masonic_year_id == year.id)
    )
    total_budget = float(r2.scalar_one() or 0)

    # Membres actifs sans cotisation assignée
    r3 = await db.execute(select(func.count(Member.id)).where(Member.status == MemberStatus.ACTIVE))
    active_count = r3.scalar_one() or 0
    unassigned = active_count - len(contributions)

    stats = {
        "total_due": total_due,
        "total_paid": total_paid,
        "total_remaining": total_due - total_paid,
        "paid_count": paid_count,
        "pending_count": pending_count,
        "unassigned": unassigned,
        "total_budget": total_budget,
        "active_count": active_count,
    }

    return templates.TemplateResponse(request, "pages/finance/dashboard.html", {
        "current_member": member,
        "current_user": user,
        "year": year,
        "cfg": cfg,
        "stats": stats,
        "tier_labels": TIER_LABELS,
        "is_admin": user.is_admin,
    })


# ══════════════════════════════════════════════════════════════════════════════
# BUDGET
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/budget", response_class=HTMLResponse)
async def budget_view(
    request: Request,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    year_id: Optional[int] = None,
):
    user, member = ctx
    # Liste des années
    r = await db.execute(select(MasonicYear).order_by(MasonicYear.start_date.desc()))
    years = r.scalars().all()

    selected_year = None
    if year_id:
        selected_year = await db.get(MasonicYear, year_id)
    if not selected_year:
        selected_year = next((y for y in years if y.is_current), years[0] if years else None)

    budget_lines: list[BudgetLine] = []
    cfg = None
    total_charges = Decimal("0")

    if selected_year:
        r2 = await db.execute(
            select(BudgetLine)
            .where(BudgetLine.masonic_year_id == selected_year.id)
            .order_by(BudgetLine.order_position, BudgetLine.id)
        )
        budget_lines = r2.scalars().all()
        total_charges = sum((Decimal(str(l.amount)) for l in budget_lines), Decimal("0"))
        cfg = await _get_or_create_config(db, selected_year.id)
        await db.refresh(cfg, ["tiers"])

    return templates.TemplateResponse(request, "pages/finance/budget.html", {
        "current_member": member,
        "current_user": user,
        "years": years,
        "selected_year": selected_year,
        "budget_lines": budget_lines,
        "budget_type_labels": BUDGET_TYPE_LABELS,
        "total_charges": total_charges,
        "cfg": cfg,
        "BudgetLineType": BudgetLineType,
        "tier_labels": TIER_LABELS,
        "tier_coefficients": TIER_COEFFICIENTS,
        "is_admin": user.is_admin,
    })


@router.post("/budget/add")
async def budget_add(
    request: Request,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    year_id: Annotated[int, Form()],
    label: Annotated[str, Form()],
    btype: Annotated[str, Form()],
    amount: Annotated[float, Form()],
    notes: Annotated[str, Form()] = "",
):
    # Compter les lignes existantes pour l'ordre
    r = await db.execute(
        select(func.count(BudgetLine.id)).where(BudgetLine.masonic_year_id == year_id)
    )
    pos = r.scalar_one() or 0

    db.add(BudgetLine(
        masonic_year_id=year_id,
        label=label.strip(),
        type=BudgetLineType(btype),
        amount=amount,
        order_position=pos,
        notes=notes.strip() or None,
    ))
    await db.commit()
    return RedirectResponse(url=f"/finance/budget?year_id={year_id}", status_code=303)


@router.post("/budget/{line_id}/delete")
async def budget_delete(
    line_id: int,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    line = await db.get(BudgetLine, line_id)
    if not line:
        raise HTTPException(404)
    year_id = line.masonic_year_id
    await db.delete(line)
    await db.commit()
    return RedirectResponse(url=f"/finance/budget?year_id={year_id}", status_code=303)


@router.post("/config/update")
async def config_update(
    request: Request,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    year_id: Annotated[int, Form()],
    national_capitation: Annotated[float, Form()],
    regional_capitation: Annotated[float, Form()],
    auto_t3: Annotated[str, Form()] = "off",
    manual_t3: Annotated[float, Form()] = 0.0,
):
    cfg = await _get_or_create_config(db, year_id)
    await db.refresh(cfg, ["tiers"])

    cfg.national_capitation_rate = national_capitation
    cfg.regional_capitation_rate = regional_capitation

    if auto_t3 == "on":
        cfg.reference_amount = float(await _compute_t3_from_budget(db, year_id, cfg))
    else:
        cfg.reference_amount = manual_t3

    _recompute_tiers(cfg)
    await db.commit()
    return RedirectResponse(url=f"/finance/budget?year_id={year_id}", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# COTISATIONS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/cotisations", response_class=HTMLResponse)
async def cotisations_view(
    request: Request,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    year_id: Optional[int] = None,
    status_filter: Optional[str] = None,
):
    user, member = ctx
    r = await db.execute(select(MasonicYear).order_by(MasonicYear.start_date.desc()))
    years = r.scalars().all()

    selected_year = None
    if year_id:
        selected_year = await db.get(MasonicYear, year_id)
    if not selected_year:
        selected_year = next((y for y in years if y.is_current), years[0] if years else None)

    members_list: list[Member] = []
    contributions_map: dict[int, MemberContribution] = {}
    tiers: list[ContributionTier] = []
    cfg = None

    if selected_year:
        # Membres actifs
        rm = await db.execute(
            select(Member)
            .where(Member.status == MemberStatus.ACTIVE)
            .order_by(Member.last_name, Member.first_name)
        )
        members_list = rm.scalars().all()

        # Config + tiers
        cfg = await _get_or_create_config(db, selected_year.id)
        await db.refresh(cfg, ["tiers"])
        tiers = sorted(cfg.tiers, key=lambda t: t.tier_number)

        # Contributions existantes
        rc = await db.execute(
            select(MemberContribution)
            .where(MemberContribution.masonic_year_id == selected_year.id)
            .options(
                selectinload(MemberContribution.payments),
                selectinload(MemberContribution.quitus),
            )
        )
        for c in rc.scalars().all():
            contributions_map[c.member_id] = c

    return templates.TemplateResponse(request, "pages/finance/cotisations.html", {
        "current_member": member,
        "current_user": user,
        "years": years,
        "selected_year": selected_year,
        "members_list": members_list,
        "contributions_map": contributions_map,
        "tiers": tiers,
        "cfg": cfg,
        "tier_labels": TIER_LABELS,
        "tier_coefficients": TIER_COEFFICIENTS,
        "ContributionStatus": ContributionStatus,
        "status_filter": status_filter,
        "method_labels": METHOD_LABELS,
        "PaymentMethod": PaymentMethod,
        "is_admin": user.is_admin,
        "now": datetime.now(),
    })


@router.post("/cotisations/assign")
async def assign_contribution(
    request: Request,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    year_id: Annotated[int, Form()],
    member_id: Annotated[int, Form()],
    tier_number: Annotated[int, Form()],
    due_date: Annotated[Optional[str], Form()] = None,
    notes: Annotated[str, Form()] = "",
):
    cfg = await _get_or_create_config(db, year_id)
    await db.refresh(cfg, ["tiers"])

    tier = next((t for t in cfg.tiers if t.tier_number == tier_number), None)
    if not tier:
        raise HTTPException(400, "Tranche invalide")

    capitation = float(cfg.national_capitation_rate) + float(cfg.regional_capitation_rate)
    base = float(tier.amount)
    total = base + capitation

    # Upsert
    r = await db.execute(
        select(MemberContribution).where(
            and_(MemberContribution.member_id == member_id,
                 MemberContribution.masonic_year_id == year_id)
        )
    )
    contrib = r.scalar_one_or_none()
    if contrib:
        contrib.tier_id = tier.id
        contrib.base_amount = base
        contrib.capitation_amount = capitation
        contrib.total_amount = total
        if notes.strip():
            contrib.notes = notes.strip()
    else:
        contrib = MemberContribution(
            member_id=member_id,
            masonic_year_id=year_id,
            tier_id=tier.id,
            base_amount=base,
            capitation_amount=capitation,
            total_amount=total,
            due_date=date.fromisoformat(due_date) if due_date else None,
            status=ContributionStatus.PENDING,
            notes=notes.strip() or None,
        )
        db.add(contrib)

    await db.commit()
    return RedirectResponse(url=f"/finance/cotisations?year_id={year_id}", status_code=303)


@router.post("/cotisations/assign-all")
async def assign_all_t3(
    request: Request,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    year_id: Annotated[int, Form()],
):
    """Assigne T3 à tous les membres actifs sans cotisation pour cette année."""
    cfg = await _get_or_create_config(db, year_id)
    await db.refresh(cfg, ["tiers"])

    tier3 = next((t for t in cfg.tiers if t.tier_number == 3), None)
    if not tier3:
        raise HTTPException(400)

    rm = await db.execute(select(Member).where(Member.status == MemberStatus.ACTIVE))
    all_active = rm.scalars().all()

    rc = await db.execute(
        select(MemberContribution.member_id)
        .where(MemberContribution.masonic_year_id == year_id)
    )
    already = {row[0] for row in rc.all()}

    capitation = float(cfg.national_capitation_rate) + float(cfg.regional_capitation_rate)
    base = float(tier3.amount)
    total = base + capitation

    for m in all_active:
        if m.id not in already:
            db.add(MemberContribution(
                member_id=m.id,
                masonic_year_id=year_id,
                tier_id=tier3.id,
                base_amount=base,
                capitation_amount=capitation,
                total_amount=total,
                status=ContributionStatus.PENDING,
            ))
    await db.commit()
    return RedirectResponse(url=f"/finance/cotisations?year_id={year_id}", status_code=303)


@router.post("/cotisations/{contribution_id}/pay")
async def record_payment(
    contribution_id: int,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    amount: Annotated[float, Form()],
    method: Annotated[str, Form()],
    payment_date: Annotated[str, Form()],
    reference: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
):
    contrib = await db.get(MemberContribution, contribution_id,
                           options=[selectinload(MemberContribution.payments)])
    if not contrib:
        raise HTTPException(404)

    _, member = ctx
    db.add(Payment(
        member_contribution_id=contribution_id,
        amount=amount,
        payment_date=date.fromisoformat(payment_date),
        method=PaymentMethod(method),
        reference=reference.strip() or None,
        notes=notes.strip() or None,
        recorded_by_id=member.id,
    ))

    # Recalculer le statut
    total_paid = sum(float(p.amount) for p in contrib.payments) + amount
    if total_paid >= float(contrib.total_amount):
        contrib.status = ContributionStatus.PAID
    elif total_paid > 0:
        contrib.status = ContributionStatus.PARTIAL

    await db.commit()
    return RedirectResponse(
        url=f"/finance/cotisations?year_id={contrib.masonic_year_id}", status_code=303
    )


@router.post("/cotisations/{contribution_id}/exempt")
async def set_exempt(
    contribution_id: int,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    notes: Annotated[str, Form()] = "",
):
    contrib = await db.get(MemberContribution, contribution_id)
    if not contrib:
        raise HTTPException(404)
    contrib.status = ContributionStatus.EXEMPT
    if notes.strip():
        contrib.notes = notes.strip()
    await db.commit()
    return RedirectResponse(
        url=f"/finance/cotisations?year_id={contrib.masonic_year_id}", status_code=303
    )
