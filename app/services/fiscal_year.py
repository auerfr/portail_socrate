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
    """Crée une nouvelle année civile et bascule `is_current`.

    Lève ValueError si le libellé existe déjà.
    """
    label = label.strip()
    if not label:
        raise ValueError("Le libellé est requis")

    existing = await db.execute(select(FiscalYear).where(FiscalYear.label == label))
    if existing.scalar_one_or_none():
        raise ValueError("Une année civile porte déjà ce libellé")

    r_cur = await db.execute(select(FiscalYear).where(FiscalYear.is_current == True))
    for y in r_cur.scalars().all():
        y.is_current = False

    new_year = FiscalYear(
        label=label,
        start_date=start_date,
        end_date=end_date,
        is_current=True,
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
