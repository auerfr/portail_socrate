"""Service Listes de diffusion — résolution destinataires, rendu, envoi async."""
import asyncio
import hashlib
import hmac
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from html import escape as html_escape
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models.identity import Member, MemberStatus, MasonicGrade, LodgeFunction
from app.models.lodge import ExternalContact
from app.models.mailing import (
    MailingList, MailingListMember, MailingListExternal, MailingListType,
    MailingCampaign, MailingDelivery, CampaignStatus, DeliveryStatus,
)


@dataclass
class Recipient:
    """Destinataire unifié (membre ou contact externe)."""
    email: str
    first_name: str = ""
    last_name: str = ""
    civility: str = ""    # "F" / "S" / ""
    grade_label: str = ""
    kind: str = "m"        # "m" = member, "e" = external
    contact_id: int = 0    # member_id ou external_id
    raw_name: str = ""     # pour externes : nom complet brut

logger = logging.getLogger(__name__)

# Rate limit pour l'envoi
EMAIL_DELAY_MS = 250
RECIPIENTS_HARD_LIMIT = 500  # garde-fou par campagne

# Stockage des tasks fire-and-forget pour éviter qu'asyncio les garbage-collecte
# avant la fin de l'envoi.
_RUNNING_TASKS: set = set()


def launch_send_task(campaign_id: int) -> None:
    """Démarre l'envoi en arrière-plan en gardant la task référencée."""
    import asyncio as _aio
    task = _aio.ensure_future(send_campaign_async(campaign_id))
    _RUNNING_TASKS.add(task)
    task.add_done_callback(_RUNNING_TASKS.discard)


# ─────────────────────────────────────────────────────────────────────────────
#  Résolution des destinataires
# ─────────────────────────────────────────────────────────────────────────────

_GRADE_LABEL = {"APPRENTI": "Apprenti", "COMPAGNON": "Compagnon", "MAITRE": "Maître"}


def _member_to_recipient(m: Member) -> Recipient:
    g = m.masonic_grade.value if m.masonic_grade else ""
    return Recipient(
        email=m.email or "",
        first_name=m.first_name or "",
        last_name=m.last_name or "",
        civility=m.civility or "",
        grade_label=_GRADE_LABEL.get(g, g),
        kind="m", contact_id=m.id,
    )


def _external_to_recipient(e: ExternalContact) -> Recipient:
    # Priorité aux champs structurés, sinon fallback en splittant `name`
    fname = (e.first_name or "").strip()
    lname = (e.last_name or "").strip()
    if not fname and not lname and e.name:
        if " " in e.name:
            parts = e.name.strip().split(" ", 1)
            fname, lname = parts[0], parts[1]
        else:
            lname = e.name.strip()
    return Recipient(
        email=e.email or "",
        first_name=fname, last_name=lname,
        civility="", grade_label="",
        kind="e", contact_id=e.id,
        raw_name=e.name or f"{fname} {lname}".strip(),
    )


async def resolve_recipients(db: AsyncSession, mlist: MailingList) -> list[Recipient]:
    """Liste unifiée des destinataires (members + externals, désinscrits exclus)."""
    recipients: list[Recipient] = []

    # ── 1. Members selon le type de liste ──
    if mlist.list_type == MailingListType.STATIC:
        r = await db.execute(
            select(Member)
            .join(MailingListMember, MailingListMember.member_id == Member.id)
            .where(
                MailingListMember.list_id == mlist.id,
                MailingListMember.unsubscribed_at.is_(None),
            )
            .order_by(Member.last_name, Member.first_name)
        )
        members = list(r.scalars().all())
    else:
        # DYNAMIC : critères JSON sur Member
        criteria = mlist.criteria or {}
        stmt = select(Member)
        statuses = criteria.get("status") or ["ACTIVE"]
        if statuses and "ALL" not in statuses:
            stmt = stmt.where(Member.status.in_(
                [MemberStatus(s) for s in statuses if s in MemberStatus.__members__]
            ))
        grades = criteria.get("grade") or []
        if grades and "ALL" not in grades:
            stmt = stmt.where(Member.masonic_grade.in_(
                [MasonicGrade(g) for g in grades if g in MasonicGrade.__members__]
            ))
        funcs = criteria.get("lodge_function") or []
        if funcs and "ALL" not in funcs:
            stmt = stmt.where(Member.lodge_function.in_(
                [LodgeFunction(f) for f in funcs if f in LodgeFunction.__members__]
            ))
        group_ids = criteria.get("group_ids") or []
        if group_ids:
            from app.models.groups import GroupMembership
            sub = select(GroupMembership.member_id).where(GroupMembership.group_id.in_(group_ids))
            stmt = stmt.where(Member.id.in_(sub))
        stmt = stmt.order_by(Member.last_name, Member.first_name)
        r = await db.execute(stmt)
        members = list(r.scalars().all())
        # Filtre les désinscrits manuellement
        rs = await db.execute(
            select(MailingListMember.member_id).where(
                MailingListMember.list_id == mlist.id,
                MailingListMember.unsubscribed_at.isnot(None),
            )
        )
        unsubscribed = {row[0] for row in rs.all()}
        members = [m for m in members if m.id not in unsubscribed]

    recipients.extend(_member_to_recipient(m) for m in members)

    # ── 2. Externals (toujours via MailingListExternal pour les deux types) ──
    re_ = await db.execute(
        select(ExternalContact)
        .join(MailingListExternal, MailingListExternal.external_id == ExternalContact.id)
        .where(
            MailingListExternal.list_id == mlist.id,
            MailingListExternal.unsubscribed_at.is_(None),
            ExternalContact.is_active == True,  # noqa: E712
        )
        .order_by(ExternalContact.name)
    )
    externals = list(re_.scalars().all())
    recipients.extend(_external_to_recipient(e) for e in externals)

    # Déduplication par email (au cas où un externe a le même email qu'un membre)
    seen, deduped = set(), []
    for r in recipients:
        if not r.email:
            continue
        key = r.email.lower().strip()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return deduped


# ─────────────────────────────────────────────────────────────────────────────
#  Rendu du corps : Markdown léger + variables
# ─────────────────────────────────────────────────────────────────────────────

_VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _recipient_vars(r: Recipient) -> dict:
    """Variables disponibles dans le corps personnalisé."""
    if r.kind == "m":
        civ = "Sœur" if r.civility == "S" else "Frère"
        civ_short = "S∴" if r.civility == "S" else "F∴"
    else:
        # Pour un externe : pas de Frère/Sœur, on utilise un fallback neutre
        civ = ""
        civ_short = ""
    return {
        "prenom":         r.first_name,
        "nom":            r.last_name,
        "civilite":       civ,
        "civilite_court": civ_short,
        "grade":          r.grade_label,
        "email":          r.email,
        "nom_complet":    r.raw_name or f"{r.first_name} {r.last_name}".strip(),
    }


def render_subject(template: str, recipient: Recipient) -> str:
    vars_ = _recipient_vars(recipient)
    return _VAR_RE.sub(lambda m: vars_.get(m.group(1), m.group(0)), template)


def render_body_md(template: str, recipient: Recipient) -> str:
    """Substitution des variables uniquement (le Markdown sera rendu côté HTML)."""
    vars_ = _recipient_vars(recipient)
    return _VAR_RE.sub(lambda m: vars_.get(m.group(1), m.group(0)), template)


def md_to_html(md: str) -> str:
    """Markdown très light : paragraphes, gras, italique, listes, liens, sauts de ligne."""
    if not md:
        return ""
    # Échappement HTML d'abord
    out = html_escape(md)
    # Liens [texte](url)
    out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2" target="_blank">\1</a>', out)
    # Gras **
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    # Italique *
    out = re.sub(r"(?<!\*)\*(?!\*)([^\*\n]+)\*(?!\*)", r"<em>\1</em>", out)
    # Listes — basiques (chaque ligne commençant par - ou *)
    lines = out.split("\n")
    rendered_lines = []
    in_list = False
    for ln in lines:
        s = ln.strip()
        if re.match(r"^[-*]\s+", s):
            if not in_list:
                rendered_lines.append("<ul>")
                in_list = True
            rendered_lines.append(f"<li>{s[2:].strip()}</li>")
        else:
            if in_list:
                rendered_lines.append("</ul>")
                in_list = False
            rendered_lines.append(ln)
    if in_list:
        rendered_lines.append("</ul>")
    out = "\n".join(rendered_lines)
    # Paragraphes : 2 sauts → </p><p>
    paragraphs = [p for p in re.split(r"\n\s*\n", out) if p.strip()]
    body = "".join(f"<p>{p.replace(chr(10), '<br>')}</p>" for p in paragraphs)
    return body


def make_html_email(rendered_body_html: str, unsubscribe_url: str,
                    list_name: str, lodge_name: str) -> str:
    """Wrappe le corps Markdown rendu dans un template HTML simple."""
    return f"""<!DOCTYPE html>
<html lang="fr">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:24px 12px;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.08);max-width:600px;width:100%;">
        <tr><td style="background:#2c7a7b;padding:18px 24px;color:#fff;">
          <div style="font-size:11px;letter-spacing:.08em;text-transform:uppercase;opacity:.8;">{html_escape(lodge_name)}</div>
        </td></tr>
        <tr><td style="padding:24px;color:#1f2937;font-size:15px;line-height:1.6;">
          {rendered_body_html}
        </td></tr>
        <tr><td style="background:#f9fafb;border-top:1px solid #e5e7eb;padding:14px 24px;font-size:11px;color:#6b7280;">
          Vous recevez cet email car vous êtes inscrit(e) à la liste « {html_escape(list_name)} ».<br>
          <a href="{html_escape(unsubscribe_url)}" style="color:#6b7280;text-decoration:underline;">Se désinscrire de cette liste</a>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
#  Tokens de tracking (pixel ouverture + clics)
# ─────────────────────────────────────────────────────────────────────────────

def make_tracking_token(delivery_id: int, kind: str) -> str:
    """kind = 'o' (open) ou 'c' (click)."""
    payload = f"{delivery_id}.{kind}"
    sig = hmac.new(_hmac_secret(), payload.encode(), hashlib.sha256).hexdigest()[:12]
    return f"{payload}.{sig}"


def verify_tracking_token(token: str) -> Optional[tuple[int, str]]:
    """Retourne (delivery_id, kind) ou None."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        d_id, kind, sig = parts
        if kind not in ("o", "c"):
            return None
        payload = f"{d_id}.{kind}"
        expected = hmac.new(_hmac_secret(), payload.encode(), hashlib.sha256).hexdigest()[:12]
        if not hmac.compare_digest(expected, sig):
            return None
        return (int(d_id), kind)
    except (ValueError, AttributeError):
        return None


def _rewrite_links(html: str, base_url: str, delivery_id: int) -> str:
    """Réécrit les liens <a href="..."> vers le tracker de clics."""
    import re
    token = make_tracking_token(delivery_id, "c")

    def replace_href(m):
        original = m.group(1)
        # Ne pas réécrire les liens de désinscription ou internes
        if "/mailing/unsubscribe" in original or original.startswith("mailto:"):
            return m.group(0)
        from urllib.parse import quote
        return f'href="{base_url}/mailing/track/click/{token}?url={quote(original, safe="")}"'

    return re.sub(r'href="(https?://[^"]+)"', replace_href, html)


# ─────────────────────────────────────────────────────────────────────────────
#  Token désinscription signé HMAC
# ─────────────────────────────────────────────────────────────────────────────

def _hmac_secret() -> bytes:
    s = get_settings()
    return (s.secret_key or "fallback-secret").encode("utf-8")


def make_unsubscribe_token(list_id: int, kind: str, contact_id: int) -> str:
    """Token de désinscription signé HMAC. `kind` = 'm' (member) ou 'e' (external)."""
    if kind not in ("m", "e"):
        kind = "m"
    payload = f"{list_id}.{kind}.{contact_id}"
    sig = hmac.new(_hmac_secret(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{payload}.{sig}"


def verify_unsubscribe_token(token: str) -> Optional[tuple[int, str, int]]:
    """Retourne (list_id, kind, contact_id) ou None si invalide.

    Compatibilité ascendante : si le token contient 3 segments (ancien format
    list_id.member_id.sig), on considère kind='m'.
    """
    try:
        parts = token.split(".")
        if len(parts) == 4:
            list_id_s, kind, contact_id_s, sig = parts
            if kind not in ("m", "e"):
                return None
            list_id, contact_id = int(list_id_s), int(contact_id_s)
            payload = f"{list_id}.{kind}.{contact_id}"
        elif len(parts) == 3:
            # Ancien format : list_id.member_id.sig (kind = "m" implicite)
            list_id_s, member_id_s, sig = parts
            list_id, contact_id = int(list_id_s), int(member_id_s)
            kind = "m"
            payload = f"{list_id}.{contact_id}"
        else:
            return None
        expected = hmac.new(_hmac_secret(), payload.encode(), hashlib.sha256).hexdigest()[:16]
        if not hmac.compare_digest(expected, sig):
            return None
        return (list_id, kind, contact_id)
    except (ValueError, AttributeError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  Envoi (worker async)
# ─────────────────────────────────────────────────────────────────────────────

async def _resolve_attachments(db: AsyncSession, attachments_json) -> list[tuple[str, bytes, str]]:
    """Charge le contenu des pièces jointes depuis la GED.

    Format attendu : [{"doc_id": int, "filename": str}, ...]
    Renvoie : [(filename, bytes, mime_type), ...]
    """
    out = []
    if not attachments_json:
        return out
    try:
        from app.models.documents import Document
        import os
        import mimetypes
    except Exception:
        return out

    for a in attachments_json:
        doc_id = a.get("doc_id")
        if not doc_id:
            continue
        doc = await db.get(Document, int(doc_id))
        if not doc or not doc.storage_path:
            continue
        try:
            with open(doc.storage_path, "rb") as f:
                content = f.read()
            fname = a.get("filename") or doc.original_filename or os.path.basename(doc.storage_path)
            mime = doc.mime_type or mimetypes.guess_type(fname)[0] or "application/octet-stream"
            out.append((fname, content, mime))
        except OSError:
            logger.warning("PJ introuvable doc_id=%s path=%s", doc_id, doc.file_path)
    return out


async def send_campaign_async(campaign_id: int, base_url: str = "https://portail.amisdesocrate.fr"):
    """Worker d'envoi d'une campagne — boucle avec rate-limit + reuse SMTP."""
    from app.services.email import _send_raw

    async with AsyncSessionLocal() as db:
        campaign = await db.get(MailingCampaign, campaign_id)
        if not campaign:
            return
        if campaign.status not in (CampaignStatus.DRAFT, CampaignStatus.SENDING):
            return
        campaign.status = CampaignStatus.SENDING
        await db.commit()

        # Charger la liste + résoudre destinataires
        mlist = await db.get(MailingList, campaign.list_id)
        if not mlist:
            campaign.status = CampaignStatus.FAILED
            await db.commit()
            return
        recipients = await resolve_recipients(db, mlist)
        if len(recipients) > RECIPIENTS_HARD_LIMIT:
            recipients = recipients[:RECIPIENTS_HARD_LIMIT]

        # Lodge name
        from app.models.lodge import LodgeSettings
        lr = await db.execute(select(LodgeSettings).limit(1))
        lodge = lr.scalar_one_or_none()
        lodge_name = lodge.name if lodge and lodge.name else "Portail Socrate"

        # Pièces jointes (chargées une fois pour tous)
        attachments = await _resolve_attachments(db, campaign.attachments)

        # Skip les destinataires déjà SENT (idempotence en cas de retry)
        existing = await db.execute(
            select(MailingDelivery).where(MailingDelivery.campaign_id == campaign.id)
        )
        already_sent_keys = set()
        for d_existing in existing.scalars().all():
            if d_existing.status == DeliveryStatus.SENT:
                key = (d_existing.member_id, d_existing.external_id, d_existing.email.lower())
                already_sent_keys.add(key)

        sent = 0
        failed = 0
        campaign.recipients_count = len(recipients)
        await db.commit()

        for r in recipients:
            key = (
                r.contact_id if r.kind == "m" else None,
                r.contact_id if r.kind == "e" else None,
                (r.email or "").lower(),
            )
            if key in already_sent_keys:
                sent += 1
                continue

            # Crée le record delivery
            d = MailingDelivery(
                campaign_id=campaign.id,
                member_id=(r.contact_id if r.kind == "m" else None),
                external_id=(r.contact_id if r.kind == "e" else None),
                email=r.email or "",
                status=DeliveryStatus.PENDING,
            )
            db.add(d)
            await db.flush()

            if not r.email:
                d.status = DeliveryStatus.NO_EMAIL
                d.error = "Aucune adresse email"
                failed += 1
                await db.commit()
                continue

            # Rendu personnalisé
            subj = render_subject(campaign.subject, r)
            body_md = render_body_md(campaign.body_md, r)
            body_html_inner = md_to_html(body_md)

            # URL de désinscription
            tok = make_unsubscribe_token(mlist.id, r.kind, r.contact_id)
            unsub_url = f"{base_url}/mailing/unsubscribe/{tok}"

            # Pixel de tracking d'ouverture (image 1×1 transparente)
            open_token = make_tracking_token(d.id, "o")
            pixel = f'<img src="{base_url}/mailing/track/open/{open_token}" width="1" height="1" alt="" style="display:none">'

            # Réécriture des liens pour le tracking clics
            body_html_with_links = _rewrite_links(body_html_inner, base_url, d.id)

            html = make_html_email(body_html_with_links + pixel, unsub_url, mlist.name, lodge_name)
            text = body_md + f"\n\n— Se désinscrire : {unsub_url}"

            try:
                ok, err = await _send_raw(
                    to=r.email, subject=subj, html=html, text=text,
                    attachments=attachments or None,
                )
                if ok:
                    d.status = DeliveryStatus.SENT
                    d.sent_at = datetime.utcnow()
                    sent += 1
                else:
                    d.status = DeliveryStatus.FAILED
                    d.error = (err or "")[:1000]
                    failed += 1
            except Exception as e:
                d.status = DeliveryStatus.FAILED
                d.error = str(e)[:1000]
                failed += 1
            await db.commit()

            # Rate limit
            await asyncio.sleep(EMAIL_DELAY_MS / 1000.0)

        # Fin de campagne
        campaign.sent_count = sent
        campaign.failed_count = failed
        campaign.status = CampaignStatus.SENT if sent > 0 else CampaignStatus.FAILED
        campaign.sent_at = datetime.utcnow()
        await db.commit()


# ─────────────────────────────────────────────────────────────────────────────
#  Bootstrap : auto-création des listes système au démarrage
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_LISTS = [
    {
        "name": "Tous les membres actifs",
        "description": "Tous les membres en statut actif. Liste recalculée à chaque envoi.",
        "criteria": {"status": ["ACTIVE"]},
    },
    {
        "name": "Apprentis et Compagnons",
        "description": "Tous les apprentis et compagnons actifs.",
        "criteria": {"status": ["ACTIVE"], "grade": ["APPRENTI", "COMPAGNON"]},
    },
    {
        "name": "Maîtres",
        "description": "Tous les maîtres actifs.",
        "criteria": {"status": ["ACTIVE"], "grade": ["MAITRE"]},
    },
]


async def ensure_system_lists():
    """Crée les 3 listes dynamiques système si elles n'existent pas."""
    async with AsyncSessionLocal() as db:
        for spec in DEFAULT_LISTS:
            r = await db.execute(
                select(MailingList).where(MailingList.name == spec["name"])
            )
            if r.scalar_one_or_none():
                continue
            ml = MailingList(
                name=spec["name"],
                description=spec["description"],
                list_type=MailingListType.DYNAMIC,
                criteria=spec["criteria"],
                is_system=True,
            )
            db.add(ml)
        await db.commit()
