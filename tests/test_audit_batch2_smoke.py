"""Vérifie que les appels log_audit() ajoutés au module groups ne font pas
planter les routes (import + appel direct du routeur, DB réelle in-memory).
Un seul module testé en profondeur pour ce lot (calendar/planches/programs
suivent exactement le même patron déjà validé sur bookmarks/news/
announcements/lodges_directory et sur groups ici)."""
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import (
    Member, MemberStatus, MasonicGrade, LodgeFunction, MembershipType, User,
)
from app.models.system import AuditLog
from app.routers import groups as groups_router


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


async def _max_audit_id(db: AsyncSession) -> int:
    return (await db.execute(select(func.max(AuditLog.id)))).scalar() or 0


async def _last_action(db: AsyncSession, after_id: int) -> str:
    r = await db.execute(select(AuditLog.action).where(AuditLog.id > after_id).order_by(AuditLog.id.desc()).limit(1))
    return r.scalar_one()


@pytest.mark.asyncio
async def test_group_create_edit_add_member_remove_member_delete_logged(db: AsyncSession):
    user, member = await _admin_ctx(db, "b2group")
    other = Member(
        first_name="Autre", last_name="Membre", email="autre@test.fr",
        status=MemberStatus.ACTIVE, masonic_grade=MasonicGrade.MAITRE,
        lodge_function=LodgeFunction.FRERE, membership_type=MembershipType.APPARTENANCE,
        civility="F",
    )
    db.add(other)
    await db.flush()

    class _FakeRequest:
        headers = {}
        client = None

    after_id = await _max_audit_id(db)
    resp = await groups_router.create_group(
        request=_FakeRequest(), ctx=(user, member), db=db,
        name="Groupe Test", description="", color="#3b5bdb", group_type="CUSTOM",
    )
    assert resp.status_code == 303
    assert await _last_action(db, after_id) == "GROUP_CREATE"
    group_id = (await db.execute(select(AuditLog.resource_id).where(AuditLog.id > after_id))).scalar_one()

    after_id = await _max_audit_id(db)
    await groups_router.add_member(group_id, request=_FakeRequest(), ctx=(user, member), db=db, member_id=other.id)
    assert await _last_action(db, after_id) == "GROUP_ADD_MEMBER"

    after_id = await _max_audit_id(db)
    await groups_router.edit_group(
        group_id, request=_FakeRequest(), ctx=(user, member), db=db,
        name="Groupe Test modifié", description="", color="#3b5bdb",
    )
    assert await _last_action(db, after_id) == "GROUP_EDIT"

    after_id = await _max_audit_id(db)
    await groups_router.remove_member(group_id, request=_FakeRequest(), ctx=(user, member), db=db, member_id=other.id)
    assert await _last_action(db, after_id) == "GROUP_REMOVE_MEMBER"

    after_id = await _max_audit_id(db)
    await groups_router.delete_group(group_id, request=_FakeRequest(), ctx=(user, member), db=db)
    assert await _last_action(db, after_id) == "GROUP_DELETE"
