"""Router Finance — Cotisations, budget, paiements, comptabilité"""
import csv
import io
import os
import shutil
import uuid
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, Request, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

UPLOAD_DIR = "app/static/uploads/transactions"
os.makedirs(UPLOAD_DIR, exist_ok=True)

from app.database import get_db
from app.dependencies import require_auth, require_admin, require_finance_manager, can_manage_finance
from app.models.finance import (
    BudgetLine, BudgetLineType, ContributionConfig, ContributionTier,
    MemberContribution, ContributionStatus, Payment, PaymentMethod, Quitus,
    BudgetCategory, Transaction, TransactionType,
)
from app.models.identity import Member, MemberStatus, MembershipType, User
from app.models.lodge import MasonicYear

router = APIRouter(prefix="/finance", tags=["finance"])
templates = Jinja2Templates(directory="app/templates")

def _capitation(full_capitation: float, member: Member) -> float:
    """Retourne la capitation applicable : 0 pour les affiliés, pleine pour les membres en appartenance."""
    if member.membership_type == MembershipType.AFFILIATION:
        return 0.0
    return full_capitation


async def _affilié_ids(db: AsyncSession) -> set[int]:
    """Retourne l'ensemble des IDs de membres affiliés (optimisation pour traitements en masse)."""
    r = await db.execute(
        select(Member.id).where(Member.membership_type == MembershipType.AFFILIATION)
    )
    return {row[0] for row in r.all()}


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
    search: Optional[str] = None,
    status_filter: Optional[str] = None,  # retard | ajour | all
):
    user, member = ctx
    year = await _get_current_year(db)
    if not year:
        return templates.TemplateResponse(request, "pages/finance/dashboard.html", {
            "current_member": member, "current_user": user,
            "year": None, "stats": None, "member_states": [],
        })

    cfg = await _get_or_create_config(db, year.id)
    await db.refresh(cfg, ["tiers"])

    # ── Membres actifs (hors super-admins) ───────────────────────────────────
    _admin_ids = select(User.member_id).where(User.is_admin == True, User.member_id.isnot(None))
    rm = await db.execute(
        select(Member)
        .where(Member.status == MemberStatus.ACTIVE, Member.id.not_in(_admin_ids))
        .order_by(Member.last_name, Member.first_name)
    )
    all_members = rm.scalars().all()

    # ── Contributions de l'année ──────────────────────────────────────────────
    rc = await db.execute(
        select(MemberContribution)
        .where(MemberContribution.masonic_year_id == year.id)
        .options(selectinload(MemberContribution.payments),
                 selectinload(MemberContribution.quitus))
    )
    contributions_map: dict[int, MemberContribution] = {c.member_id: c for c in rc.scalars().all()}

    # ── Tiers map ────────────────────────────────────────────────────────────
    tiers_map: dict[int, ContributionTier] = {t.id: t for t in cfg.tiers}

    # ── État par membre ───────────────────────────────────────────────────────
    member_states = []
    for m in all_members:
        c = contributions_map.get(m.id)
        if c:
            due = float(c.total_amount)
            paid = float(c.amount_paid)
            remaining = float(c.amount_remaining)
            advance = max(0.0, -remaining)
            tier = tiers_map.get(c.tier_id)
            is_late = remaining > 0.005 and c.status not in (
                ContributionStatus.PAID, ContributionStatus.EXEMPT
            )
            is_ok = c.status in (ContributionStatus.PAID, ContributionStatus.EXEMPT) or remaining <= 0.005
        else:
            due = paid = remaining = advance = 0.0
            tier = is_late = None
            is_ok = False

        # Filtre texte
        if search:
            s = search.lower()
            if s not in m.last_name.lower() and s not in m.first_name.lower():
                continue

        # Filtre statut
        if status_filter == "retard" and not is_late:
            continue
        if status_filter == "ajour" and not is_ok:
            continue

        member_states.append({
            "member": m,
            "contrib": c,
            "tier": tier,
            "due": due,
            "paid": paid,
            "remaining": remaining,
            "advance": advance,
            "is_late": is_late,
            "is_ok": is_ok,
        })

    # ── Stats globales (sur les membres non-admin uniquement) ────────────────
    _member_ids = {m.id for m in all_members}
    all_contribs = [c for c in contributions_map.values() if c.member_id in _member_ids]
    total_due   = sum(float(c.total_amount) for c in all_contribs)
    total_paid  = sum(float(c.amount_paid)  for c in all_contribs)
    total_remaining = max(0.0, total_due - total_paid)
    total_advances  = sum(
        max(0.0, float(c.amount_paid) - float(c.total_amount))
        for c in all_contribs
    )
    late_count  = sum(1 for ms in member_states if ms["is_late"])
    paid_count  = sum(1 for c in all_contribs if c.status in (
        ContributionStatus.PAID, ContributionStatus.EXEMPT))
    unassigned  = len(all_members) - len(all_contribs)

    # Budget total
    rb = await db.execute(
        select(func.sum(BudgetLine.amount)).where(BudgetLine.masonic_year_id == year.id)
    )
    total_budget = float(rb.scalar_one() or 0)

    # Trésorerie théorique = initiale + encaissé + autres recettes - dépenses
    rt_income = await db.execute(
        select(func.sum(Transaction.amount))
        .where(Transaction.masonic_year_id == year.id, Transaction.type == TransactionType.INCOME)
    )
    rt_expense = await db.execute(
        select(func.sum(Transaction.amount))
        .where(Transaction.masonic_year_id == year.id, Transaction.type == TransactionType.EXPENSE)
    )
    other_income  = float(rt_income.scalar_one() or 0)
    total_expense = float(rt_expense.scalar_one() or 0)
    initial_treasury = float(cfg.initial_treasury or 0)
    theoretical_treasury = initial_treasury + total_paid + other_income - total_expense

    stats = {
        "total_due":      total_due,
        "total_paid":     total_paid,
        "total_remaining": total_remaining,
        "total_advances": total_advances,
        "late_count":     late_count,
        "paid_count":     paid_count,
        "unassigned":     unassigned,
        "total_budget":   total_budget,
        "active_count":   len(all_members),
        "initial_treasury": initial_treasury,
        "other_income":   other_income,
        "total_expense":  total_expense,
        "theoretical_treasury": theoretical_treasury,
    }

    # Top 10 retardataires (non filtré)
    all_late = [
        {"member": m, "remaining": float(c.amount_remaining)}
        for m, c in [(m, contributions_map[m.id]) for m in all_members if m.id in contributions_map]
        if float(contributions_map[m.id].amount_remaining) > 0.005
        and contributions_map[m.id].status not in (ContributionStatus.PAID, ContributionStatus.EXEMPT)
    ]
    all_late.sort(key=lambda x: -x["remaining"])
    top_late = all_late[:10]

    return templates.TemplateResponse(request, "pages/finance/dashboard.html", {
        "current_member": member,
        "current_user": user,
        "year": year,
        "cfg": cfg,
        "stats": stats,
        "tier_labels": TIER_LABELS,
        "is_admin": user.is_admin,
        "member_states": member_states,
        "top_late": top_late,
        "search": search or "",
        "status_filter": status_filter or "all",
        "ContributionStatus": ContributionStatus,
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

    can_edit = user.is_admin or can_manage_finance(member)
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
        "can_edit": can_edit,
    })


@router.post("/budget/add")
async def budget_add(
    request: Request,
    ctx: Annotated[object, Depends(require_finance_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
    year_id: Annotated[int, Form()],
    label: Annotated[str, Form()],
    btype: Annotated[str, Form()],
    amount: Annotated[float, Form()],
    category_label: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
):
    r = await db.execute(
        select(func.count(BudgetLine.id)).where(BudgetLine.masonic_year_id == year_id)
    )
    pos = r.scalar_one() or 0

    db.add(BudgetLine(
        masonic_year_id=year_id,
        label=label.strip(),
        type=BudgetLineType(btype),
        category_label=category_label.strip() or None,
        amount=amount,
        order_position=pos,
        notes=notes.strip() or None,
    ))
    await db.commit()
    return RedirectResponse(url=f"/finance/budget?year_id={year_id}", status_code=303)


@router.post("/budget/import-csv")
async def budget_import_csv(
    request: Request,
    ctx: Annotated[object, Depends(require_finance_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
    year_id: Annotated[int, Form()],
    csvfile: UploadFile = File(...),
):
    """Import CSV : colonnes Catégorie,Libellé,Montant (séparateur ; ou ,)."""
    content = await csvfile.read()
    text = content.decode("utf-8-sig")  # gère le BOM Excel
    dialect = "excel" if "," in text.split("\n")[0] else "excel-tab"
    # Détecter le séparateur ; vs ,
    first_line = text.split("\n")[0]
    sep = ";" if first_line.count(";") >= first_line.count(",") else ","

    reader = csv.DictReader(io.StringIO(text), delimiter=sep)

    # Compter les lignes existantes pour l'ordre
    r = await db.execute(
        select(func.count(BudgetLine.id)).where(BudgetLine.masonic_year_id == year_id)
    )
    pos = r.scalar_one() or 0

    for row in reader:
        # Accepte différents noms de colonnes
        cat = (row.get("Catégorie") or row.get("Categorie") or row.get("categorie") or "").strip()
        lbl = (row.get("Libellé") or row.get("Libelle") or row.get("libelle") or row.get("label") or "").strip()
        amt_raw = (row.get("Montant") or row.get("montant") or "0").strip().replace(",", ".").replace(" ", "").replace("€", "")
        if not lbl:
            continue
        try:
            amt = float(amt_raw)
        except ValueError:
            amt = 0.0

        db.add(BudgetLine(
            masonic_year_id=year_id,
            label=lbl,
            type=BudgetLineType.CHARGE_FIXE,
            category_label=cat or None,
            amount=amt,
            order_position=pos,
        ))
        pos += 1

    await db.commit()
    return RedirectResponse(url=f"/finance/budget?year_id={year_id}", status_code=303)


@router.post("/budget/{line_id}/delete")
async def budget_delete(
    line_id: int,
    ctx: Annotated[object, Depends(require_finance_manager)],
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
    ctx: Annotated[object, Depends(require_finance_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
    year_id: Annotated[int, Form()],
    national_capitation: Annotated[float, Form()],
    regional_capitation: Annotated[float, Form()],
    initial_treasury: Annotated[float, Form()] = 0.0,
    auto_t3: Annotated[str, Form()] = "off",
    manual_t3: Annotated[float, Form()] = 0.0,
):
    cfg = await _get_or_create_config(db, year_id)
    await db.refresh(cfg, ["tiers"])

    cfg.national_capitation_rate = national_capitation
    cfg.regional_capitation_rate = regional_capitation
    cfg.initial_treasury = initial_treasury

    if auto_t3 == "on":
        cfg.reference_amount = float(await _compute_t3_from_budget(db, year_id, cfg))
    else:
        cfg.reference_amount = manual_t3

    _recompute_tiers(cfg)

    # ── Resynchroniser toutes les cotisations ouvertes (PENDING / PARTIAL) ──
    # Quand le barème change, les montants dus doivent être recalculés pour que
    # Σ(parts membres) == budget total.
    await db.flush()  # s'assurer que les nouveaux tier.amount sont visibles

    tier_by_id = {t.id: t for t in cfg.tiers}
    full_capitation = float(cfg.national_capitation_rate) + float(cfg.regional_capitation_rate)
    affiliés = await _affilié_ids(db)

    open_r = await db.execute(
        select(MemberContribution).where(
            MemberContribution.masonic_year_id == year_id,
            MemberContribution.status.in_([ContributionStatus.PENDING, ContributionStatus.PARTIAL]),
        )
    )
    for contrib in open_r.scalars().all():
        tier = tier_by_id.get(contrib.tier_id)
        if tier:
            new_base = float(tier.amount)
            cap = 0.0 if contrib.member_id in affiliés else full_capitation
            contrib.base_amount = new_base
            contrib.capitation_amount = cap
            contrib.total_amount = new_base + cap

    await db.commit()
    return RedirectResponse(url=f"/finance/budget?year_id={year_id}", status_code=303)


# ── Export CSV retardataires ──────────────────────────────────────────────────

@router.get("/cotisations/export-csv")
async def export_retardataires_csv(
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    year_id: Optional[int] = None,
):
    year = await _get_current_year(db) if not year_id else await db.get(MasonicYear, year_id)
    if not year:
        raise HTTPException(404)

    rm = await db.execute(
        select(Member).where(Member.status == MemberStatus.ACTIVE)
        .order_by(Member.last_name, Member.first_name)
    )
    members = rm.scalars().all()

    rc = await db.execute(
        select(MemberContribution)
        .where(MemberContribution.masonic_year_id == year.id)
        .options(selectinload(MemberContribution.payments))
    )
    cmap = {c.member_id: c for c in rc.scalars().all()}

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Nom", "Prénom", "Dû (€)", "Payé (€)", "Reste (€)", "Statut"])
    for m in members:
        c = cmap.get(m.id)
        if c:
            due = float(c.total_amount)
            paid = float(c.amount_paid)
            remaining = float(c.amount_remaining)
            status = c.status.value
        else:
            due = paid = remaining = 0.0
            status = "NON ASSIGNÉ"
        writer.writerow([m.last_name, m.first_name,
                         f"{due:.2f}", f"{paid:.2f}", f"{remaining:.2f}", status])

    output.seek(0)
    filename = f"cotisations_{year.label.replace(' ', '_')}.csv"
    return StreamingResponse(
        io.BytesIO(output.read().encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


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
        # Membres actifs (hors super-admins)
        admin_ids = select(User.member_id).where(
            User.is_admin == True, User.member_id.isnot(None)
        )
        rm = await db.execute(
            select(Member)
            .where(Member.status == MemberStatus.ACTIVE, Member.id.not_in(admin_ids))
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


async def _close_appel_and_assign_defaults(
    db: AsyncSession,
    year_id: int,
    cfg: ContributionConfig,
) -> int:
    """
    Appelé quand on clôt l'appel à tranche.

    Pour chaque membre actif sans cotisation pour cette année :
      - On cherche sa cotisation de l'année précédente → on reconduit son tier_number
      - Sinon on affecte T3 par défaut
    Puis on recalcule T3 depuis le budget avec la distribution complète,
    et on resynchronise toutes les cotisations PENDING/PARTIAL.

    Retourne le nombre de membres auto-affectés.
    """
    await db.refresh(cfg, ["tiers"])

    # ── 1. Récupérer l'année courante pour trouver la précédente ────────────
    r_yr = await db.execute(select(MasonicYear).where(MasonicYear.id == year_id))
    current_year = r_yr.scalar_one_or_none()

    prev_year = None
    if current_year:
        r_prev = await db.execute(
            select(MasonicYear)
            .where(MasonicYear.start_date < current_year.start_date)
            .order_by(MasonicYear.start_date.desc())
            .limit(1)
        )
        prev_year = r_prev.scalar_one_or_none()

    # ── 2. Membres actifs ────────────────────────────────────────────────────
    r_members = await db.execute(
        select(Member).where(Member.status == MemberStatus.ACTIVE)
    )
    active_members = r_members.scalars().all()

    # ── 3. Cotisations existantes pour cette année ───────────────────────────
    r_existing = await db.execute(
        select(MemberContribution.member_id)
        .where(MemberContribution.masonic_year_id == year_id)
    )
    already_assigned = {row[0] for row in r_existing.all()}

    # ── 4. Tiers de l'année précédente (par member_id → tier_number) ────────
    prev_tier_by_member: dict[int, int] = {}
    if prev_year:
        r_prev_contribs = await db.execute(
            select(MemberContribution.member_id, ContributionTier.tier_number)
            .join(ContributionTier, ContributionTier.id == MemberContribution.tier_id)
            .where(MemberContribution.masonic_year_id == prev_year.id)
        )
        for member_id, tier_num in r_prev_contribs.all():
            prev_tier_by_member[member_id] = tier_num

    # ── 5. Map tier_number → tier object (année courante) ───────────────────
    tier_by_number = {t.tier_number: t for t in cfg.tiers}
    full_capitation = float(cfg.national_capitation_rate) + float(cfg.regional_capitation_rate)
    affiliés = await _affilié_ids(db)

    # ── 6. Affecter les membres sans cotisation ──────────────────────────────
    auto_assigned = 0
    for m in active_members:
        if m.id in already_assigned:
            continue

        # Reconduire l'année précédente, sinon T3 par défaut
        tier_num = prev_tier_by_member.get(m.id, 3)
        tier = tier_by_number.get(tier_num) or tier_by_number.get(3)
        if not tier:
            continue  # pas de tiers configurés — ne devrait pas arriver

        note_parts = []
        if m.id in prev_tier_by_member:
            note_parts.append(
                f"Tranche reconduite automatiquement depuis l'année précédente "
                f"({prev_year.label if prev_year else '?'}) — T{tier_num}"
            )
        else:
            note_parts.append(
                "Tranche T3 affectée par défaut (aucune réponse à l'appel, "
                "pas de cotisation l'année précédente)"
            )

        cap = 0.0 if m.id in affiliés else full_capitation
        new_contrib = MemberContribution(
            member_id=m.id,
            masonic_year_id=year_id,
            tier_id=tier.id,
            base_amount=float(tier.amount),
            capitation_amount=cap,
            total_amount=float(tier.amount) + cap,
            status=ContributionStatus.PENDING,
            notes="\n".join(note_parts),
        )
        db.add(new_contrib)
        auto_assigned += 1

    await db.flush()

    # ── 7. Recalculer T3 avec la distribution COMPLÈTE ──────────────────────
    new_t3 = await _compute_t3_from_budget(db, year_id, cfg)
    cfg.reference_amount = new_t3
    _recompute_tiers(cfg)
    await db.flush()
    await db.refresh(cfg, ["tiers"])

    # ── 8. Resynchroniser toutes les cotisations PENDING / PARTIAL ───────────
    tier_by_id = {t.id: t for t in cfg.tiers}
    new_full_cap = float(cfg.national_capitation_rate) + float(cfg.regional_capitation_rate)

    r_open = await db.execute(
        select(MemberContribution).where(
            MemberContribution.masonic_year_id == year_id,
            MemberContribution.status.in_([ContributionStatus.PENDING, ContributionStatus.PARTIAL]),
        )
    )
    for contrib in r_open.scalars().all():
        tier = tier_by_id.get(contrib.tier_id)
        if tier:
            cap = 0.0 if contrib.member_id in affiliés else new_full_cap
            contrib.base_amount = float(tier.amount)
            contrib.capitation_amount = cap
            contrib.total_amount = float(tier.amount) + cap

    return auto_assigned


@router.post("/cotisations/toggle-appel")
async def toggle_appel(
    request: Request,
    ctx: Annotated[object, Depends(require_finance_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
    year_id: Annotated[int, Form()],
):
    """Ouvre ou ferme la fenêtre d'appel à tranche pour l'année donnée.

    À la fermeture : affecte automatiquement les membres sans réponse
    (tranche de l'année précédente ou T3), recalcule T3 sur la distribution
    complète, et resynchronise toutes les cotisations ouvertes.
    """
    cfg = await _get_or_create_config(db, year_id)
    was_open = bool(cfg.tier_selection_open)
    cfg.tier_selection_open = not was_open

    if was_open:
        # Clôture → affecter les non-répondants et finaliser T3
        await _close_appel_and_assign_defaults(db, year_id, cfg)

    await db.commit()
    return RedirectResponse(url=f"/finance/budget?year_id={year_id}", status_code=303)


@router.post("/cotisations/resync")
async def resync_contributions(
    request: Request,
    ctx: Annotated[object, Depends(require_finance_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
    year_id: Annotated[int, Form()],
):
    """Resynchronise les montants des cotisations ouvertes sur le barème actuel.
    Seules les cotisations PENDING et PARTIAL sont mises à jour.
    Les cotisations PAID et EXEMPT ne sont pas touchées.
    """
    cfg = await _get_or_create_config(db, year_id)
    await db.refresh(cfg, ["tiers"])

    tier_by_id = {t.id: t for t in cfg.tiers}
    full_capitation = float(cfg.national_capitation_rate) + float(cfg.regional_capitation_rate)
    affiliés = await _affilié_ids(db)

    open_r = await db.execute(
        select(MemberContribution).where(
            MemberContribution.masonic_year_id == year_id,
            MemberContribution.status.in_([ContributionStatus.PENDING, ContributionStatus.PARTIAL]),
        )
    )
    updated = 0
    for contrib in open_r.scalars().all():
        tier = tier_by_id.get(contrib.tier_id)
        if tier:
            cap = 0.0 if contrib.member_id in affiliés else full_capitation
            contrib.base_amount = float(tier.amount)
            contrib.capitation_amount = cap
            contrib.total_amount = float(tier.amount) + cap
            updated += 1

    await db.commit()
    return RedirectResponse(url=f"/finance/cotisations?year_id={year_id}", status_code=303)


@router.post("/cotisations/assign")
async def assign_contribution(
    request: Request,
    ctx: Annotated[object, Depends(require_finance_manager)],
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

    full_capitation = float(cfg.national_capitation_rate) + float(cfg.regional_capitation_rate)
    member_obj = await db.get(Member, member_id)
    cap = 0.0 if (member_obj and member_obj.membership_type == MembershipType.AFFILIATION) else full_capitation
    base = float(tier.amount)
    total = base + cap

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
        contrib.capitation_amount = cap
        contrib.total_amount = total
        if notes.strip():
            contrib.notes = notes.strip()
    else:
        contrib = MemberContribution(
            member_id=member_id,
            masonic_year_id=year_id,
            tier_id=tier.id,
            base_amount=base,
            capitation_amount=cap,
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
    ctx: Annotated[object, Depends(require_finance_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
    year_id: Annotated[int, Form()],
):
    """Assigne T3 à tous les membres actifs sans cotisation pour cette année."""
    cfg = await _get_or_create_config(db, year_id)
    await db.refresh(cfg, ["tiers"])

    tier3 = next((t for t in cfg.tiers if t.tier_number == 3), None)
    if not tier3:
        raise HTTPException(400)

    _adm = select(User.member_id).where(User.is_admin == True, User.member_id.isnot(None))
    rm = await db.execute(
        select(Member).where(Member.status == MemberStatus.ACTIVE, Member.id.not_in(_adm))
    )
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
    ctx: Annotated[object, Depends(require_finance_manager)],
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
    ctx: Annotated[object, Depends(require_finance_manager)],
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


@router.post("/cotisations/{contribution_id}/delete")
async def delete_contribution(
    contribution_id: int,
    ctx: Annotated[object, Depends(require_finance_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    r = await db.execute(
        select(MemberContribution)
        .options(selectinload(MemberContribution.payments), selectinload(MemberContribution.quitus))
        .where(MemberContribution.id == contribution_id)
    )
    contrib = r.scalar_one_or_none()
    if not contrib:
        raise HTTPException(404)
    year_id = contrib.masonic_year_id

    # Supprimer quitus, paiements, puis cotisation
    if contrib.quitus:
        await db.delete(contrib.quitus)
    for p in contrib.payments:
        await db.delete(p)
    await db.flush()
    await db.delete(contrib)
    await db.commit()
    return RedirectResponse(url=f"/finance/cotisations?year_id={year_id}", status_code=303)


@router.post("/cotisations/{contribution_id}/quitus")
async def issue_quitus(
    contribution_id: int,
    ctx: Annotated[object, Depends(require_finance_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
    valid_until: Annotated[Optional[str], Form()] = None,
    notes: Annotated[str, Form()] = "",
):
    """Émet un quitus pour une cotisation soldée ou exemptée."""
    _, member = ctx
    contrib = await db.get(MemberContribution, contribution_id,
                           options=[selectinload(MemberContribution.quitus)])
    if not contrib:
        raise HTTPException(404)
    if contrib.status not in (ContributionStatus.PAID, ContributionStatus.EXEMPT):
        raise HTTPException(400, "Quitus réservé aux cotisations soldées ou exemptées")

    # Éviter le doublon
    if not contrib.quitus:
        db.add(Quitus(
            member_id=contrib.member_id,
            masonic_year_id=contrib.masonic_year_id,
            contribution_id=contribution_id,
            issued_by_id=member.id,
            valid_until=date.fromisoformat(valid_until) if valid_until else None,
            notes=notes.strip() or None,
        ))
        await db.commit()
    return RedirectResponse(
        url=f"/finance/cotisations?year_id={contrib.masonic_year_id}", status_code=303
    )


# ══════════════════════════════════════════════════════════════════════════════
# SÉLECTION DE TRANCHE PAR LE MEMBRE
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/membres/{member_id}", response_class=HTMLResponse)
async def membre_finance_detail(
    member_id: int,
    request: Request,
    ctx: Annotated[object, Depends(require_finance_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, current_member = ctx

    target = await db.get(Member, member_id)
    if not target:
        raise HTTPException(404)

    # Toutes les années maçonniques
    r_years = await db.execute(select(MasonicYear).order_by(MasonicYear.start_date.desc()))
    years = r_years.scalars().all()

    # Toutes les contributions du membre
    rc = await db.execute(
        select(MemberContribution)
        .where(MemberContribution.member_id == member_id)
        .options(
            selectinload(MemberContribution.payments),
            selectinload(MemberContribution.quitus),
        )
        .order_by(MemberContribution.masonic_year_id.desc())
    )
    contributions = rc.scalars().all()

    # Index années et tiers
    year_map = {y.id: y for y in years}
    all_tiers_r = await db.execute(select(ContributionTier))
    tier_map = {t.id: t for t in all_tiers_r.scalars().all()}

    # Configs par année (pour détail capitation)
    all_cfg_r = await db.execute(select(ContributionConfig))
    cfg_map = {c.masonic_year_id: c for c in all_cfg_r.scalars().all()}

    return templates.TemplateResponse(request, "pages/finance/membre_detail.html", {
        "current_member": current_member,
        "current_user": user,
        "target": target,
        "contributions": contributions,
        "year_map": year_map,
        "tier_map": tier_map,
        "cfg_map": cfg_map,
        "method_labels": METHOD_LABELS,
        "PaymentMethod": PaymentMethod,
        "ContributionStatus": ContributionStatus,
        "is_admin": user.is_admin,
        "now": datetime.now(),
    })


@router.get("/cotisations/choisir", response_class=HTMLResponse)
async def choisir_tranche_form(
    request: Request,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    year = await _get_current_year(db)
    if not year:
        return RedirectResponse(url="/finance/", status_code=302)

    cfg = await _get_or_create_config(db, year.id)
    await db.refresh(cfg, ["tiers"])
    tiers = sorted(cfg.tiers, key=lambda t: t.tier_number)

    # Cotisation actuelle
    cr = await db.execute(
        select(MemberContribution)
        .options(selectinload(MemberContribution.payments))
        .where(
            MemberContribution.member_id == member.id,
            MemberContribution.masonic_year_id == year.id,
        )
    )
    my_contrib = cr.scalar_one_or_none()
    my_tier = None
    if my_contrib:
        my_tier = next((t for t in tiers if t.id == my_contrib.tier_id), None)

    capitation = float(cfg.national_capitation_rate) + float(cfg.regional_capitation_rate)

    return templates.TemplateResponse(request, "pages/finance/choisir_tranche.html", {
        "current_member": member,
        "current_user": user,
        "year": year,
        "cfg": cfg,
        "tiers": tiers,
        "my_contrib": my_contrib,
        "my_tier": my_tier,
        "capitation": capitation,
        "tier_labels": TIER_LABELS,
        "appel_open": bool(cfg.tier_selection_open),
    })


@router.post("/cotisations/choisir")
async def choisir_tranche_save(
    request: Request,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    tier_number: Annotated[int, Form()],
):
    user, member = ctx
    year = await _get_current_year(db)
    if not year:
        raise HTTPException(400, "Aucune année en cours")

    cfg = await _get_or_create_config(db, year.id)
    await db.refresh(cfg, ["tiers"])

    tier = next((t for t in cfg.tiers if t.tier_number == tier_number), None)
    if not tier:
        raise HTTPException(400, "Tranche invalide")

    # Vérifier si une contribution existe déjà et n'est pas encore payée
    cr = await db.execute(
        select(MemberContribution).where(
            MemberContribution.member_id == member.id,
            MemberContribution.masonic_year_id == year.id,
        )
    )
    existing = cr.scalar_one_or_none()

    capitation = float(cfg.national_capitation_rate) + float(cfg.regional_capitation_rate)
    base = float(tier.amount)
    total = base + capitation

    # Vérifier que l'appel est ouvert
    if not bool(cfg.tier_selection_open):
        return RedirectResponse(url="/finance/cotisations/choisir", status_code=302)

    if existing:
        # La tranche est figée dès qu'elle a été choisie — seul l'admin peut modifier via /cotisations/assign
        return RedirectResponse(url="/finance/cotisations/choisir", status_code=302)
    else:
        db.add(MemberContribution(
            member_id=member.id,
            masonic_year_id=year.id,
            tier_id=tier.id,
            base_amount=base,
            capitation_amount=capitation,
            total_amount=total,
            status=ContributionStatus.PENDING,
        ))

    await db.commit()
    return RedirectResponse(url="/finance/cotisations/choisir", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# MOUVEMENTS COMPTABLES (TRANSACTIONS)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/transactions", response_class=HTMLResponse)
async def transactions_view(
    request: Request,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    year_id: Optional[int] = None,
    txn_type: Optional[str] = None,
):
    user, member = ctx
    r = await db.execute(select(MasonicYear).order_by(MasonicYear.start_date.desc()))
    years = r.scalars().all()

    selected_year = None
    if year_id:
        selected_year = await db.get(MasonicYear, year_id)
    if not selected_year:
        selected_year = next((y for y in years if y.is_current), years[0] if years else None)

    transactions = []
    categories = []
    total_income = Decimal("0")
    total_expense = Decimal("0")

    if selected_year:
        # ── Auto-synchronisation : catégories budget → BudgetCategory ───────
        # Les category_label des lignes budgétaires deviennent automatiquement
        # des catégories de dépenses disponibles dans les mouvements.
        bl_r = await db.execute(
            select(BudgetLine.category_label)
            .where(
                BudgetLine.masonic_year_id == selected_year.id,
                BudgetLine.category_label.is_not(None),
            )
            .distinct()
        )
        budget_cat_labels = {row[0] for row in bl_r.all()}

        # Catégories existantes
        existing_r = await db.execute(
            select(BudgetCategory).where(BudgetCategory.masonic_year_id == selected_year.id)
        )
        existing_cats = existing_r.scalars().all()
        existing_names = {c.name for c in existing_cats}

        # Créer les manquantes (type EXPENSE par défaut pour le budget = charges)
        for label in sorted(budget_cat_labels):
            if label not in existing_names:
                db.add(BudgetCategory(
                    masonic_year_id=selected_year.id,
                    name=label,
                    type=TransactionType.EXPENSE,
                    planned_amount=0,
                ))
        if budget_cat_labels - existing_names:
            await db.flush()

        # Recharger les catégories après sync
        rc = await db.execute(
            select(BudgetCategory)
            .where(BudgetCategory.masonic_year_id == selected_year.id)
            .order_by(BudgetCategory.type, BudgetCategory.name)
        )
        categories = rc.scalars().all()

        # Transactions (filtrées si nécessaire)
        q = (
            select(Transaction)
            .options(selectinload(Transaction.category))
            .where(Transaction.masonic_year_id == selected_year.id)
        )
        if txn_type in ("INCOME", "EXPENSE"):
            q = q.where(Transaction.type == TransactionType(txn_type))
        q = q.order_by(Transaction.date.desc(), Transaction.id.desc())

        rt = await db.execute(q)
        transactions = rt.scalars().all()

        for t in transactions:
            amt = Decimal(str(t.amount))
            if t.type == TransactionType.INCOME:
                total_income += amt
            else:
                total_expense += amt

    return templates.TemplateResponse(request, "pages/finance/transactions.html", {
        "current_member": member,
        "current_user": user,
        "years": years,
        "selected_year": selected_year,
        "transactions": transactions,
        "categories": categories,
        "total_income": total_income,
        "total_expense": total_expense,
        "solde": total_income - total_expense,
        "TransactionType": TransactionType,
        "filter_type": txn_type,
        "is_admin": user.is_admin,
        "today": date.today(),
    })


@router.post("/transactions/add")
async def add_transaction(
    request: Request,
    ctx: Annotated[object, Depends(require_finance_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
    year_id: Annotated[int, Form()],
    txn_date: Annotated[str, Form()],
    label: Annotated[str, Form()],
    amount: Annotated[float, Form()],
    txn_type: Annotated[str, Form()],
    category_id: Annotated[Optional[int], Form()] = None,
    new_category: Annotated[str, Form()] = "",
    document: Optional[UploadFile] = File(None),
):
    _, rec_member = ctx

    # Créer la catégorie à la volée si l'utilisateur en saisit une nouvelle
    cat_id = category_id or None
    if not cat_id and new_category.strip():
        cat = BudgetCategory(
            masonic_year_id=year_id,
            name=new_category.strip(),
            type=TransactionType(txn_type),
            planned_amount=0,
        )
        db.add(cat)
        await db.flush()
        cat_id = cat.id

    txn = Transaction(
        masonic_year_id=year_id,
        date=date.fromisoformat(txn_date),
        label=label.strip(),
        amount=amount,
        type=TransactionType(txn_type),
        category_id=cat_id,
        created_by_id=rec_member.id,
    )
    db.add(txn)
    await db.flush()  # Pour obtenir l'id avant de sauvegarder le fichier

    # Sauvegarder le justificatif si fourni
    if document and document.filename:
        allowed = {"application/pdf", "image/jpeg", "image/png", "image/webp"}
        if document.content_type in allowed:
            ext = os.path.splitext(document.filename)[1] or ".pdf"
            filename = f"{uuid.uuid4().hex}{ext}"
            filepath = os.path.join(UPLOAD_DIR, filename)
            with open(filepath, "wb") as f:
                shutil.copyfileobj(document.file, f)
            txn.attachment_url = f"/static/uploads/transactions/{filename}"

    await db.commit()
    return RedirectResponse(url=f"/finance/transactions?year_id={year_id}", status_code=303)


@router.post("/transactions/{txn_id}/delete")
async def delete_transaction(
    txn_id: int,
    ctx: Annotated[object, Depends(require_finance_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    txn = await db.get(Transaction, txn_id)
    if not txn:
        raise HTTPException(404)
    year_id = txn.masonic_year_id
    # Supprimer le fichier joint si présent
    if txn.attachment_url:
        filepath = txn.attachment_url.lstrip("/")
        if os.path.exists(filepath):
            os.remove(filepath)
    await db.delete(txn)
    await db.commit()
    return RedirectResponse(url=f"/finance/transactions?year_id={year_id}", status_code=303)


@router.post("/transactions/{txn_id}/attach")
async def attach_document(
    txn_id: int,
    ctx: Annotated[object, Depends(require_finance_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
    document: UploadFile = File(...),
):
    """Upload d'un justificatif (PDF, image) lié à un mouvement."""
    txn = await db.get(Transaction, txn_id)
    if not txn:
        raise HTTPException(404)

    # Valider le type de fichier
    allowed = {"application/pdf", "image/jpeg", "image/png", "image/webp"}
    if document.content_type not in allowed:
        raise HTTPException(400, "Format non supporté (PDF, JPG, PNG)")

    # Supprimer l'ancien fichier
    if txn.attachment_url:
        old_path = txn.attachment_url.lstrip("/")
        if os.path.exists(old_path):
            os.remove(old_path)

    # Sauvegarder
    ext = os.path.splitext(document.filename or "")[1] or ".pdf"
    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)
    with open(filepath, "wb") as f:
        shutil.copyfileobj(document.file, f)

    txn.attachment_url = f"/static/uploads/transactions/{filename}"
    await db.commit()
    return RedirectResponse(
        url=f"/finance/transactions?year_id={txn.masonic_year_id}", status_code=303
    )


@router.post("/transactions/{txn_id}/detach")
async def detach_document(
    txn_id: int,
    ctx: Annotated[object, Depends(require_finance_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Supprime le justificatif lié."""
    txn = await db.get(Transaction, txn_id)
    if not txn:
        raise HTTPException(404)
    if txn.attachment_url:
        filepath = txn.attachment_url.lstrip("/")
        if os.path.exists(filepath):
            os.remove(filepath)
        txn.attachment_url = None
        await db.commit()
    return RedirectResponse(
        url=f"/finance/transactions?year_id={txn.masonic_year_id}", status_code=303
    )


# ══════════════════════════════════════════════════════════════════════════════
# BILAN COMPTABLE
# ══════════════════════════════════════════════════════════════════════════════

async def _compute_bilan(
    db: AsyncSession,
    selected_year: MasonicYear,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
):
    """Calcule les données du bilan, optionnellement filtrées par période."""
    # ── Mouvements par catégorie ─────────────────────────────────────────────
    q = (
        select(Transaction)
        .options(selectinload(Transaction.category))
        .where(Transaction.masonic_year_id == selected_year.id)
    )
    if date_from:
        q = q.where(Transaction.date >= date_from)
    if date_to:
        q = q.where(Transaction.date <= date_to)
    q = q.order_by(Transaction.date)
    rt = await db.execute(q)
    all_txns = rt.scalars().all()

    charges_by_cat: dict[str, Decimal] = {}
    total_charges = Decimal("0")
    produits_by_cat: dict[str, Decimal] = {}
    total_produits = Decimal("0")

    for t in all_txns:
        amt = Decimal(str(t.amount))
        cat_name = t.category.name if t.category else ("Divers" if t.type == TransactionType.EXPENSE else "Recettes diverses")
        if t.type == TransactionType.EXPENSE:
            charges_by_cat[cat_name] = charges_by_cat.get(cat_name, Decimal("0")) + amt
            total_charges += amt
        else:
            produits_by_cat[cat_name] = produits_by_cat.get(cat_name, Decimal("0")) + amt
            total_produits += amt

    # ── Cotisations encaissées ────────────────────────────────────────────────
    cot_q = (
        select(func.sum(Payment.amount))
        .join(MemberContribution, Payment.member_contribution_id == MemberContribution.id)
        .where(MemberContribution.masonic_year_id == selected_year.id)
    )
    if date_from:
        cot_q = cot_q.where(Payment.payment_date >= date_from)
    if date_to:
        cot_q = cot_q.where(Payment.payment_date <= date_to)
    cot_r = await db.execute(cot_q)
    total_cotisations = Decimal(str(cot_r.scalar_one() or 0))

    # ── Budget prévisionnel ───────────────────────────────────────────────────
    rb = await db.execute(
        select(BudgetLine)
        .where(BudgetLine.masonic_year_id == selected_year.id)
        .order_by(BudgetLine.category_label, BudgetLine.order_position)
    )
    budget_lines = rb.scalars().all()
    total_budget = sum(Decimal(str(bl.amount)) for bl in budget_lines)
    budget_by_cat: dict[str, Decimal] = {}
    for bl in budget_lines:
        cat = bl.category_label or bl.label
        budget_by_cat[cat] = budget_by_cat.get(cat, Decimal("0")) + Decimal(str(bl.amount))

    total_produits_global = total_produits + total_cotisations
    resultat = total_produits_global - total_charges

    # ── Cotisations dues (toujours sur l'année complète) ─────────────────────
    cot_due_r = await db.execute(
        select(func.sum(MemberContribution.total_amount))
        .where(MemberContribution.masonic_year_id == selected_year.id)
    )
    total_cotisations_due = Decimal(str(cot_due_r.scalar_one() or 0))

    return {
        "charges_by_cat": dict(sorted(charges_by_cat.items())),
        "total_charges": total_charges,
        "produits_by_cat": dict(sorted(produits_by_cat.items())),
        "total_produits": total_produits,
        "total_cotisations": total_cotisations,
        "total_produits_global": total_produits_global,
        "resultat": resultat,
        "budget_by_cat": dict(sorted(budget_by_cat.items())),
        "total_budget": total_budget,
        "total_cotisations_due": total_cotisations_due,
        "creances": total_cotisations_due - total_cotisations,
    }


@router.get("/bilan", response_class=HTMLResponse)
async def bilan_view(
    request: Request,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    year_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    user, member = ctx
    r = await db.execute(select(MasonicYear).order_by(MasonicYear.start_date.desc()))
    years = r.scalars().all()

    selected_year = None
    if year_id:
        selected_year = await db.get(MasonicYear, year_id)
    if not selected_year:
        selected_year = next((y for y in years if y.is_current), years[0] if years else None)

    if not selected_year:
        return templates.TemplateResponse(request, "pages/finance/bilan.html", {
            "current_member": member, "current_user": user,
            "years": years, "selected_year": None,
        })

    d_from = date.fromisoformat(date_from) if date_from else None
    d_to   = date.fromisoformat(date_to)   if date_to   else None

    data = await _compute_bilan(db, selected_year, d_from, d_to)

    return templates.TemplateResponse(request, "pages/finance/bilan.html", {
        "current_member": member,
        "current_user": user,
        "years": years,
        "selected_year": selected_year,
        "date_from": date_from or "",
        "date_to": date_to or "",
        "is_admin": user.is_admin,
        "today": date.today(),
        **data,
    })


@router.get("/bilan/export-csv")
async def bilan_export_csv(
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    year_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    """Export CSV du bilan (charges + produits + résultat)."""
    _, member = ctx
    year = None
    if year_id:
        year = await db.get(MasonicYear, year_id)
    if not year:
        r = await db.execute(select(MasonicYear).where(MasonicYear.is_current == True).limit(1))
        year = r.scalar_one_or_none()
    if not year:
        raise HTTPException(404, "Aucune année configurée")

    d_from = date.fromisoformat(date_from) if date_from else None
    d_to   = date.fromisoformat(date_to)   if date_to   else None
    data = await _compute_bilan(db, year, d_from, d_to)

    output = io.StringIO()
    w = csv.writer(output, delimiter=";")

    period_label = ""
    if d_from or d_to:
        period_label = f" (du {d_from or '…'} au {d_to or '…'})"

    w.writerow([f"BILAN COMPTABLE — {year.label}{period_label}"])
    w.writerow([])

    w.writerow(["CHARGES", "Montant (€)"])
    for cat, amt in data["charges_by_cat"].items():
        w.writerow([cat, f"{float(amt):.2f}"])
    w.writerow(["TOTAL CHARGES", f"{float(data['total_charges']):.2f}"])
    w.writerow([])

    w.writerow(["PRODUITS", "Montant (€)"])
    w.writerow(["Cotisations encaissées", f"{float(data['total_cotisations']):.2f}"])
    for cat, amt in data["produits_by_cat"].items():
        w.writerow([cat, f"{float(amt):.2f}"])
    w.writerow(["TOTAL PRODUITS", f"{float(data['total_produits_global']):.2f}"])
    w.writerow([])

    resultat = data["resultat"]
    w.writerow(["RÉSULTAT", f"{float(resultat):+.2f}"])
    w.writerow(["", "Excédent" if resultat >= 0 else "Déficit"])
    w.writerow([])

    w.writerow(["COTISATIONS", "Montant (€)"])
    w.writerow(["Appelées (total dû)", f"{float(data['total_cotisations_due']):.2f}"])
    w.writerow(["Encaissées", f"{float(data['total_cotisations']):.2f}"])
    w.writerow(["Créances (impayés)", f"{float(data['creances']):.2f}"])

    output.seek(0)
    period_str = f"_{date_from}_{date_to}" if (date_from or date_to) else ""
    filename = f"bilan_{year.label.replace(' ', '_')}{period_str}.csv"
    return StreamingResponse(
        io.BytesIO(output.read().encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ── Gestion individuelle des paiements ──────────────────────────────────────

@router.post("/payments/{payment_id}/delete")
async def delete_payment(
    payment_id: int,
    ctx: Annotated[object, Depends(require_finance_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    payment = await db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(404)
    contrib_id = payment.member_contribution_id

    r = await db.execute(
        select(MemberContribution)
        .options(selectinload(MemberContribution.payments))
        .where(MemberContribution.id == contrib_id)
    )
    contrib = r.scalar_one_or_none()

    await db.delete(payment)
    await db.flush()

    if contrib:
        remaining_paid = sum(
            float(p.amount) for p in contrib.payments if p.id != payment_id
        )
        if remaining_paid <= 0:
            contrib.status = ContributionStatus.PENDING
        elif remaining_paid < float(contrib.total_amount):
            contrib.status = ContributionStatus.PARTIAL
        else:
            contrib.status = ContributionStatus.PAID

    await db.commit()
    year_id = contrib.masonic_year_id if contrib else ""
    return RedirectResponse(url=f"/finance/cotisations?year_id={year_id}", status_code=303)


@router.post("/payments/{payment_id}/edit")
async def edit_payment(
    payment_id: int,
    ctx: Annotated[object, Depends(require_finance_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
    amount: Annotated[float, Form()],
    method: Annotated[str, Form()],
    payment_date: Annotated[str, Form()],
    reference: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
):
    payment = await db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(404)

    payment.amount = amount
    payment.method = PaymentMethod(method)
    payment.payment_date = date.fromisoformat(payment_date)
    payment.reference = reference.strip() or None
    payment.notes = notes.strip() or None

    contrib_id = payment.member_contribution_id
    r = await db.execute(
        select(MemberContribution)
        .options(selectinload(MemberContribution.payments))
        .where(MemberContribution.id == contrib_id)
    )
    contrib = r.scalar_one_or_none()
    if contrib:
        total_paid = sum(
            float(p.amount) if p.id != payment_id else amount
            for p in contrib.payments
        )
        if total_paid <= 0:
            contrib.status = ContributionStatus.PENDING
        elif total_paid < float(contrib.total_amount):
            contrib.status = ContributionStatus.PARTIAL
        else:
            contrib.status = ContributionStatus.PAID

    await db.commit()
    year_id = contrib.masonic_year_id if contrib else ""
    return RedirectResponse(url=f"/finance/cotisations?year_id={year_id}", status_code=303)


@router.post("/cotisations/{contribution_id}/set-amount")
async def set_contribution_amount(
    contribution_id: int,
    ctx: Annotated[object, Depends(require_finance_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
    total_amount: Annotated[float, Form()],
    notes: Annotated[str, Form()] = "",
):
    """Permet de forcer manuellement le montant dû d'une cotisation."""
    r = await db.execute(
        select(MemberContribution)
        .options(selectinload(MemberContribution.payments))
        .where(MemberContribution.id == contribution_id)
    )
    contrib = r.scalar_one_or_none()
    if not contrib:
        raise HTTPException(404)

    contrib.total_amount = total_amount
    if notes.strip():
        contrib.notes = (contrib.notes or "") + f"\n[Montant modifié manuellement] {notes.strip()}"

    total_paid = sum(float(p.amount) for p in contrib.payments)
    if total_paid <= 0:
        contrib.status = ContributionStatus.PENDING
    elif total_paid < total_amount:
        contrib.status = ContributionStatus.PARTIAL
    else:
        contrib.status = ContributionStatus.PAID

    await db.commit()
    return RedirectResponse(url=f"/finance/cotisations?year_id={contrib.masonic_year_id}", status_code=303)
