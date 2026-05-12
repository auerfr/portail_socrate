"""Service Listes de diffusion — résolution destinataires, rendu, envoi async."""
import asyncio
import hashlib
import hmac
import logging
import re
from datetime import datetime
from html import escape as html_escape
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models.identity import Member, MemberStatus, MasonicGrade, LodgeFunction
from app.models.mailing import (
    MailingList, MailingListMember, MailingListType,
    MailingCampaign, MailingDelivery, CampaignStatus, DeliveryStatus,
)

logger = logging.getLogger(__name__)

# Rate limit pour l'envoi
EMAIL_DELAY_MS = 250
RECIPIENTS_HARD_LIMIT = 500  # garde-fou par campagne


# ─────────────────────────────────────────────────────────────────────────────
#  Résolution des destinataires
# ─────────────────────────────────────────────────────────────────────────────

async def resolve_recipients(db: AsyncSession, mlist: MailingList) -> list[Member]:
    """Retourne la liste des membres destinataires (en excluant les désinscrits)."""
    # 1) Charger les membres de base
    if mlist.list_type == MailingListType.STATIC:
        # Inscrits manuellement (jointure sur MailingListMember non-désinscrits)
        r = await db.execute(
            select(Member)
            .join(MailingListMember, MailingListMember.member_id == Member.id)
            .where(
                MailingListMember.list_id == mlist.id,
                MailingListMember.unsubscribed_at.is_(None),
            )
            .order_by(Member.last_name, Member.first_name)
        )
        return list(r.scalars().all())

    # DYNAMIC : critères JSON sur Member
    criteria = mlist.criteria or {}
    stmt = select(Member)

    # status — défaut ACTIVE
    statuses = criteria.get("status") or ["ACTIVE"]
    if statuses and "ALL" not in statuses:
        stmt = stmt.where(Member.status.in_(
            [MemberStatus(s) for s in statuses if s in MemberStatus.__members__]
        ))

    # grade
    grades = criteria.get("grade") or []
    if grades and "ALL" not in grades:
        stmt = stmt.where(Member.masonic_grade.in_(
            [MasonicGrade(g) for g in grades if g in MasonicGrade.__members__]
        ))

    # lodge_function
    funcs = criteria.get("lodge_function") or []
    if funcs and "ALL" not in funcs:
        stmt = stmt.where(Member.lodge_function.in_(
            [LodgeFunction(f) for f in funcs if f in LodgeFunction.__members__]
        ))

    # group_ids (membre d'au moins un groupe parmi la liste)
    group_ids = criteria.get("group_ids") or []
    if group_ids:
        from app.models.groups import GroupMembership
        sub = select(GroupMembership.member_id).where(GroupMembership.group_id.in_(group_ids))
        stmt = stmt.where(Member.id.in_(sub))

    stmt = stmt.order_by(Member.last_name, Member.first_name)
    r = await db.execute(stmt)
    members = list(r.scalars().all())

    # Filtre les désinscrits manuellement de cette liste dynamique
    rs = await db.execute(
        select(MailingListMember.member_id).where(
            MailingListMember.list_id == mlist.id,
            MailingListMember.unsubscribed_at.isnot(None),
        )
    )
    unsubscribed = {row[0] for row in rs.all()}
    return [m for m in members if m.id not in unsubscribed]


# ─────────────────────────────────────────────────────────────────────────────
#  Rendu du corps : Markdown léger + variables
# ─────────────────────────────────────────────────────────────────────────────

_VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _member_vars(member: Member) -> dict:
    civ = "Sœur" if (member.civility == "S") else "Frère"
    grade_v = member.masonic_grade.value if member.masonic_grade else ""
    grade_label = {"APPRENTI": "Apprenti", "COMPAGNON": "Compagnon",
                   "MAITRE": "Maître"}.get(grade_v, grade_v)
    return {
        "prenom":     member.first_name or "",
        "nom":        member.last_name or "",
        "civilite":   civ,
        "civilite_court": "S∴" if member.civility == "S" else "F∴",
        "grade":      grade_label,
        "email":      member.email or "",
    }


def render_subject(template: str, member: Member) -> str:
    vars_ = _member_vars(member)
    return _VAR_RE.sub(lambda m: vars_.get(m.group(1), m.group(0)), template)


def render_body_md(template: str, member: Member) -> str:
    """Substitution des variables uniquement (le Markdown sera rendu côté HTML)."""
    vars_ = _member_vars(member)
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
#  Token désinscription signé HMAC
# ─────────────────────────────────────────────────────────────────────────────

def _hmac_secret() -> bytes:
    s = get_settings()
    return (s.secret_key or "fallback-secret").encode("utf-8")


def make_unsubscribe_token(list_id: int, member_id: int) -> str:
    payload = f"{list_id}.{member_id}"
    sig = hmac.new(_hmac_secret(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{payload}.{sig}"


def verify_unsubscribe_token(token: str) -> Optional[tuple[int, int]]:
    try:
        list_id_s, member_id_s, sig = token.split(".")
        list_id, member_id = int(list_id_s), int(member_id_s)
        expected = hmac.new(
            _hmac_secret(), f"{list_id}.{member_id}".encode(), hashlib.sha256
        ).hexdigest()[:16]
        if not hmac.compare_digest(expected, sig):
            return None
        return (list_id, member_id)
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

        sent = 0
        failed = 0
        campaign.recipients_count = len(recipients)
        await db.commit()

        for m in recipients:
            # Crée le record delivery
            d = MailingDelivery(
                campaign_id=campaign.id,
                member_id=m.id,
                email=m.email or "",
                status=DeliveryStatus.PENDING,
            )
            db.add(d)
            await db.flush()

            if not m.email:
                d.status = DeliveryStatus.NO_EMAIL
                d.error = "Aucune adresse email"
                failed += 1
                await db.commit()
                continue

            # Rendu personnalisé
            subj = render_subject(campaign.subject, m)
            body_md = render_body_md(campaign.body_md, m)
            body_html_inner = md_to_html(body_md)

            # URL de désinscription
            tok = make_unsubscribe_token(mlist.id, m.id)
            unsub_url = f"{base_url}/mailing/unsubscribe/{tok}"

            html = make_html_email(body_html_inner, unsub_url, mlist.name, lodge_name)
            text = body_md + f"\n\n— Se désinscrire : {unsub_url}"

            try:
                ok, err = await _send_raw(
                    to=m.email, subject=subj, html=html, text=text,
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
