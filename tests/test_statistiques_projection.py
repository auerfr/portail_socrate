"""Page Statistiques de la loge — simulation de fin d'année : l'utilisateur a
demandé (1) de pré-remplir les mouvements prévus d'après la moyenne
historique plutôt que de partir de zéro, (2) de pouvoir simuler l'âge moyen
en indiquant l'âge des nouveaux arrivants, et (3) une courbe de tendance sur
les graphiques d'effectif. Vérifie que la page expose bien les données
nécessaires à ces trois calculs (faits côté JS, non testables ici)."""
import uuid
from datetime import date, timedelta

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.dependencies import hash_password
from app.models.identity import Member, MemberStatus, MasonicGrade, LodgeFunction, MembershipType, User
from app.models.lodge import MasonicYear
from app.models.meetings import Meeting, MeetingType, MeetingGrade, MeetingStatus, Attendance, AttendanceStatus


async def _member_client(db: AsyncSession) -> AsyncClient:
    suffix = uuid.uuid4().hex[:10]
    login = f"test.stats.{suffix}"
    m = Member(
        first_name="Simple", last_name="Membre", email=f"{login}@test.fr",
        status=MemberStatus.ACTIVE, masonic_grade=MasonicGrade.MAITRE,
        lodge_function=LodgeFunction.FRERE, membership_type=MembershipType.APPARTENANCE, civility="F",
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


async def _seed_years_and_members(db: AsyncSession):
    suffix = uuid.uuid4().hex[:8]
    previous = MasonicYear(
        label=f"test-prev-{suffix}",
        start_date=date.today() - timedelta(days=400), end_date=date.today() - timedelta(days=35),
        is_current=False,
    )
    current = MasonicYear(
        label=f"test-cur-{suffix}",
        start_date=date.today() - timedelta(days=34), end_date=date.today() + timedelta(days=330),
        is_current=True,
    )
    db.add_all([previous, current])
    await db.flush()

    today = date.today()
    for i in range(3):
        db.add(Member(
            first_name=f"M{i}", last_name=f"Test{suffix}{i}",
            email=f"m{i}.{suffix}@test.fr",
            status=MemberStatus.ACTIVE, masonic_grade=MasonicGrade.MAITRE,
            lodge_function=LodgeFunction.FRERE, membership_type=MembershipType.APPARTENANCE, civility="F",
            birth_date=today - timedelta(days=365 * (40 + i)),
            initiation_date=today - timedelta(days=365 * (5 + i)),
            membership_start_date=today - timedelta(days=365 * (5 + i)),
        ))
    await db.commit()
    return previous, current


@pytest.mark.asyncio
async def test_projection_card_prefilled_from_history_and_age_simulation_wired(db: AsyncSession):
    await _seed_years_and_members(db)
    client = await _member_client(db)

    resp = await client.get("/statistiques/")
    assert resp.status_code == 200, resp.text
    text = resp.text

    # Les champs de mouvements ne partent plus de 0 à l'aveugle : pré-remplis
    # d'après la moyenne historique des années complètes.
    assert "moyenne historique" in text
    assert 'data-proj="in"' in text
    assert 'data-proj="out"' in text

    # Simulation d'âge moyen avec l'âge des nouveaux arrivants.
    assert "Âge moyen des nouveaux" in text
    assert 'data-proj="age-in"' in text
    assert 'data-proj="age"' in text
    assert "data-avg-age=" in text
    assert "data-ages-known=" in text

    # Courbe de tendance sur les graphiques d'effectif.
    assert "linearTrend" in text
    assert "trendDataset('Tendance'" in text


@pytest.mark.asyncio
async def test_three_year_trend_feeds_mid_term_projection(db: AsyncSession):
    """Avec au moins 3 années maçonniques complètes (le minimum pour qu'une
    régression linéaire ait un sens), la section « Projection à 3 ans » doit
    apparaître avec des valeurs calculées — pas seulement les données brutes
    pour les graphiques JS, comme dans le test ci-dessus."""
    suffix = uuid.uuid4().hex[:8]
    today = date.today()
    years = []
    for i in range(3):
        y = MasonicYear(
            label=f"test-trend-y{i}-{suffix}",
            start_date=today - timedelta(days=365 * (3 - i)),
            end_date=today - timedelta(days=365 * (3 - i) - 330) if i < 2 else today + timedelta(days=30),
            is_current=(i == 2),
        )
        db.add(y)
        years.append(y)
    await db.flush()

    members = []
    for i in range(3):
        m = Member(
            first_name=f"T{i}", last_name=f"Trend{suffix}{i}",
            email=f"t{i}.{suffix}@test.fr",
            status=MemberStatus.ACTIVE, masonic_grade=MasonicGrade.MAITRE,
            lodge_function=LodgeFunction.FRERE, membership_type=MembershipType.APPARTENANCE, civility="F",
            birth_date=today - timedelta(days=365 * (35 + i)),
            initiation_date=today - timedelta(days=365 * (4 + i)),
            membership_start_date=today - timedelta(days=365 * (4 + i)),
        )
        db.add(m)
        members.append(m)
    await db.flush()

    # Une tenue par année, grade ALL (tout le monde est "attendu"), assiduité
    # croissante d'année en année pour obtenir une pente non nulle.
    for i, y in enumerate(years):
        mtg = Meeting(
            masonic_year_id=y.id, meeting_date=y.start_date + timedelta(days=30), meeting_time="20:30",
            type=MeetingType.SOLENNELLE, grade=MeetingGrade.ALL, status=MeetingStatus.PUBLISHED,
            agape_enabled=False, registration_open=False,
        )
        db.add(mtg)
        await db.flush()
        present_count = i + 1  # 1, 2 puis 3 présents sur 3 membres : assiduité croissante
        for j, m in enumerate(members):
            status = AttendanceStatus.PRESENT if j < present_count else AttendanceStatus.ABSENT
            db.add(Attendance(meeting_id=mtg.id, member_id=m.id, status=status))
    await db.commit()

    client = await _member_client(db)
    resp = await client.get("/statistiques/")
    assert resp.status_code == 200, resp.text
    text = resp.text

    assert "Projection à 3 ans" in text
    assert "Effectif" in text and "Assiduité" in text
