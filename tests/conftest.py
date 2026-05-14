"""Fixtures partagées — base de données en mémoire + client de test."""
import asyncio
import os
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

# Forcer une DB SQLite en mémoire pour les tests (jamais la prod)
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test_secret_key_12345678901234567890")
os.environ.setdefault("ENVIRONMENT", "test")

from app.database import Base
from app.main import app, lifespan

# Engine en mémoire partagé entre tous les tests d'une session
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
test_engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestSession = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(scope="session")
def event_loop():
    """Boucle asyncio partagée pour toute la session de tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
async def setup_database():
    """Crée les tables en mémoire une fois pour toute la session."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await test_engine.dispose()


@pytest.fixture
async def db() -> AsyncSession:
    """Session DB de test avec rollback automatique après chaque test."""
    async with TestSession() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def client() -> AsyncClient:
    """Client HTTP de test (ASGI transport, pas de réseau)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def admin_client(client: AsyncClient, db: AsyncSession):
    """Client authentifié en tant qu'admin de test."""
    from app.models.identity import Member, MemberStatus, MasonicGrade, LodgeFunction, MembershipType
    from app.models.identity import User
    from app.dependencies import hash_password

    # Créer un membre + user admin de test
    m = Member(
        first_name="Test", last_name="Admin",
        email="test@admin.fr",
        status=MemberStatus.ACTIVE,
        masonic_grade=MasonicGrade.MAITRE,
        lodge_function=LodgeFunction.VM,
        membership_type=MembershipType.APPARTENANCE,
        civility="F",
    )
    db.add(m)
    await db.flush()
    u = User(
        member_id=m.id,
        login="test.admin",
        password_hash=hash_password("TestPass123!"),
        is_admin=True, is_active=True,
    )
    db.add(u)
    await db.commit()

    # Login
    resp = await client.post("/auth/login",
                             data={"username": "test.admin", "password": "TestPass123!"},
                             follow_redirects=False)
    assert resp.status_code in (302, 303), f"Login failed: {resp.status_code}"
    client.cookies.update(resp.cookies)
    return client
