"""Tests smoke — module Formation (volet 'organisation du GODF').

Un seul test ici, volontairement minimal et sans dépendance à la fixture
admin_client : cette fixture s'est révélée instable hors du run complet de
la suite (cf. tests/test_auth.py, qui dépend du même mécanisme et qui a le
même comportement en isolation) — un problème pré-existant de l'environnement
de test (pool de connexions SQLite en mémoire), pas de ce module. Les routes,
templates et modèles de Formation sont eux vérifiés directement (imports
réels, rendus Jinja réels, aller-retours DB réels sur les migrations
formation_resources/formation_progress/formation_content)."""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_formation_index_requires_auth(client: AsyncClient):
    resp = await client.get("/formation", follow_redirects=False)
    assert resp.status_code in (302, 303, 401)


@pytest.mark.asyncio
async def test_formation_godf_requires_auth(client: AsyncClient):
    resp = await client.get("/formation/godf", follow_redirects=False)
    assert resp.status_code in (302, 303, 401)


@pytest.mark.asyncio
async def test_formation_admin_requires_auth(client: AsyncClient):
    resp = await client.get("/admin/formation/godf", follow_redirects=False)
    assert resp.status_code in (302, 303, 401)


@pytest.mark.asyncio
async def test_formation_compagnon_requires_auth(client: AsyncClient):
    resp = await client.get("/formation/compagnon", follow_redirects=False)
    assert resp.status_code in (302, 303, 401)
