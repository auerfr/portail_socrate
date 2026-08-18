"""Service — création d'une nouvelle année maçonnique.

Centralise ce que faisaient séparément Finance (`/finance/budget/new-year`)
et Secrétariat (`/secretariat/annees/new`) : bascule de l'année courante,
archivage du tableau de loge dans OfficerAssignment (jusque-là jamais
alimenté), et copie optionnelle du budget prévisionnel.
"""
from datetime import date as _date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.associative import OfficerAssignment
from app.models.finance import BudgetLine
from app.models.lodge import LodgeOffice, MasonicYear


async def create_new_masonic_year(
    db: AsyncSession,
    label: str,
    start_date: _date,
    end_date: _date,
    copy_budget_from_year_id: Optional[int] = None,
) -> MasonicYear:
    """Crée une nouvelle année maçonnique et bascule `is_current`.

    Archive aussi le tableau de loge courant (LodgeOffice) dans
    OfficerAssignment, daté du jour, pour garder une mémoire année par année
    de qui occupait quelle fonction — avant que les offices ne soient
    réattribués pour la nouvelle année via Paramètres.

    Lève ValueError si le libellé existe déjà.
    """
    label = label.strip()
    if not label:
        raise ValueError("Le libellé est requis")

    existing = await db.execute(select(MasonicYear).where(MasonicYear.label == label))
    if existing.scalar_one_or_none():
        raise ValueError("Une année maçonnique porte déjà ce libellé")

    today = _date.today()

    # Snapshot du tableau de loge courant AVANT bascule de l'année.
    r_off = await db.execute(select(LodgeOffice).where(LodgeOffice.member_id.isnot(None)))
    current_offices = r_off.scalars().all()

    # Clôture de l'année (ou des années) précédemment courante(s), et des
    # affectations d'officiers encore ouvertes qui s'y rattachent.
    r_cur = await db.execute(select(MasonicYear).where(MasonicYear.is_current == True))
    previous_years = r_cur.scalars().all()
    previous_year_ids = [y.id for y in previous_years]
    for y in previous_years:
        y.is_current = False

    if previous_year_ids:
        r_open = await db.execute(
            select(OfficerAssignment).where(
                OfficerAssignment.masonic_year_id.in_(previous_year_ids),
                OfficerAssignment.is_current == True,
            )
        )
        for oa in r_open.scalars().all():
            oa.is_current = False
            oa.end_date = today

    new_year = MasonicYear(
        label=label,
        start_date=start_date,
        end_date=end_date,
        is_current=True,
    )
    db.add(new_year)
    await db.flush()

    for office in current_offices:
        db.add(OfficerAssignment(
            masonic_year_id=new_year.id,
            member_id=office.member_id,
            function=office.label,
            investiture_date=today,
            is_current=True,
        ))

    if copy_budget_from_year_id:
        r_lines = await db.execute(
            select(BudgetLine).where(BudgetLine.masonic_year_id == copy_budget_from_year_id)
        )
        for bl in r_lines.scalars().all():
            db.add(BudgetLine(
                masonic_year_id=new_year.id,
                label=bl.label,
                type=bl.type,
                category_label=bl.category_label,
                amount=bl.amount,
                order_position=bl.order_position,
                notes=bl.notes,
            ))

    return new_year
