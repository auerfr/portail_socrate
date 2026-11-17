"""Le lien d'inscription public d'une tenue doit être visible par N'IMPORTE
QUEL frère/sœur connecté·e, pas seulement VM/Secrétaire/admin — un frère qui
invite personnellement un visiteur doit pouvoir le lui transmettre lui-même,
sans passer par l'officier qui prépare le programme PDF."""
import uuid
from datetime import date, timedelta

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.dependencies import hash_password
from app.models.identity import Member, MemberStatus, MasonicGrade, LodgeFunction, MembershipType, User
from app.models.lodge import MasonicYear
from app.models.meetings import Meeting, MeetingType, MeetingGrade, MeetingStatus


async def _make_meeting(db: AsyncSession) -> int:
    year = MasonicYear(
        label="test-year-reglink", start_date=date.today() - timedelta(days=30),
        end_date=date.today() + timedelta(days=300), is_current=True,
    )
    db.add(year)
    await db.flush()
    meeting = Meeting(
        masonic_year_id=year.id,
        meeting_date=date.today() + timedelta(days=7), meeting_time="20:30",
        type=MeetingType.SOLENNELLE, grade=MeetingGrade.APPRENTI, status=MeetingStatus.PUBLISHED,
    )
    db.add(meeting)
    await db.commit()
    await db.refresh(meeting)
    return meeting.id


async def _plain_member_client(db: AsyncSession) -> AsyncClient:
    suffix = uuid.uuid4().hex[:10]
    login = f"test.frere.{suffix}"
    m = Member(
        first_name="Simple", last_name="Frère",
        email=f"{login}@test.fr",
        status=MemberStatus.ACTIVE,
        masonic_grade=MasonicGrade.MAITRE,
        lodge_function=LodgeFunction.FRERE,
        membership_type=MembershipType.APPARTENANCE,
        civility="F",
    )
    db.add(m)
    await db.flush()
    u = User(member_id=m.id, login=login, password_hash=hash_password("TestPass123!"), is_admin=False, is_active=True)
    db.add(u)
    await db.commit()

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    resp = await client.post("/auth/login", data={"username": login, "password": "TestPass123!"}, follow_redirects=False)
    assert resp.status_code in (302, 303), f"Login failed: {resp.status_code}"
    client.cookies.update(resp.cookies)
    return client


@pytest.mark.asyncio
async def test_plain_member_sees_registration_link(db: AsyncSession):
    meeting_id = await _make_meeting(db)
    client = await _plain_member_client(db)
    try:
        resp = await client.get(f"/meetings/{meeting_id}")
        assert resp.status_code == 200, resp.text
        assert "Lien d'inscription" in resp.text
        assert f"/inscription/" in resp.text
    finally:
        await client.aclose()
