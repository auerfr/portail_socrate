"""Vérifie que les appels log_audit() ajoutés aux modules bookmarks, news,
announcements et lodges_directory ne font pas planter les routes (import +
appel direct du routeur, DB réelle in-memory) et écrivent bien une entrée."""
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import (
    Member, MemberStatus, MasonicGrade, LodgeFunction, MembershipType, User,
)
from app.models.system import AuditLog
from app.routers import bookmarks as bookmarks_router
from app.routers import news as news_router
from app.routers import announcements as announcements_router
from app.routers import lodges_directory as lodges_router


async def _admin_ctx(db: AsyncSession, login: str) -> tuple[User, Member]:
    m = Member(
        first_name="Test", last_name=login.capitalize(),
        email=f"{login}@test.fr",
        status=MemberStatus.ACTIVE,
        masonic_grade=MasonicGrade.MAITRE,
        lodge_function=LodgeFunction.VM,
        membership_type=MembershipType.APPARTENANCE,
        civility="F",
    )
    db.add(m)
    await db.flush()
    u = User(member_id=m.id, login=login, password_hash="x", is_admin=True, is_active=True)
    db.add(u)
    await db.flush()
    return (u, m)


async def _last_action(db: AsyncSession, after_id: int) -> str:
    r = await db.execute(select(AuditLog.action).where(AuditLog.id > after_id).order_by(AuditLog.id.desc()).limit(1))
    return r.scalar_one()


async def _max_audit_id(db: AsyncSession) -> int:
    from sqlalchemy import func
    return (await db.execute(select(func.max(AuditLog.id)))).scalar() or 0


@pytest.mark.asyncio
async def test_bookmark_create_and_delete_logged(db: AsyncSession):
    user, member = await _admin_ctx(db, "b1admin")
    after_id = await _max_audit_id(db)

    resp = await bookmarks_router.bookmark_create(
        request=None, ctx=(user, member), db=db,
        url="example.com", title="Exemple", description="", tags="",
    )
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "BOOKMARK_CREATE"

    bk_id = (await db.execute(select(AuditLog.resource_id).where(AuditLog.id > after_id))).scalar_one()
    after_id = await _max_audit_id(db)
    await bookmarks_router.bookmark_delete(bk_id, request=None, ctx=(user, member), db=db)
    assert await _last_action(db, after_id) == "BOOKMARK_DELETE"


@pytest.mark.asyncio
async def test_news_create_edit_delete_logged(db: AsyncSession):
    user, member = await _admin_ctx(db, "b1news")
    after_id = await _max_audit_id(db)

    resp = await news_router.news_create(
        request=None, ctx=(user, member), db=db,
        title="Actu test", content="contenu", is_featured="", is_online="on",
        target="", publish_until="", notify_members="",
    )
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "NEWS_CREATE"

    article_id = (await db.execute(select(AuditLog.resource_id).where(AuditLog.id > after_id))).scalar_one()

    after_id = await _max_audit_id(db)
    await news_router.news_update(
        article_id, request=None, ctx=(user, member), db=db,
        title="Actu modifiée", content="contenu2", is_featured="", is_online="on",
        target="", publish_until="",
    )
    assert await _last_action(db, after_id) == "NEWS_EDIT"

    after_id = await _max_audit_id(db)
    await news_router.news_delete(article_id, request=None, ctx=(user, member), db=db)
    assert await _last_action(db, after_id) == "NEWS_DELETE"


@pytest.mark.asyncio
async def test_announcement_create_pin_delete_logged(db: AsyncSession):
    user, member = await _admin_ctx(db, "b1ann")
    after_id = await _max_audit_id(db)

    resp = await announcements_router.create_announcement(
        request=None, ctx=(user, member), db=db,
        title="Annonce test", content="contenu", is_pinned=False, expires_at="",
    )
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "ANNOUNCEMENT_CREATE"

    ann_id = (await db.execute(select(AuditLog.resource_id).where(AuditLog.id > after_id))).scalar_one()

    after_id = await _max_audit_id(db)
    await announcements_router.toggle_pin(ann_id, request=None, ctx=(user, member), db=db)
    assert await _last_action(db, after_id) == "ANNOUNCEMENT_PIN"

    after_id = await _max_audit_id(db)
    await announcements_router.delete_announcement(ann_id, request=None, ctx=(user, member), db=db)
    assert await _last_action(db, after_id) == "ANNOUNCEMENT_DELETE"


@pytest.mark.asyncio
async def test_neighboring_lodge_create_edit_toggle_delete_logged(db: AsyncSession):
    user, member = await _admin_ctx(db, "b1lodge")
    after_id = await _max_audit_id(db)

    class _FakeForm:
        def getlist(self, key):
            return []

    class _FakeRequest:
        async def form(self):
            return _FakeForm()

        headers = {}
        client = None

    resp = await lodges_router.lodge_create(
        request=_FakeRequest(), ctx=(user, member), db=db,
        orient="Paris", region="", name="Loge Test", rite="", obedience="",
        address="", latitude="", longitude="", meeting_time="", notes="", active=False,
    )
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "NEIGHBORING_LODGE_CREATE"

    lodge_id = (await db.execute(select(AuditLog.resource_id).where(AuditLog.id > after_id))).scalar_one()

    after_id = await _max_audit_id(db)
    await lodges_router.lodge_edit(
        lodge_id, request=_FakeRequest(), ctx=(user, member), db=db,
        orient="Paris", region="", name="Loge Test modifiée", rite="", obedience="",
        address="", latitude="", longitude="", meeting_time="", notes="", active=False,
    )
    assert await _last_action(db, after_id) == "NEIGHBORING_LODGE_EDIT"

    after_id = await _max_audit_id(db)
    await lodges_router.lodge_toggle_active(lodge_id, request=_FakeRequest(), ctx=(user, member), db=db)
    assert await _last_action(db, after_id) == "NEIGHBORING_LODGE_ACTIVATE"

    after_id = await _max_audit_id(db)
    await lodges_router.lodge_delete(lodge_id, request=_FakeRequest(), ctx=(user, member), db=db)
    assert await _last_action(db, after_id) == "NEIGHBORING_LODGE_DELETE"
