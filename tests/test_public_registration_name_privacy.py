"""Régression confidentialité : le lien public /inscription/{token} (alias
de /meetings/public/{token}) est accessible SANS authentification — n'importe
qui qui le détient (passant, correspondant externe via le programme) voyait
jusqu'ici la liste complète des membres par nom de famille en clair dans le
menu « Votre nom ». Doit utiliser la même écriture maçonnique (anon_nom) que
le reste de l'app pour les tiers non autorisés."""
import uuid
from datetime import date, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import Member, MemberStatus, MasonicGrade, LodgeFunction, MembershipType
from app.models.lodge import MasonicYear
from app.models.meetings import Meeting, MeetingType, MeetingGrade, MeetingStatus


async def _current_year_id(db: AsyncSession) -> int:
    from sqlalchemy import select
    existing = (await db.execute(select(MasonicYear).where(MasonicYear.is_current == True))).scalars().first()  # noqa: E712
    if existing:
        return existing.id
    year = MasonicYear(
        label=f"test-year-privacy-{uuid.uuid4().hex[:8]}",
        start_date=date.today() - timedelta(days=30),
        end_date=date.today() + timedelta(days=300), is_current=True,
    )
    db.add(year)
    await db.commit()
    await db.refresh(year)
    return year.id


async def _make_meeting(db: AsyncSession) -> Meeting:
    year_id = await _current_year_id(db)
    meeting = Meeting(
        masonic_year_id=year_id,
        meeting_date=date.today() + timedelta(days=7), meeting_time="20:30",
        type=MeetingType.SOLENNELLE, grade=MeetingGrade.APPRENTI, status=MeetingStatus.PUBLISHED,
        agape_enabled=False, registration_open=True,
    )
    db.add(meeting)
    await db.commit()
    await db.refresh(meeting)
    return meeting


@pytest.mark.asyncio
async def test_public_page_shows_masonic_notation_not_real_last_name(client: AsyncClient, db: AsyncSession):
    meeting = await _make_meeting(db)
    suffix = uuid.uuid4().hex[:8]
    member = Member(
        first_name="Confidentiel", last_name=f"Patronyme{suffix}",
        email=f"patronyme.{suffix}@test.fr",
        status=MemberStatus.ACTIVE, masonic_grade=MasonicGrade.MAITRE,
        lodge_function=LodgeFunction.FRERE, membership_type=MembershipType.APPARTENANCE, civility="F",
    )
    db.add(member)
    await db.commit()

    resp = await client.get(f"/meetings/public/{meeting.token}")
    assert resp.status_code == 200, resp.text
    text = resp.text

    # Le prénom reste visible (convention déjà en place ailleurs dans l'app :
    # anniversaires, announcements, attendance…) mais le nom de famille réel
    # ne doit JAMAIS apparaître en clair sur cette page sans authentification.
    assert "Confidentiel" in text
    assert f"Patronyme{suffix}" not in text
    # consonnes du nom de famille (écriture maçonnique), cf. masonic_write()
    from app.utils.text import masonic_write
    assert masonic_write(f"Patronyme{suffix}") in text


@pytest.mark.asyncio
async def test_inscription_alias_redirects_to_public_meeting_page(client: AsyncClient, db: AsyncSession):
    meeting = await _make_meeting(db)
    resp = await client.get(f"/inscription/{meeting.token}", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == f"/meetings/public/{meeting.token}"
