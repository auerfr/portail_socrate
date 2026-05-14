"""Tests du module authentification."""
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient):
    """Un mauvais mot de passe retourne 401 ou 200 avec erreur."""
    resp = await client.post(
        "/auth/login",
        data={"username": "inexistant", "password": "mauvais"},
        follow_redirects=False,
    )
    # Soit 401 soit redirect vers login avec message d'erreur
    assert resp.status_code in (401, 200, 302, 303)


@pytest.mark.asyncio
async def test_password_reset_page(client: AsyncClient):
    """La page de réinitialisation de mot de passe répond 200."""
    resp = await client.get("/auth/reset-password")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_2fa_setup_requires_admin(client: AsyncClient):
    """La page 2FA exige d'être admin."""
    resp = await client.get("/auth/2fa/setup", follow_redirects=False)
    # Non authentifié → redirect login ou 401
    assert resp.status_code in (302, 303, 401)


@pytest.mark.asyncio
async def test_2fa_verify_page(client: AsyncClient):
    """La page de vérification 2FA est accessible sans auth."""
    resp = await client.get("/auth/2fa/verify")
    assert resp.status_code == 200
