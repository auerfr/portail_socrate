"""Envoi des accès portail aux membres : création de compte, génération du
guide utilisateur (PDF) et envoi de l'email avec pièce jointe.
"""
import asyncio
import logging
import secrets
import unicodedata
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models.identity import Member, MemberStatus, User
from app.models.system import EmailLog
from app.services.email import _send_raw

logger = logging.getLogger(__name__)

# Sujet des emails d'accès — sert aussi à retrouver leur statut dans EmailLog.
ACCESS_EMAIL_SUBJECT = "Vos accès au portail de la loge"

# Délai entre deux envois lors d'un envoi groupé (mêmes précautions que
# app/services/mailing.py — ne pas saturer le serveur SMTP).
ACCESS_EMAIL_DELAY_MS = 300

_RUNNING_TASKS: set = set()


def launch_bulk_access_send() -> None:
    """Démarre l'envoi groupé en tâche de fond (fire-and-forget)."""
    task = asyncio.ensure_future(run_bulk_access_send())
    _RUNNING_TASKS.add(task)
    task.add_done_callback(_RUNNING_TASKS.discard)


async def generate_unique_login(db, member: Member) -> str:
    """Génère un identifiant unique prenom.nom (sans accents) pour ce membre."""
    base_login = f"{member.first_name}.{member.last_name}".lower().replace(" ", "").replace("'", "")
    base_login = "".join(
        c for c in unicodedata.normalize("NFKD", base_login)
        if not unicodedata.combining(c)
    )
    login = base_login
    n = 2
    while (await db.execute(select(User).where(User.login == login))).scalar_one_or_none():
        login = f"{base_login}{n}"
        n += 1
    return login


async def ensure_user_account(db, member: Member) -> User:
    """Retourne le User du membre, en le créant si nécessaire (mot de passe
    placeholder inutilisable, à définir via le lien de réinitialisation)."""
    user = (await db.execute(select(User).where(User.member_id == member.id))).scalar_one_or_none()
    if user:
        return user

    from passlib.context import CryptContext
    pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    login = await generate_unique_login(db, member)
    user = User(
        member_id=member.id,
        login=login,
        password_hash=pwd_ctx.hash(secrets.token_urlsafe(32)),
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    await db.flush()
    return user


# ── Guide utilisateur (PDF) ────────────────────────────────────────────────

def build_guide_pdf(lodge_name: str, portal_url: str) -> bytes:
    """Génère le guide utilisateur en PDF (reportlab, mêmes conventions que
    les autres exports du portail — cf. app/routers/meetings.py)."""
    import io
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, ListFlowable, ListItem, HRFlowable,
    )

    teal = colors.HexColor("#2c7a7b")
    amber_bg = colors.HexColor("#fffbeb")
    amber_border = colors.HexColor("#f59e0b")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2.5 * cm, rightMargin=2.5 * cm,
        topMargin=2.5 * cm, bottomMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("H1", parent=styles["Heading1"], textColor=teal, fontSize=18, spaceAfter=4)
    subtitle = ParagraphStyle("Subtitle", parent=styles["Normal"], fontSize=11, textColor=colors.HexColor("#6b7280"), spaceAfter=16)
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], textColor=teal, fontSize=13, spaceBefore=14, spaceAfter=6)
    normal = ParagraphStyle("N", parent=styles["Normal"], fontSize=10, leading=15, alignment=TA_LEFT)
    note = ParagraphStyle("Note", parent=normal, fontSize=10, leading=15, textColor=colors.HexColor("#78350f"))

    def box(title: str, text: str):
        t = ParagraphStyle("BoxTitle", parent=normal, fontName="Helvetica-Bold", textColor=colors.HexColor("#92400e"))
        from reportlab.platypus import Table, TableStyle
        tbl = Table(
            [[Paragraph(f"Important — {title}", t)], [Paragraph(text, note)]],
            colWidths=[16 * cm],
        )
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), amber_bg),
            ("BOX", (0, 0), (-1, -1), 1, amber_border),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        return tbl

    elems = [
        Paragraph(f"Guide d'utilisation — {lodge_name}", h1),
        Paragraph("Portail des membres — prise en main rapide", subtitle),

        Paragraph("Premiers pas", h2),
        ListFlowable([
            ListItem(Paragraph(
                f"<b>Se connecter</b> — rendez-vous sur <a href='{portal_url}/auth/login' color='#2c7a7b'>{portal_url}</a>, "
                "saisissez votre identifiant et votre mot de passe. Si vous recevez cet accès pour la première fois, "
                "cliquez sur le lien fourni dans l'email pour choisir votre mot de passe avant de vous connecter.",
                normal,
            )),
            ListItem(Paragraph(
                "<b>Vérifier votre profil</b> — depuis le menu profil, vous pouvez changer votre mot de passe "
                "et votre identifiant à tout moment.",
                normal,
            )),
            ListItem(Paragraph(
                "<b>Régler vos notifications</b> — dans Paramètres, choisissez si vous souhaitez recevoir un "
                "email à chaque message reçu sur le portail.",
                normal,
            )),
            ListItem(Paragraph(
                "<b>Explorer le menu</b> — la barre latérale liste les rubriques auxquelles vous avez accès ; "
                "certaines dépendent de votre grade ou de votre fonction (voir ci-dessous).",
                normal,
            )),
        ], bulletType="1", start="1"),
        Spacer(1, 0.3 * cm),

        Paragraph("Ce que vous pouvez faire sur le portail", h2),
        Paragraph(
            "Le portail regroupe les outils utiles à la vie de la loge. Selon votre grade "
            "(Apprenti, Compagnon, Maître) et votre fonction (officier, membre d'une commission…), "
            "certaines rubriques peuvent ne pas apparaître dans votre menu — c'est normal, elles sont "
            f"réservées à certains rôles. Si vous occupez une fonction particulière (Trésorier, Secrétaire, "
            f"Maître des Banquets, Vénérable Maître), une FAQ dédiée sur <a href='{portal_url}/faq' color='#2c7a7b'>{portal_url}/faq</a> "
            "détaille les outils qui vous concernent.",
            normal,
        ),
        Spacer(1, 0.15 * cm),
        ListFlowable([
            ListItem(Paragraph("<b>Tableau de bord</b> — actualités et informations de la loge.", normal)),
            ListItem(Paragraph("<b>Messagerie</b> — messages internes entre membres.", normal)),
            ListItem(Paragraph("<b>Documents / Bibliothèque</b> — planches, comptes-rendus, archives de la loge, et un espace personnel pour vos propres fichiers.", normal)),
            ListItem(Paragraph("<b>Calendrier</b> — tenues et événements de la loge, avec export vers votre agenda personnel.", normal)),
            ListItem(Paragraph("<b>Forum</b> — échanges par thème et par sujet entre membres.", normal)),
            ListItem(Paragraph("<b>Planches</b> — rédigez, déposez et commentez vos planches.", normal)),
            ListItem(Paragraph("<b>Annuaire des membres</b> — coordonnées et fiches des frères.", normal)),
            ListItem(Paragraph("<b>Ma cotisation</b> — choisissez votre tranche de cotisation quand l'appel est ouvert, et suivez votre statut de paiement.", normal)),
            ListItem(Paragraph("<b>Chat</b> — discussion instantanée entre membres.", normal)),
            ListItem(Paragraph("<b>Sondages</b> — participez aux votes et consultations de la loge.", normal)),
            ListItem(Paragraph("<b>Projets</b> — suivi des tâches des projets auxquels vous participez.", normal)),
            ListItem(Paragraph("<b>Liens partagés</b> et <b>Anniversaires</b> — ressources utiles et rappels des anniversaires (d'âge et maçonniques).", normal)),
            ListItem(Paragraph(
                "<b>Rubriques réservées</b> (officiers, présences, trésorerie, diffusion, annonces, administration…) — "
                "visibles uniquement par les frères occupant la fonction correspondante.",
                normal,
            )),
        ], bulletType="bullet", start="•"),
        Spacer(1, 0.3 * cm),

        Paragraph("En cas de souci", h2),
        ListFlowable([
            ListItem(Paragraph(
                f"<b>Mot de passe oublié</b> — utilisez <a href='{portal_url}/auth/reset-password' color='#2c7a7b'>{portal_url}/auth/reset-password</a> "
                "pour recevoir un lien de réinitialisation par email.",
                normal,
            )),
            ListItem(Paragraph("<b>Vous ne recevez pas nos emails</b> — pensez à vérifier votre dossier spams/courrier indésirable.", normal)),
            ListItem(Paragraph(
                f"<b>Une rubrique semble manquante</b> — c'est normal si elle dépend de votre grade ou de votre "
                f"fonction ; consultez la FAQ sur <a href='{portal_url}/faq' color='#2c7a7b'>{portal_url}/faq</a> pour vérifier ce qui vous concerne.",
                normal,
            )),
            ListItem(Paragraph("<b>Autre difficulté</b> — contactez le webmestre ou un membre du bureau, ils pourront vous accompagner ou réinitialiser votre accès.", normal)),
        ], bulletType="bullet", start="•"),
        Spacer(1, 0.3 * cm),

        box(
            "Le bureau affiché est celui de 2025-2026",
            "Les officiers actuellement affichés sur le portail correspondent au bureau de "
            "l'année maçonnique 2025-2026. Ce bureau sera mis à jour en septembre, lors de "
            "l'installation du nouveau bureau.",
        ),
        Spacer(1, 0.3 * cm),

        box(
            "Une formation aux outils sera organisée",
            "Une formation à l'utilisation du portail sera organisée par visioconférence, avec "
            "plusieurs créneaux proposés au choix pour s'adapter aux disponibilités de chacun. "
            "Les dates seront communiquées prochainement.",
        ),
        Spacer(1, 0.4 * cm),

        HRFlowable(width="100%", color=colors.HexColor("#e5e7eb"), thickness=0.5),
        Spacer(1, 0.2 * cm),
        Paragraph(
            f"Ce guide reste consultable à tout moment sur <a href='{portal_url}/guide' color='#2c7a7b'>{portal_url}/guide</a>, "
            f"et la FAQ par fonction sur <a href='{portal_url}/faq' color='#2c7a7b'>{portal_url}/faq</a>.",
            ParagraphStyle("Foot", parent=normal, textColor=colors.HexColor("#6b7280"), fontSize=9),
        ),
    ]

    doc.build(elems)
    buf.seek(0)
    return buf.read()


# ── Email d'accès ───────────────────────────────────────────────────────────

def _access_email_content(member: Member, user: User, token: str, portal_url: str, lodge_name: str) -> tuple[str, str]:
    reset_url = f"{portal_url}/auth/reset-password/{token}"
    guide_url = f"{portal_url}/guide"

    text = f"""Bonjour {member.first_name},

Un accès au portail de la loge {lodge_name} a été créé (ou renouvelé) pour vous.

Identifiant : {user.login}

Définissez votre mot de passe ici (lien valable 7 jours) :
{reset_url}

Si vous avez déjà un mot de passe qui fonctionne, vous pouvez l'ignorer et vous connecter
directement sur {portal_url}/auth/login.

Vous trouverez en pièce jointe un petit guide d'utilisation du portail (également consultable
en ligne : {guide_url}). Il précise notamment que le bureau affiché est celui de l'année
2025-2026 (mise à jour en septembre lors de l'installation) et qu'une formation aux outils
sera prochainement organisée par visioconférence, avec plusieurs créneaux au choix.

Cordialement,
L'administration du Portail {lodge_name}"""

    html = f"""<p>Bonjour {member.first_name},</p>
<p>Un accès au portail de la loge <strong>{lodge_name}</strong> a été créé (ou renouvelé) pour vous.</p>
<table style="border:1px solid #ddd;padding:12px;border-radius:8px;background:#f9f9f9">
  <tr><td><strong>Identifiant</strong></td><td>{user.login}</td></tr>
</table>
<p><a href="{reset_url}" style="background:#2c7a7b;color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none;display:inline-block;margin:12px 0">
  Définir mon mot de passe →
</a></p>
<p style="color:#6b7280;font-size:13px;">Ce lien est valable 7 jours. Si vous avez déjà un mot de passe qui fonctionne,
vous pouvez l'ignorer et vous connecter directement sur <a href="{portal_url}/auth/login">{portal_url}/auth/login</a>.</p>
<p>Vous trouverez ci-joint un petit <strong>guide d'utilisation du portail</strong> (également consultable en ligne
sur <a href="{guide_url}">{guide_url}</a>). Il précise notamment que le bureau affiché est celui de l'année
<strong>2025-2026</strong> (mise à jour en septembre lors de l'installation) et qu'une <strong>formation aux outils</strong>
sera prochainement organisée par visioconférence, avec plusieurs créneaux au choix.</p>
<hr><p style="color:#888;font-size:12px">Portail {lodge_name}</p>"""

    return html, text


async def send_access_email(db, member: Member, portal_url: str, guide_pdf: bytes) -> tuple[bool, Optional[str]]:
    """Assure le compte, régénère un token de réinitialisation (7j) et envoie
    l'email d'accès avec le guide en pièce jointe."""
    if not member.email:
        return False, "Membre sans adresse email"

    settings = get_settings()
    user = await ensure_user_account(db, member)
    user.reset_token = secrets.token_urlsafe(32)
    user.reset_token_expires = datetime.utcnow() + timedelta(days=7)
    await db.commit()

    html, text = _access_email_content(member, user, user.reset_token, portal_url, settings.lodge_name)
    ok, err = await _send_raw(
        to=member.email,
        subject=f"[{settings.lodge_name}] {ACCESS_EMAIL_SUBJECT}",
        html=html,
        text=text,
        attachments=[("guide-utilisateur-portail.pdf", guide_pdf, "application/pdf")],
    )
    return ok, err


async def run_bulk_access_send(portal_url: Optional[str] = None) -> None:
    """Envoie l'accès à tous les membres actifs, en tâche de fond (sa propre
    session DB, calqué sur app.services.mailing.send_campaign_async)."""
    settings = get_settings()
    base_url = (portal_url or settings.portal_url or "https://portail.amisdesocrate.fr").rstrip("/")
    guide_pdf = build_guide_pdf(settings.lodge_name, base_url)

    async with AsyncSessionLocal() as db:
        members = (await db.execute(
            select(Member).where(Member.status == MemberStatus.ACTIVE).order_by(Member.last_name)
        )).scalars().all()

        for member in members:
            try:
                await send_access_email(db, member, base_url, guide_pdf)
            except Exception:
                logger.exception("Échec envoi accès portail → membre %s", member.id)
            await asyncio.sleep(ACCESS_EMAIL_DELAY_MS / 1000.0)


async def last_send_status(db, emails: list[str]) -> dict[str, EmailLog]:
    """Dernier EmailLog (envoi d'accès) par adresse email, pour l'écran de vérification."""
    if not emails:
        return {}
    rows = (await db.execute(
        select(EmailLog)
        .where(EmailLog.recipient.in_(emails), EmailLog.subject.like(f"%{ACCESS_EMAIL_SUBJECT}%"))
        .order_by(EmailLog.created_at.desc())
    )).scalars().all()
    out: dict[str, EmailLog] = {}
    for row in rows:
        out.setdefault(row.recipient, row)
    return out
