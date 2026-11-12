"""Les actions créer/modifier/clôturer/supprimer un sondage doivent être
journalisées dans AuditLog — avant ce correctif, le module Sondages n'écrivait
jamais dans ce journal, rendant impossible de savoir qui avait supprimé un
sondage (cf. incident du sondage de disponibilités disparu sans trace).

N'utilise PAS la fixture `admin_client` (passe par /auth/login) : elle est
documentée comme instable hors du run complet de la suite, cf. l'en-tête de
tests/test_formation.py — même constat ici. On appelle directement les
fonctions du routeur avec un ctx (user, member) construit à la main, sur la
session `db` (fiable, utilisée partout ailleurs dans la suite)."""
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import Poll
from app.models.identity import (
    Member, MemberStatus, MasonicGrade, LodgeFunction, MembershipType, User,
)
from app.models.system import AuditLog
from app.routers import polls as polls_router


async def _make_ctx(db: AsyncSession, login: str) -> tuple[User, Member]:
    m = Member(
        first_name="Test", last_name=login.capitalize(),
        email=f"{login}@test.fr",
        status=MemberStatus.ACTIVE,
        masonic_grade=MasonicGrade.MAITRE,
        lodge_function=LodgeFunction.FRERE,
        membership_type=MembershipType.APPARTENANCE,
        civility="F",
    )
    db.add(m)
    await db.flush()
    u = User(member_id=m.id, login=login, password_hash="x", is_admin=False, is_active=True)
    db.add(u)
    await db.flush()
    return (u, m)


async def _max_audit_id(db: AsyncSession) -> int:
    return (await db.execute(select(func.max(AuditLog.id)))).scalar() or 0


async def _audit_actions_for(db: AsyncSession, poll_id: int, after_id: int) -> list[str]:
    """Filtré aussi par `after_id` (watermark AuditLog.id capturé avant ce
    test) : Poll.id n'a pas d'AUTOINCREMENT, donc SQLite réattribue l'id
    d'une ligne supprimée au prochain INSERT — un poll_id seul peut
    correspondre au sondage d'un AUTRE test déjà supprimé. created_at ne
    suffit pas non plus comme filtre temporel : server_default=func.now()
    a une résolution à la seconde sous SQLite, bien plus grossière que la
    durée de toute la suite de tests."""
    r = await db.execute(
        select(AuditLog.action)
        .where(
            AuditLog.resource_type == "poll",
            AuditLog.resource_id == poll_id,
            AuditLog.id > after_id,
        )
        .order_by(AuditLog.id)
    )
    return [a for (a,) in r.all()]


@pytest.mark.asyncio
async def test_poll_create_is_logged(db: AsyncSession):
    user, member = await _make_ctx(db, "auditcreator1")
    after_id = await _max_audit_id(db)

    resp = await polls_router.polls_create(
        request=None, ctx=(user, member), db=db,
        title="Dispos répétitions", description="", options_raw="Samedi\nDimanche",
        is_multiple="", is_anonymous="", is_public_vote="",
        target="", ends_at="", notify_members="",
        vote_type="CHOICE", rating_winners="3", member_ids="",
    )
    poll_id = int(resp.headers["location"].rstrip("/").rsplit("/", 1)[-1])

    assert await _audit_actions_for(db, poll_id, after_id) == ["POLL_CREATE"]
    entry = (await db.execute(
        select(AuditLog).where(
            AuditLog.resource_type == "poll", AuditLog.resource_id == poll_id,
            AuditLog.id > after_id,
        )
    )).scalar_one()
    assert entry.target_label == "Dispos répétitions"
    assert entry.actor_id == member.id


@pytest.mark.asyncio
async def test_poll_edit_close_delete_are_logged_in_order(db: AsyncSession):
    user, member = await _make_ctx(db, "auditcreator2")
    after_id = await _max_audit_id(db)

    resp = await polls_router.polls_create(
        request=None, ctx=(user, member), db=db,
        title="Titre initial", description="", options_raw="A\nB",
        is_multiple="", is_anonymous="", is_public_vote="",
        target="", ends_at="", notify_members="",
        vote_type="CHOICE", rating_winners="3", member_ids="",
    )
    poll_id = int(resp.headers["location"].rstrip("/").rsplit("/", 1)[-1])

    await polls_router.poll_edit(
        poll_id, request=None, ctx=(user, member), db=db,
        title="Titre modifié", description="", ends_at="",
    )
    await polls_router.poll_close(poll_id, request=None, ctx=(user, member), db=db)
    await polls_router.poll_delete(poll_id, request=None, ctx=(user, member), db=db)

    assert await _audit_actions_for(db, poll_id, after_id) == [
        "POLL_CREATE", "POLL_EDIT", "POLL_CLOSE", "POLL_DELETE",
    ]

    delete_entry = (await db.execute(
        select(AuditLog).where(
            AuditLog.resource_type == "poll",
            AuditLog.resource_id == poll_id,
            AuditLog.action == "POLL_DELETE",
            AuditLog.id > after_id,
        )
    )).scalar_one()
    assert delete_entry.target_label == "Titre modifié"
    assert delete_entry.details.get("votes") == 0
    assert delete_entry.details.get("created_by_id") == member.id

    # Le sondage est bien parti, mais sa trace survit.
    assert await db.get(Poll, poll_id) is None


@pytest.mark.asyncio
async def test_other_member_delete_attempt_is_not_logged(db: AsyncSession):
    """403 avant toute écriture : une tentative refusée ne doit PAS polluer
    le journal comme si elle avait réussi."""
    creator_user, creator = await _make_ctx(db, "auditcreator3")
    intruder_user, intruder = await _make_ctx(db, "auditintruder3")
    after_id = await _max_audit_id(db)

    resp = await polls_router.polls_create(
        request=None, ctx=(creator_user, creator), db=db,
        title="Sondage protégé", description="", options_raw="X\nY",
        is_multiple="", is_anonymous="", is_public_vote="",
        target="", ends_at="", notify_members="",
        vote_type="CHOICE", rating_winners="3", member_ids="",
    )
    poll_id = int(resp.headers["location"].rstrip("/").rsplit("/", 1)[-1])

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await polls_router.poll_delete(poll_id, request=None, ctx=(intruder_user, intruder), db=db)
    assert exc_info.value.status_code == 403

    assert await _audit_actions_for(db, poll_id, after_id) == ["POLL_CREATE"]
    assert await db.get(Poll, poll_id) is not None
