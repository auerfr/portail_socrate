"""Régression confidentialité : la courbe d'évolution de l'assiduité d'un
membre (ajoutée sur /attendance/member/{id}) est plus exposante qu'une
simple liste de dates pointées — demande explicite : réservée au membre
lui-même, au VM, au Secrétaire et à l'administrateur, PAS aux Surveillants
(qui ont par ailleurs accès à cette page pour l'émargement, via
can_manage_attendance — volontairement plus large pour ce seul usage)."""
import uuid
from datetime import date, timedelta

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.dependencies import hash_password
from app.models.identity import (
    Member, MemberStatus, MasonicGrade, LodgeFunction, MembershipType, User,
)
from app.models.lodge import MasonicYear
from app.models.meetings import Meeting, MeetingType, MeetingGrade, MeetingStatus, Attendance, AttendanceStatus


async def _member_client(db: AsyncSession, *, lodge_function=LodgeFunction.FRERE, is_admin: bool = False) -> AsyncClient:
    suffix = uuid.uuid4().hex[:10]
    login = f"test.attpriv.{suffix}"
    m = Member(
        first_name="Viewer", last_name=f"V{suffix}", email=f"{login}@test.fr",
        status=MemberStatus.ACTIVE, masonic_grade=MasonicGrade.MAITRE,
        lodge_function=lodge_function, membership_type=MembershipType.APPARTENANCE, civility="F",
    )
    db.add(m)
    await db.flush()
    u = User(member_id=m.id, login=login, password_hash=hash_password("TestPass123!"), is_admin=is_admin, is_active=True)
    db.add(u)
    await db.commit()

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    resp = await client.post("/auth/login", data={"username": login, "password": "TestPass123!"}, follow_redirects=False)
    assert resp.status_code in (302, 303)
    client.cookies.update(resp.cookies)
    return client, m


async def _target_with_two_years_of_attendance(db: AsyncSession) -> Member:
    suffix = uuid.uuid4().hex[:8]
    y1 = MasonicYear(
        label=f"test-y1-{suffix}", start_date=date.today() - timedelta(days=400),
        end_date=date.today() - timedelta(days=35), is_current=False,
    )
    y2 = MasonicYear(
        label=f"test-y2-{suffix}", start_date=date.today() - timedelta(days=34),
        end_date=date.today() + timedelta(days=330), is_current=True,
    )
    db.add_all([y1, y2])
    await db.flush()

    target = Member(
        first_name="Cible", last_name=f"Cible{suffix}", email=f"cible.{suffix}@test.fr",
        status=MemberStatus.ACTIVE, masonic_grade=MasonicGrade.MAITRE,
        lodge_function=LodgeFunction.FRERE, membership_type=MembershipType.APPARTENANCE, civility="F",
    )
    db.add(target)
    await db.flush()

    for yr, days_ago in ((y1, 200), (y2, 10)):
        mtg = Meeting(
            masonic_year_id=yr.id, meeting_date=date.today() - timedelta(days=days_ago), meeting_time="20:30",
            type=MeetingType.SOLENNELLE, grade=MeetingGrade.APPRENTI, status=MeetingStatus.PUBLISHED,
            agape_enabled=False, registration_open=False,
        )
        db.add(mtg)
        await db.flush()
        db.add(Attendance(meeting_id=mtg.id, member_id=target.id, status=AttendanceStatus.PRESENT))
    await db.commit()
    return target


@pytest.mark.asyncio
async def test_member_sees_own_attendance_evolution(db: AsyncSession):
    target = await _target_with_two_years_of_attendance(db)
    client, viewer = await _member_client(db, lodge_function=LodgeFunction.FRERE)
    # Se connecter en tant que la cible elle-même (pas le viewer tiers) :
    # on reconnecte un client sous le login de "target" pour simuler son propre accès.
    suffix = uuid.uuid4().hex[:8]
    login = f"test.self.{suffix}"
    u = User(member_id=target.id, login=login, password_hash=hash_password("TestPass123!"), is_admin=False, is_active=True)
    db.add(u)
    await db.commit()
    self_client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    resp = await self_client.post("/auth/login", data={"username": login, "password": "TestPass123!"}, follow_redirects=False)
    self_client.cookies.update(resp.cookies)

    resp = await self_client.get(f"/attendance/member/{target.id}")
    assert resp.status_code == 200, resp.text
    assert "Évolution de l'assiduité" in resp.text


@pytest.mark.asyncio
async def test_surveillant_cannot_see_others_attendance_evolution(db: AsyncSession):
    target = await _target_with_two_years_of_attendance(db)
    client, viewer = await _member_client(db, lodge_function=LodgeFunction.PREMIER_S)

    resp = await client.get(f"/attendance/member/{target.id}")
    assert resp.status_code == 200, resp.text
    assert "Évolution de l'assiduité" not in resp.text


@pytest.mark.asyncio
async def test_secretaire_can_see_others_attendance_evolution(db: AsyncSession):
    target = await _target_with_two_years_of_attendance(db)
    client, viewer = await _member_client(db, lodge_function=LodgeFunction.SECRETAIRE)

    resp = await client.get(f"/attendance/member/{target.id}")
    assert resp.status_code == 200, resp.text
    assert "Évolution de l'assiduité" in resp.text


@pytest.mark.asyncio
async def test_vm_can_see_others_attendance_evolution(db: AsyncSession):
    target = await _target_with_two_years_of_attendance(db)
    client, viewer = await _member_client(db, lodge_function=LodgeFunction.VM)

    resp = await client.get(f"/attendance/member/{target.id}")
    assert resp.status_code == 200, resp.text
    assert "Évolution de l'assiduité" in resp.text


@pytest.mark.asyncio
async def test_admin_can_see_others_attendance_evolution(db: AsyncSession):
    target = await _target_with_two_years_of_attendance(db)
    client, viewer = await _member_client(db, lodge_function=LodgeFunction.FRERE, is_admin=True)

    resp = await client.get(f"/attendance/member/{target.id}")
    assert resp.status_code == 200, resp.text
    assert "Évolution de l'assiduité" in resp.text
