"""Régression : traçabilité et alerte du Maître des Banquets sur les
inscriptions aux agapes — demande explicite du lodge (responsabilité : qui a
inscrit tel passant ?) + avertir suffisamment tôt le Maître des Banquets, qui
a des délais de préparation, maintenant que le lien d'inscription public est
partageable par tout membre (donc plus seulement par les officiers qui
savaient déjà qu'un passant arrivait)."""
import uuid
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

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
from app.models.meetings import Meeting, MeetingType, MeetingGrade, MeetingStatus, MeetingVisitor


async def _current_year_id(db: AsyncSession) -> int:
    # Un commit() direct via la fixture `db` persiste réellement dans le même
    # moteur que les requêtes HTTP (cf. _override_get_db, conftest.py) : le
    # rollback() de fin de test ne défait pas ce qui a déjà été commit. Créer
    # un nouveau MasonicYear "is_current=True" à chaque test en laisserait
    # plusieurs en base simultanément, et meeting_create() (scalar_one_or_none
    # sur is_current==True) casse dès qu'il y en a plus d'un — réutiliser
    # l'année courante existante si elle existe déjà.
    existing = (await db.execute(select(MasonicYear).where(MasonicYear.is_current == True))).scalars().first()  # noqa: E712
    if existing:
        return existing.id
    year = MasonicYear(
        label=f"test-year-agape-{uuid.uuid4().hex[:8]}",
        start_date=date.today() - timedelta(days=30),
        end_date=date.today() + timedelta(days=300), is_current=True,
    )
    db.add(year)
    await db.commit()
    await db.refresh(year)
    return year.id


async def _make_meeting(db: AsyncSession, *, agape_enabled: bool = True) -> Meeting:
    year_id = await _current_year_id(db)
    meeting = Meeting(
        masonic_year_id=year_id,
        meeting_date=date.today() + timedelta(days=7), meeting_time="20:30",
        type=MeetingType.SOLENNELLE, grade=MeetingGrade.APPRENTI, status=MeetingStatus.PUBLISHED,
        agape_enabled=agape_enabled, registration_open=True,
    )
    db.add(meeting)
    await db.commit()
    await db.refresh(meeting)
    return meeting


async def _make_banquet_master(db: AsyncSession, *, cumulated: bool = False) -> Member:
    suffix = uuid.uuid4().hex[:10]
    m = Member(
        first_name="Maitre", last_name="Banquets",
        email=f"mdb.{suffix}@test.fr",
        status=MemberStatus.ACTIVE, masonic_grade=MasonicGrade.MAITRE,
        lodge_function=LodgeFunction.FRERE if cumulated else LodgeFunction.MAITRE_BANQUETS,
        membership_type=MembershipType.APPARTENANCE, civility="F",
    )
    db.add(m)
    await db.flush()
    if cumulated:
        db.add(MemberResponsibility(
            member_id=m.id, type=ResponsibilityType.OFFICE_SECOND,
            label="Maître des Banquets", lodge_function=LodgeFunction.MAITRE_BANQUETS, is_active=True,
        ))
    await db.commit()
    return m


async def _member_client(db: AsyncSession, *, lodge_function=LodgeFunction.FRERE) -> AsyncClient:
    suffix = uuid.uuid4().hex[:10]
    login = f"test.agape.{suffix}"
    m = Member(
        first_name="Simple", last_name="Membre", email=f"{login}@test.fr",
        status=MemberStatus.ACTIVE, masonic_grade=MasonicGrade.MAITRE,
        lodge_function=lodge_function, membership_type=MembershipType.APPARTENANCE, civility="F",
    )
    db.add(m)
    await db.flush()
    u = User(member_id=m.id, login=login, password_hash=hash_password("TestPass123!"), is_admin=False, is_active=True)
    db.add(u)
    await db.commit()

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    resp = await client.post("/auth/login", data={"username": login, "password": "TestPass123!"}, follow_redirects=False)
    assert resp.status_code in (302, 303)
    client.cookies.update(resp.cookies)
    return client


@pytest.mark.asyncio
async def test_officer_add_visitor_records_registered_by_and_notifies(admin_client: AsyncClient, db: AsyncSession):
    meeting = await _make_meeting(db)
    mdb = await _make_banquet_master(db)

    with patch("app.services.banquet_notify._send_raw", new_callable=AsyncMock) as mock_send:
        resp = await admin_client.post(
            f"/attendance/meeting/{meeting.id}/add-visitor",
            data={
                "civility": "F", "first_name": "Jean", "last_name": "Passant",
                "lodge_name": "Loge Amie", "agape": "true", "agape_guests": "1",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303, resp.text

    mv = (await db.execute(select(MeetingVisitor).where(MeetingVisitor.meeting_id == meeting.id))).scalar_one()
    assert mv.registered_by_id is not None
    assert mv.agape is True

    # Email de notif envoyé au Maître des Banquets (admin_client != mdb, pas d'auto-exclusion)
    assert mock_send.await_count >= 1
    sent_to = [call.kwargs.get("to") for call in mock_send.await_args_list]
    assert mdb.email in sent_to


@pytest.mark.asyncio
async def test_banquet_master_adding_self_is_not_notified(db: AsyncSession):
    """Si l'officier qui inscrit cumule VM + Maître des Banquets, pas de notif
    à lui-même — mais un AUTRE Maître des Banquets doit, lui, être notifié."""
    meeting = await _make_meeting(db)
    other_mdb = await _make_banquet_master(db)

    suffix = uuid.uuid4().hex[:10]
    login = f"test.vmdb.{suffix}"
    vm_and_mdb = Member(
        first_name="VM", last_name="CumulBanquets", email=f"{login}@test.fr",
        status=MemberStatus.ACTIVE, masonic_grade=MasonicGrade.MAITRE,
        lodge_function=LodgeFunction.VM, membership_type=MembershipType.APPARTENANCE, civility="F",
    )
    db.add(vm_and_mdb)
    await db.flush()
    db.add(MemberResponsibility(
        member_id=vm_and_mdb.id, type=ResponsibilityType.OFFICE_SECOND,
        label="Maître des Banquets", lodge_function=LodgeFunction.MAITRE_BANQUETS, is_active=True,
    ))
    user = User(member_id=vm_and_mdb.id, login=login, password_hash=hash_password("TestPass123!"), is_admin=False, is_active=True)
    db.add(user)
    await db.commit()

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        resp = await client.post("/auth/login", data={"username": login, "password": "TestPass123!"}, follow_redirects=False)
        assert resp.status_code in (302, 303)
        client.cookies.update(resp.cookies)

        with patch("app.services.banquet_notify._send_raw", new_callable=AsyncMock) as mock_send:
            resp = await client.post(
                f"/attendance/meeting/{meeting.id}/add-visitor",
                data={
                    "civility": "F", "first_name": "Jean", "last_name": "Passant2",
                    "agape": "true", "agape_guests": "0",
                },
                follow_redirects=False,
            )
            assert resp.status_code == 303, resp.text
    finally:
        await client.aclose()

    sent_to = [call.kwargs.get("to") for call in mock_send.await_args_list]
    assert vm_and_mdb.email not in sent_to  # pas de self-notif
    assert other_mdb.email in sent_to       # l'autre Maître des Banquets, oui


@pytest.mark.asyncio
async def test_public_self_registration_leaves_registered_by_null_and_notifies(db: AsyncSession):
    meeting = await _make_meeting(db)
    mdb = await _make_banquet_master(db, cumulated=True)

    with patch("app.services.banquet_notify._send_raw", new_callable=AsyncMock) as mock_send:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                f"/meetings/public/{meeting.token}",
                data={
                    "visitor_type": "visitor", "civility": "F",
                    "first_name": "Passant", "last_name": "Public",
                    "agape": "true", "agape_guests": "0",
                },
            )
            assert resp.status_code == 200, resp.text

    mv = (await db.execute(select(MeetingVisitor).where(MeetingVisitor.meeting_id == meeting.id))).scalar_one()
    assert mv.registered_by_id is None
    assert mv.agape is True

    assert mock_send.await_count >= 1
    sent_to = [call.kwargs.get("to") for call in mock_send.await_args_list]
    assert mdb.email in sent_to


@pytest.mark.asyncio
async def test_no_notification_when_agape_disabled(db: AsyncSession):
    meeting = await _make_meeting(db, agape_enabled=False)
    await _make_banquet_master(db)

    with patch("app.services.banquet_notify._send_raw", new_callable=AsyncMock) as mock_send:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                f"/meetings/public/{meeting.token}",
                data={
                    "visitor_type": "visitor", "civility": "F",
                    "first_name": "Passant", "last_name": "SansAgapes",
                    "agape": "true", "agape_guests": "0",
                },
            )
            assert resp.status_code == 200, resp.text

    mock_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_banquet_page_shows_who_registered(db: AsyncSession):
    meeting = await _make_meeting(db)
    mdb = await _make_banquet_master(db)
    client = await _member_client(db, lodge_function=LodgeFunction.MAITRE_BANQUETS)

    with patch("app.services.banquet_notify._send_raw", new_callable=AsyncMock):
        # Auto-inscription publique
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as pub_client:
            await pub_client.post(
                f"/meetings/public/{meeting.token}",
                data={
                    "visitor_type": "visitor", "civility": "F",
                    "first_name": "Auto", "last_name": "Visiteur",
                    "agape": "true", "agape_guests": "0",
                },
            )

    try:
        resp = await client.get(f"/meetings/{meeting.id}/banquet")
        assert resp.status_code == 200
        assert "Auto-inscription (lien public)" in resp.text
    finally:
        await client.aclose()
