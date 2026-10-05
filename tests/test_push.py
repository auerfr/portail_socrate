"""Tests — service Web Push : send_push_to_subscription doit distinguer un
envoi réussi d'un abonnement mort et d'un échec de livraison (régression :
un rejet du service de push autre que 404/410 était auparavant compté comme
un envoi réussi, masquant les vraies pannes, notamment sur iOS/Safari)."""
from unittest.mock import MagicMock, patch

import pytest

from app.models.system import PushSubscription
from app.services.push import PushResult, send_push_to_subscription


def _fake_sub():
    return PushSubscription(
        id=1, member_id=1,
        endpoint="https://web.push.apple.com/fake",
        key_p256dh="p256dh", key_auth="auth",
    )


def _fake_settings():
    s = MagicMock()
    s.vapid_private_key = "fake-key"
    s.vapid_claim_email = "mailto:test@example.com"
    return s


@pytest.mark.asyncio
async def test_send_push_success():
    with patch("app.services.push.get_settings", return_value=_fake_settings()), \
         patch("pywebpush.webpush", return_value=None):
        result, reason = await send_push_to_subscription(_fake_sub(), "Titre", "Corps")
    assert result is PushResult.SENT
    assert reason == "OK"


@pytest.mark.asyncio
async def test_send_push_expired_subscription():
    from pywebpush import WebPushException

    resp = MagicMock()
    resp.status_code = 410
    resp.text = "gone"
    exc = WebPushException("gone")
    exc.response = resp

    with patch("app.services.push.get_settings", return_value=_fake_settings()), \
         patch("pywebpush.webpush", side_effect=exc):
        result, reason = await send_push_to_subscription(_fake_sub(), "Titre", "Corps")
    assert result is PushResult.EXPIRED
    assert "410" in reason


@pytest.mark.asyncio
async def test_send_push_delivery_failure_is_not_counted_as_sent():
    """Le cas qui causait le bug : une erreur VAPID/auth (403) rejetée par le
    service de push ne doit jamais apparaître comme un envoi réussi — et la
    vraie raison doit être renvoyée pour être affichée à l'utilisateur."""
    from pywebpush import WebPushException

    resp = MagicMock()
    resp.status_code = 403
    resp.text = "VAPID credentials rejected"
    exc = WebPushException("forbidden")
    exc.response = resp

    with patch("app.services.push.get_settings", return_value=_fake_settings()), \
         patch("pywebpush.webpush", side_effect=exc):
        result, reason = await send_push_to_subscription(_fake_sub(), "Titre", "Corps")
    assert result is PushResult.FAILED
    assert result is not PushResult.SENT
    assert "403" in reason
    assert "VAPID credentials rejected" in reason
