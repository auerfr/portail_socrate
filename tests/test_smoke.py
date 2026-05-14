"""Tests smoke — vérifications rapides que l'app démarre et les routes existent."""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_app_imports():
    """L'application se charge sans erreur d'import."""
    from app.main import app
    assert app is not None


@pytest.mark.asyncio
async def test_routes_exist():
    """Les routes clés sont enregistrées."""
    from app.main import app
    paths = {r.path for r in app.routes if hasattr(r, "path")}
    required = [
        "/", "/auth/login", "/members/",
        "/meetings/", "/documents/",
        "/finance/", "/projects/",
        "/mailing/", "/bookmarks/",
        "/admin/", "/admin/users", "/admin/audit",
        "/admin/data", "/admin/comm", "/admin/sessions",
        "/admin/permissions", "/admin/email-templates",
        "/auth/2fa/setup",
    ]
    missing = [p for p in required if p not in paths]
    assert not missing, f"Routes manquantes : {missing}"


@pytest.mark.asyncio
async def test_login_page(client: AsyncClient):
    """La page de login répond 200."""
    resp = await client.get("/auth/login")
    assert resp.status_code == 200
    assert "login" in resp.text.lower() or "connexion" in resp.text.lower()


@pytest.mark.asyncio
async def test_unauthenticated_redirect(client: AsyncClient):
    """Les pages protégées redirigent vers /auth/login."""
    for path in ["/members/", "/meetings/", "/admin/"]:
        resp = await client.get(path, follow_redirects=False)
        assert resp.status_code in (302, 303, 401), \
            f"{path} devrait rediriger (got {resp.status_code})"


@pytest.mark.asyncio
async def test_static_assets(client: AsyncClient):
    """Les assets statiques clés sont servis."""
    for url in ["/static/manifest.json", "/static/sw.js",
                "/static/img/icon-192.png"]:
        resp = await client.get(url)
        assert resp.status_code == 200, f"{url} → {resp.status_code}"


@pytest.mark.asyncio
async def test_tracking_endpoints_no_crash(client: AsyncClient):
    """Les endpoints tracking répondent même avec un token invalide."""
    resp = await client.get("/mailing/track/open/invalid_token")
    # Doit retourner le GIF ou une erreur gracieuse, pas un 500
    assert resp.status_code in (200, 400, 404)

    resp2 = await client.get("/mailing/track/click/invalid_token?url=https://example.com")
    assert resp2.status_code in (200, 302, 400, 404)
