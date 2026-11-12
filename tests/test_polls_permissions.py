"""Un sondage ne doit pouvoir être modifié / clôturé / supprimé que par son
créateur ou un admin — pas par n'importe quel membre connecté (régression :
voir l'incident d'un sondage de disponibilités supprimé par un tiers alors
que _can_manage, délibérément large pour la création, gouvernait aussi ces
actions)."""
from types import SimpleNamespace

from app.routers.polls import _can_edit_poll


def _poll(created_by_id: int) -> SimpleNamespace:
    return SimpleNamespace(created_by_id=created_by_id)


def _member(member_id: int, fine_permissions=()) -> SimpleNamespace:
    return SimpleNamespace(id=member_id, _fine_permissions=fine_permissions)


def test_creator_can_edit_own_poll():
    poll = _poll(created_by_id=1)
    assert _can_edit_poll(poll, _member(1), is_admin=False) is True


def test_other_member_cannot_edit_someone_elses_poll():
    poll = _poll(created_by_id=1)
    assert _can_edit_poll(poll, _member(2), is_admin=False) is False


def test_admin_can_edit_any_poll():
    poll = _poll(created_by_id=1)
    assert _can_edit_poll(poll, _member(2), is_admin=True) is True


def test_delegated_permission_can_edit_any_poll():
    poll = _poll(created_by_id=1)
    delegate = _member(2, fine_permissions=("can_manage_polls",))
    assert _can_edit_poll(poll, delegate, is_admin=False) is True


def test_unrelated_fine_permission_does_not_grant_access():
    poll = _poll(created_by_id=1)
    other_delegate = _member(2, fine_permissions=("can_manage_meetings",))
    assert _can_edit_poll(poll, other_delegate, is_admin=False) is False
