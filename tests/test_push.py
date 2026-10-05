"""Tests — service Web Push : send_push_to_subscription doit distinguer un
envoi réussi d'un abonnement mort et d'un échec de livraison (régression :
un rejet du service de push autre que 404/410 était auparavant compté comme
un envoi réussi, masquant les vraies pannes, notamment sur iOS/Safari)."""
from unittest.mock import MagicMock, patch

import pytest

from app.models.system import PushSubscription
from app.services.push import PushResult, _normalize_private_key, send_push_to_subscription


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


def test_normalize_private_key_strips_pem_envelope():
    """Régression réelle : pywebpush.webpush() route une clé passée en chaîne
    vers Vapid.from_string(), qui base64-décode la chaîne TELLE QUELLE — un
    bloc PEM complet (en-têtes "-----BEGIN/END-----" inclus) produit un DER
    invalide et l'erreur cryptography "ASN.1 parsing error: invalid length"
    (reproduit avec une vraie clé EC P-256 générée localement avant ce
    correctif). _normalize_private_key() doit retirer l'enveloppe PEM et ne
    garder que le corps base64url brut, quel que soit le format déjà stocké
    en .env (PEM complet avec \\n littéraux, ou déjà nu)."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization

    priv = ec.generate_private_key(ec.SECP256R1())
    pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    bare_body = "".join(line for line in pem.splitlines() if "-----" not in line)

    # Format tel que stocké en .env : une seule ligne, \n littéraux.
    env_style = pem.replace("\n", "\\n")
    assert _normalize_private_key(env_style) == bare_body

    # Doit rester stable si la clé est déjà stockée nue (sans enveloppe PEM).
    assert _normalize_private_key(bare_body) == bare_body


def test_normalized_key_loads_via_pywebpush_vapid_from_string():
    """Reproduit exactement le mécanisme cassé : pywebpush.webpush() route une
    clé-chaîne vers Vapid.from_string() (pas Vapid.from_pem()). Sans ce
    correctif, cet appel lève ValueError "Could not deserialize key data...
    ASN.1 parsing error: invalid length" dès qu'on lui passe un bloc PEM
    complet. Pas d'appel réseau ici : on teste uniquement le chargement de la
    clé, l'étape qui échouait avant même d'essayer d'envoyer quoi que ce soit."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    from pywebpush import Vapid

    priv = ec.generate_private_key(ec.SECP256R1())
    pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    env_style = pem.replace("\n", "\\n")

    normalized = _normalize_private_key(env_style)
    vv = Vapid.from_string(private_key=normalized)  # ne doit pas lever
    assert vv.private_key is not None
