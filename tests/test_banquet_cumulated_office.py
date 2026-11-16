"""Régression : un frère qui cumule « Maître des Banquets » comme office
SECONDAIRE (MemberResponsibility.type == OFFICE_SECOND) perdait l'accès à la
page agapes, parce que les contrôles d'accès (meetings.py) ne vérifiaient que
member.lodge_function (l'office PRINCIPAL), jamais les offices cumulés —
incident réel : un frère dont le lodge_function principal n'était plus
MAITRE_BANQUETS (mais qui le cumulait en secondaire) a reçu un 403."""
import uuid
from datetime import date, timedelta

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.dependencies import hash_password
from app.models.identity import (
    Member, MemberStatus, MasonicGrade, LodgeFunction, MembershipType,
    MemberResponsibility, ResponsibilityType, User,
)
from app.models.lodge import MasonicYear
from app.models.meetings import Meeting, MeetingType, MeetingGrade, MeetingStatus


async def _ensure_current_year(db: AsyncSession) -> int:
    existing = (await db.execute(select(MasonicYear).where(MasonicYear.is_current == True))).scalars().first()  # noqa: E712
    if existing:
        return existing.id
    year = MasonicYear(
        label="test-year-banquet", start_date=date.today() - timedelta(days=30),
        end_date=date.today() + timedelta(days=300), is_current=True,
    )
    db.add(year)
    await db.commit()
    await db.refresh(year)
    return year.id


async def _make_member_client(db: AsyncSession, *, lodge_function: LodgeFunction, cumulated_banquet: bool) -> AsyncClient:
    suffix = uuid.uuid4().hex[:10]
    login = f"test.mdb.{suffix}"
    m = Member(
        first_name="Jean-Pierre", last_name="Test",
        email=f"{login}@test.fr",
        status=MemberStatus.ACTIVE,
        masonic_grade=MasonicGrade.MAITRE,
        lodge_function=lodge_function,
        membership_type=MembershipType.APPARTENANCE,
        civility="F",
    )
    db.add(m)
    await db.flush()
    if cumulated_banquet:
        db.add(MemberResponsibility(
            member_id=m.id, type=ResponsibilityType.OFFICE_SECOND,
            label="Maître des Banquets", lodge_function=LodgeFunction.MAITRE_BANQUETS,
            is_active=True,
        ))
    u = User(member_id=m.id, login=login, password_hash=hash_password("TestPass123!"), is_admin=False, is_active=True)
    db.add(u)
    await db.commit()

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    resp = await client.post("/auth/login", data={"username": login, "password": "TestPass123!"}, follow_redirects=False)
    assert resp.status_code in (302, 303), f"Login failed: {resp.status_code}"
    client.cookies.update(resp.cookies)
    return client


async def _make_meeting(db: AsyncSession) -> int:
    year_id = await _ensure_current_year(db)
    meeting = Meeting(
        masonic_year_id=year_id,
        meeting_date=date.today() + timedelta(days=7), meeting_time="20:30",
        type=MeetingType.SOLENNELLE, grade=MeetingGrade.APPRENTI, status=MeetingStatus.PUBLISHED,
    )
    db.add(meeting)
    await db.commit()
    await db.refresh(meeting)
    return meeting.id


@pytest.mark.asyncio
async def test_cumulated_banquet_office_grants_banquet_page_access(db: AsyncSession):
    meeting_id = await _make_meeting(db)
    client = await _make_member_client(db, lodge_function=LodgeFunction.HOSPITALIER, cumulated_banquet=True)
    try:
        resp = await client.get(f"/meetings/{meeting_id}/banquet")
        assert resp.status_code == 200, resp.text
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_member_without_banquet_role_is_denied(db: AsyncSession):
    meeting_id = await _make_meeting(db)
    client = await _make_member_client(db, lodge_function=LodgeFunction.HOSPITALIER, cumulated_banquet=False)
    try:
        resp = await client.get(f"/meetings/{meeting_id}/banquet")
        assert resp.status_code == 403
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_primary_banquet_function_still_grants_access(db: AsyncSession):
    meeting_id = await _make_meeting(db)
    client = await _make_member_client(db, lodge_function=LodgeFunction.MAITRE_BANQUETS, cumulated_banquet=False)
    try:
        resp = await client.get(f"/meetings/{meeting_id}/banquet")
        assert resp.status_code == 200, resp.text
    finally:
        await client.aclose()
