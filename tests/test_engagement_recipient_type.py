"""Le tableau de bord d'engagement doit distinguer les emails/notifications
aux membres de ceux aux correspondants externes (programme, confirmation) —
jusqu'ici tout était mélangé dans un seul taux global, lui-même basé sur un
pixel qui n'existait que sur l'email d'accès (régression : ~0% d'ouverture
affiché sur la quasi-totalité des 2671 emails d'une vraie instance, alors
que la quasi-totalité n'avait structurellement aucun moyen de remonter une
ouverture)."""
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from conftest import TestSession

from app.models.system import EmailLog, EmailStatus
from app.services.email import _send_raw, create_pending_log

# app.services.email._journal / create_pending_log importent AsyncSessionLocal
# EN LOCAL (à l'appel), depuis app.database — pas depuis la session injectée
# par FastAPI. Sans ce patch, ces écritures iraient sur l'AsyncSessionLocal
# "de production" (un :memory: isolé, sans tables, cf. conftest.py), jamais
# sur celui que la fixture `db` relit ensuite.
def _patched_async_session_local():
    return patch("app.database.AsyncSessionLocal", TestSession)


@pytest.mark.asyncio
async def test_send_raw_creates_log_tagged_member(db: AsyncSession):
    with _patched_async_session_local(), patch("app.services.email.get_settings") as mock_settings:
        mock_settings.return_value.smtp_host = None  # SMTP non configuré → pas d'envoi réel
        mock_settings.return_value.portal_url = "https://portail.test"
        mock_settings.return_value.lodge_domain = "test.fr"
        await _send_raw(
            to="membre@test.fr", subject="Test", html="<p>Bonjour</p>", text="Bonjour",
            recipient_type="MEMBER",
        )

    log = (await db.execute(
        select(EmailLog).where(EmailLog.recipient == "membre@test.fr")
    )).scalars().first()
    assert log is not None
    assert log.recipient_type == "MEMBER"


@pytest.mark.asyncio
async def test_send_raw_creates_log_tagged_external(db: AsyncSession):
    with _patched_async_session_local(), patch("app.services.email.get_settings") as mock_settings:
        mock_settings.return_value.smtp_host = None
        mock_settings.return_value.portal_url = "https://portail.test"
        mock_settings.return_value.lodge_domain = "test.fr"
        await _send_raw(
            to="externe@test.fr", subject="Programme", html="<p>Bonjour</p>", text="Bonjour",
            recipient_type="EXTERNAL",
        )

    log = (await db.execute(
        select(EmailLog).where(EmailLog.recipient == "externe@test.fr")
    )).scalars().first()
    assert log is not None
    assert log.recipient_type == "EXTERNAL"


@pytest.mark.asyncio
async def test_send_raw_untyped_diagnostic_email_stays_untagged(db: AsyncSession):
    """Un email de test/diagnostic (track=False, pas de recipient_type) ne
    doit jamais apparaître dans les statistiques Membres/Externes."""
    with _patched_async_session_local(), patch("app.services.email.get_settings") as mock_settings:
        mock_settings.return_value.smtp_host = None
        mock_settings.return_value.portal_url = "https://portail.test"
        mock_settings.return_value.lodge_domain = "test.fr"
        await _send_raw(
            to="diagnostic@test.fr", subject="Test SMTP", html="<p>x</p>", text="x",
            track=False,
        )

    log = (await db.execute(
        select(EmailLog).where(EmailLog.recipient == "diagnostic@test.fr")
    )).scalars().first()
    assert log is not None
    assert log.recipient_type is None


@pytest.mark.asyncio
async def test_open_and_click_pixel_update_email_log(client: AsyncClient, db: AsyncSession):
    """Les routes /access/open/ et /access/click/ sont désormais génériques
    (tout EmailLog, pas seulement l'email d'accès) — vérifie qu'elles
    fonctionnent toujours via une vraie requête HTTP."""
    from app.services.member_access import make_access_track_token

    log = EmailLog(recipient="x@test.fr", subject="s", status=EmailStatus.SENT, recipient_type="MEMBER")
    db.add(log)
    await db.commit()
    await db.refresh(log)

    open_token = make_access_track_token(log.id, "o")
    resp = await client.get(f"/access/open/{open_token}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/gif"

    await db.refresh(log)
    assert log.opened_at is not None

    click_token = make_access_track_token(log.id, "c")
    resp = await client.get(f"/access/click/{click_token}", params={"url": "https://example.com/page"},
                             follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://example.com/page"

    await db.refresh(log)
    assert log.clicked_at is not None


@pytest.mark.asyncio
async def test_engagement_dashboard_splits_member_and_external(admin_client: AsyncClient, db: AsyncSession):
    """Vérifie par delta avant/après, pas par un pourcentage figé dans le
    texte : d'autres tests de ce fichier journalisent aussi des EmailLog
    MEMBER/EXTERNAL via le même moteur partagé (commits qui persistent
    réellement entre tests, cf. conftest.py) — un pourcentage absolu serait
    fragile selon l'ordre d'exécution."""

    async def _snapshot():
        r = await db.execute(select(EmailLog).where(EmailLog.recipient_type == "MEMBER"))
        m = r.scalars().all()
        r = await db.execute(select(EmailLog).where(EmailLog.recipient_type == "EXTERNAL"))
        e = r.scalars().all()
        return (
            sum(1 for l in m if l.status == EmailStatus.SENT),
            sum(1 for l in m if l.opened_at is not None),
            sum(1 for l in e if l.status == EmailStatus.SENT),
            sum(1 for l in e if l.opened_at is not None),
        )

    before_m_sent, before_m_opened, before_e_sent, before_e_opened = await _snapshot()

    now = datetime.utcnow()
    db.add_all([
        EmailLog(recipient="m1@test.fr", subject="s", status=EmailStatus.SENT,
                 recipient_type="MEMBER", opened_at=now, created_at=now),
        EmailLog(recipient="m2@test.fr", subject="s", status=EmailStatus.SENT,
                 recipient_type="MEMBER", created_at=now),
        EmailLog(recipient="e1@test.fr", subject="s", status=EmailStatus.SENT,
                 recipient_type="EXTERNAL", opened_at=now, clicked_at=now, created_at=now),
        EmailLog(recipient="diag@test.fr", subject="s", status=EmailStatus.SENT,
                 recipient_type=None, created_at=now),
    ])
    await db.commit()

    after_m_sent, after_m_opened, after_e_sent, after_e_opened = await _snapshot()
    assert after_m_sent - before_m_sent == 2
    assert after_m_opened - before_m_opened == 1
    assert after_e_sent - before_e_sent == 1
    assert after_e_opened - before_e_opened == 1

    resp = await admin_client.get("/engagement/?days=90")
    assert resp.status_code == 200, resp.text
    text = resp.text
    assert "Membres" in text
    assert "Externes" in text
