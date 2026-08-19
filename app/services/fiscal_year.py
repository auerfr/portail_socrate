"""Service — création d'une nouvelle année civile (FiscalYear).

Axe de rattachement du budget, des cotisations, de la trésorerie et du
bilan — distinct de MasonicYear (officiers, tenues). Le budget prévisionnel
de l'année civile N+1 est généralement voté en décembre de l'année N.
"""
from datetime import date as _date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.finance import BudgetLine, FiscalYear


async def create_new_fiscal_year(
    db: AsyncSession,
    label: str,
    start_date: _date,
    end_date: _date,
    copy_budget_from_year_id: Optional[int] = None,
) -> FiscalYear:
    """Crée une nouvelle année civile comme **brouillon** (`is_current=False`).

    Le budget, les taux de capitation et le barème peuvent être préparés
    librement sur ce brouillon (rien ne dépend de `is_current` pour éditer)
    avant de l'activer explicitement via `activate_fiscal_year` — typiquement
    une fois le budget voté en assemblée.

    Exception : si c'est la toute première année civile du système, elle
    devient automatiquement courante (sinon l'application n'a aucune année
    active).

    Lève ValueError si le libellé existe déjà.
    """
    label = label.strip()
    if not label:
        raise ValueError("Le libellé est requis")

    existing = await db.execute(select(FiscalYear).where(FiscalYear.label == label))
    if existing.scalar_one_or_none():
        raise ValueError("Une année civile porte déjà ce libellé")

    r_any = await db.execute(select(FiscalYear.id).limit(1))
    is_bootstrap = r_any.scalar_one_or_none() is None

    new_year = FiscalYear(
        label=label,
        start_date=start_date,
        end_date=end_date,
        is_current=is_bootstrap,
    )
    db.add(new_year)
    await db.flush()

    if copy_budget_from_year_id:
        r_lines = await db.execute(
            select(BudgetLine).where(BudgetLine.fiscal_year_id == copy_budget_from_year_id)
        )
        for bl in r_lines.scalars().all():
            db.add(BudgetLine(
                fiscal_year_id=new_year.id,
                masonic_year_id=bl.masonic_year_id,
                label=bl.label,
                type=bl.type,
                category_label=bl.category_label,
                amount=bl.amount,
                order_position=bl.order_position,
                notes=bl.notes,
            ))

    return new_year


async def activate_fiscal_year(db: AsyncSession, year_id: int) -> FiscalYear:
    """Active une année civile (brouillon ou passée) comme année courante,
    et bascule les autres à `is_current=False`."""
    target = await db.get(FiscalYear, year_id)
    if not target:
        raise ValueError("Année civile introuvable")

    r_cur = await db.execute(select(FiscalYear).where(FiscalYear.is_current == True))
    for y in r_cur.scalars().all():
        y.is_current = False

    target.is_current = True
    return target
