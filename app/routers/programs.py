"""Router Programmes — génération mensuelle avec URL d'inscription et QR codes"""
import asyncio
import base64
import io
import logging
import uuid

logger = logging.getLogger(__name__)
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Optional

import qrcode
import qrcode.image.svg

from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import require_auth, require_admin
from app.models.programs import Program, ProgramMeeting
from app.models.identity import LodgeFunction
from app.models.meetings import Meeting, MeetingType, MeetingGrade
from app.models.lodge import MasonicYear, LodgeSettings, ExternalContact
from app.models.documents import DocFolder, DocSpace, DocStatus, Document
from app.models.mailing import MailingList, MailingListExternal

router = APIRouter(prefix="/programs", tags=["programs"])
from app.template_engine import templates

_PROGRAM_MANAGERS = {LodgeFunction.VM, LodgeFunction.SECRETAIRE}


async def _require_program_manager(ctx: Annotated[object, Depends(require_auth)]):
    from fastapi import HTTPException
    user, member = ctx
    if not (user.is_admin or member.lodge_function in _PROGRAM_MANAGERS):
        raise HTTPException(403, "Réservé au Secrétaire, au VM ou à l'administrateur")
    return ctx


# ── Helpers ────────────────────────────────────────────────────────────────

MOIS_FR = {
    1: "janvier", 2: "février",  3: "mars",    4: "avril",
    5: "mai",     6: "juin",     7: "juillet",  8: "août",
    9: "septembre", 10: "octobre", 11: "novembre", 12: "décembre",
}
JOUR_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

MEETING_TYPE_LABELS = {
    "BLANCHE":      "Tenue régulière",
    "SOLENNELLE":   "Tenue solennelle",
    "INSTRUCTION":  "Tenue d'instruction",
    "INITIATION":   "Tenue d'initiation",
    "INSTALLATION": "Installation des officiers",
    "ELECTION":     "Élection du Vénérable Maître",
    "PASSAGE":      "Passage au 2e degré",
    "ELEVATION":    "Élévation au 3e degré",
    "FETE":         "Fête maçonnique",
    "EXTRA":        "Tenue extraordinaire",
}
GRADE_LABELS = {
    "APPRENTI":  "1er degré",
    "COMPAGNON": "2e degré",
    "MAITRE":    "3e degré",
    "ALL":       "Tous degrés",
}


def _date_al(d: date) -> str:
    """Retourne la date en Anno Lucis et en civil."""
    jour = JOUR_FR[d.weekday()]
    mois = MOIS_FR[d.month]
    return f"{jour} {d.day} {mois} {d.year + 4000} E∴L∴"


def _date_civil(d: date) -> str:
    mois = MOIS_FR[d.month]
    return f"{d.day} {mois} {d.year}"


def _inscription_url(request: Request, token: str) -> str:
    from app.config import get_settings
    portal = get_settings().portal_url.rstrip("/")
    if not portal:
        portal = str(request.base_url).rstrip("/")
    return f"{portal}/inscription/{token}"


def _qr_svg(url: str) -> str:
    """Génère un QR code au format SVG inline."""
    factory = qrcode.image.svg.SvgPathImage
    img = qrcode.make(
        url,
        image_factory=factory,
        box_size=6,
        border=2,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
    )
    buf = io.BytesIO()
    img.save(buf)
    svg = buf.getvalue().decode("utf-8")
    # Extraire juste la balise <svg …> sans la déclaration XML
    start = svg.find("<svg")
    return svg[start:] if start >= 0 else svg


def _qr_png(url: str) -> bytes:
    """Génère un QR code au format PNG — pour intégration en image inline
    dans l'email (les SVG inline ne sont pas fiables dans les clients mail)."""
    img = qrcode.make(
        url,
        box_size=6,
        border=2,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _escape_stray_amp(text: str) -> str:
    """Échappe les « & » isolés (pas déjà une entité XML valide) — le parseur
    XML minimal de reportlab Paragraph plante sinon dès qu'un texte saisi
    librement (ordre du jour, intro…) contient un « & » brut, ce qui faisait
    échouer silencieusement toute la génération PDF pour repli HTML."""
    import re as _re
    return _re.sub(r"&(?!amp;|lt;|gt;|quot;|#\d+;|#x[0-9a-fA-F]+;)", "&amp;", text)


def _html_to_reportlab_markup(html: str) -> str:
    """Convertit le HTML (Quill) d'un champ « ordre du jour » en balisage
    compatible avec reportlab Paragraph. Paragraph supporte nativement
    <b>, <i>, <u>, <a href="">, <br/> — mais pas <ul>/<li>/<p>, qu'on
    convertit donc en lignes « • … » séparées par des <br/>."""
    import re as _re
    if not html:
        return ""
    text = html
    text = _re.sub(r"<li[^>]*>\s*", "• ", text)
    text = _re.sub(r"</li>", "<br/>", text)
    text = _re.sub(r"</?(ul|ol)[^>]*>", "", text)
    text = _re.sub(r"<p[^>]*>\s*", "", text)
    text = _re.sub(r"</p>", "<br/>", text)
    text = _re.sub(r"<br\s*/?>", "<br/>", text)
    text = text.replace("<strong>", "<b>").replace("</strong>", "</b>")
    text = text.replace("<em>", "<i>").replace("</em>", "</i>")
    # Retire les balises non supportées par Paragraph (span, etc.) en gardant le texte
    text = _re.sub(r"</?(?!b>|/b>|i>|/i>|u>|/u>|a[ >]|/a>|br/?>)[a-zA-Z][^>]*>", "", text)
    # Retire un <br/> de fin superflu
    text = _re.sub(r"(<br/>)+$", "", text).strip()
    text = _escape_stray_amp(text)
    return text


def _safe_paragraph(markup: str, style, plain_fallback: Optional[str] = None):
    """Construit un Paragraph reportlab en tolérant un balisage invalide —
    un seul champ de texte libre mal formé (ordre du jour, intro…) ne doit
    jamais faire échouer tout le PDF. Retourne None si même le repli échoue
    (le champ sera simplement omis plutôt que de bloquer tout le document)."""
    from reportlab.platypus import Paragraph
    import re as _re
    try:
        return Paragraph(markup, style)
    except Exception as _e:
        logger.warning("Paragraph PDF invalide, repli texte brut : %s", _e)
        plain = plain_fallback if plain_fallback is not None else _re.sub(r"<[^>]+>", "", markup)
        plain = _escape_stray_amp(plain)
        try:
            return Paragraph(plain, style)
        except Exception:
            return None


async def _render_program_pdf_via_browser(request: Request, program_id: int) -> Optional[bytes]:
    """Génère le PDF du programme en 'imprimant' la vraie page
    /programs/{id}?print_mode=true via un navigateur headless (Playwright) —
    garantit un rendu strictement identique à la page du site (QR codes,
    couleurs, mise en page). Retourne None si indisponible (Chromium non
    installé sur l'hébergement, page inaccessible…) — dans ce cas l'appelant
    doit se replier sur la génération ReportLab."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return None

    token = request.cookies.get("access_token")
    if not token:
        return None

    base = str(request.base_url).rstrip("/")
    url = f"{base}/programs/{program_id}?print_mode=true"
    host = request.url.hostname or "localhost"

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context()
                await context.add_cookies([{
                    "name": "access_token", "value": token,
                    "domain": host, "path": "/",
                }])
                page = await context.new_page()
                await page.goto(url, wait_until="networkidle", timeout=20000)
                pdf_bytes = await page.pdf(
                    format="A4",
                    print_background=True,
                    margin={"top": "0mm", "bottom": "0mm", "left": "0mm", "right": "0mm"},
                )
                return pdf_bytes
            finally:
                await browser.close()
    except Exception as _e:
        logger.warning("PDF via navigateur headless indisponible, repli sur ReportLab : %s", _e, exc_info=True)
        return None


async def _generate_program_pdf(
    request: Request, program_id: int, program: "Program", pm_sorted: list,
    lodge, qr_pngs: dict[int, bytes],
) -> Optional[bytes]:
    """Génère le PDF d'un programme : rendu fidèle via navigateur headless
    (identique à la page du site) avec repli ReportLab si indisponible.
    Partagé entre l'envoi aux correspondants externes et l'archivage GED —
    les deux doivent produire le même document."""
    pdf_bytes = await _render_program_pdf_via_browser(request, program_id)
    if pdf_bytes:
        return pdf_bytes

    # Repli : génération ReportLab (fiable partout, mise en page simplifiée)
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, Image
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
        import os as _os

        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=A4,
            leftMargin=2*cm, rightMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm,
        )

        TEAL = colors.HexColor("#1a5252")
        TEAL_LIGHT = colors.HexColor("#ecfdf5")
        TEAL_BORDER = colors.HexColor("#d1fae5")
        GRAY = colors.HexColor("#374151")
        GRAY_LIGHT = colors.HexColor("#9ca3af")

        styles = getSampleStyleSheet()
        h1 = ParagraphStyle("h1", parent=styles["Normal"], fontSize=16, textColor=colors.white,
                             fontName="Helvetica-Bold", alignment=TA_CENTER, spaceAfter=2)
        sub = ParagraphStyle("sub", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#a7d4d4"),
                             fontName="Helvetica", alignment=TA_CENTER)
        body = ParagraphStyle("body", parent=styles["Normal"], fontSize=10, textColor=GRAY,
                              fontName="Helvetica", leading=14, spaceAfter=4)
        meeting_title = ParagraphStyle("mt", parent=styles["Normal"], fontSize=11, textColor=TEAL,
                                       fontName="Helvetica-Bold", leading=14)
        small = ParagraphStyle("small", parent=styles["Normal"], fontSize=9, textColor=GRAY,
                               fontName="Helvetica", leading=12)
        url_style = ParagraphStyle("url", parent=styles["Normal"], fontSize=8, textColor=TEAL,
                                   fontName="Helvetica", leading=10)
        footer_label = ParagraphStyle("fl", parent=styles["Normal"], fontSize=9, textColor=TEAL,
                                      fontName="Helvetica-Bold", leading=12)
        footer_val = ParagraphStyle("fv", parent=styles["Normal"], fontSize=9, textColor=GRAY,
                                    fontName="Helvetica", leading=12)

        story = []

        # ── En-tête ──
        lodge_name = _escape_stray_amp(lodge.name if lodge else "Socrate — Raison et Progrès")
        obedience = _escape_stray_amp(lodge.obedience if lodge else "Grand Orient de France")
        orient = _escape_stray_amp(lodge.orient_city if lodge else "")
        loge_num = f" — R∴L∴ n°{lodge.loge_number}" if lodge and lodge.loge_number else ""
        rite = _escape_stray_amp(lodge.rite) if lodge and lodge.rite else None

        header_text = [
            Paragraph(lodge_name, h1),
            Paragraph(f"Au nom et sous les auspices du {obedience}<br/>Or∴ de {orient}{loge_num}", sub),
        ]
        if rite:
            header_text.append(Paragraph(f"— ϕ — {rite} — ϕ —", sub))

        seal_path = _os.path.join("app", "static", "img", "sceau-socrate-transparent.png")
        seal_img = Image(seal_path, width=1.6*cm, height=1.6*cm) if _os.path.exists(seal_path) else None

        if seal_img:
            header_table = Table([[seal_img, header_text]], colWidths=[2.4*cm, doc.width - 2.4*cm])
        else:
            header_table = Table([[header_text]], colWidths=[doc.width])
        header_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), TEAL),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 14),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
            ("LEFTPADDING", (0, 0), (-1, -1), 16),
            ("RIGHTPADDING", (0, 0), (-1, -1), 16),
            ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ]))
        story.append(header_table)
        story.append(Spacer(1, 0.4*cm))

        # ── Salutation / intro ──
        story.append(Paragraph("Mon T∴C∴F∴, ma T∴C∴S∴,", body))
        if program.content_html:
            import re as _re
            clean_intro = _escape_stray_amp(_re.sub(r"<[^>]+>", " ", program.content_html).strip())
            p = _safe_paragraph(clean_intro, body)
            if p:
                story.append(p)
        story.append(Spacer(1, 0.3*cm))

        # ── Tenues ──
        MEETING_TYPE_SHORT = {
            "BLANCHE": "Ten∴ Bl∴", "SOLENNELLE": "Ten∴ Sol∴", "INSTRUCTION": "Ten∴ d'Instr∴",
            "INITIATION": "Ten∴ d'Init∴", "INSTALLATION": "Installation",
            "ELECTION": "Élection du V∴M∴", "PASSAGE": "Passage au 2e degré",
            "ELEVATION": "Élévation au 3e degré", "FETE": "Fête mac∴", "EXTRA": "Ten∴ extraordinaire",
        }

        for pm in pm_sorted:
            m = pm.meeting
            url = pm.registration_url or _inscription_url(request, m.token)

            n = m.meeting_number
            num_label = f"{n}{'ère' if n == 1 else 'ème'} " if n else ""
            type_label = MEETING_TYPE_SHORT.get(m.type.value, m.type.value)
            grade_label = {"APPRENTI": "App∴", "COMPAGNON": "Comp∴", "MAITRE": "M∴"}.get(m.grade.value, "TLR∴")
            title_str = f"▲ {num_label}{type_label} du {_date_civil(m.meeting_date)} en Loge d'{grade_label}"

            card_rows = [[Paragraph(title_str, meeting_title)]]

            if m.agenda_html:
                agenda_markup = _html_to_reportlab_markup(m.agenda_html)
                p = _safe_paragraph(agenda_markup, small)
                if p:
                    card_rows.append([p])

            if m.degrees and len(m.degrees) > 1:
                for deg in m.degrees:
                    deg_label = _escape_stray_amp(deg.description or GRADE_LABELS.get(deg.grade.value, deg.grade.value))
                    p = _safe_paragraph(f"• {deg_label}", small)
                    if p:
                        card_rows.append([p])

            if m.agape_enabled:
                agape_text = f"• Agape fraternelle à l'issue"
                if m.agape_location:
                    agape_text += f" — {_escape_stray_amp(m.agape_location)}"
                agape_text += " <font color='#b45309'><b>(Réservation impérative)</b></font>"
                p = _safe_paragraph(agape_text, small)
                if p:
                    card_rows.append([p])

            card_rows.append([Paragraph(f"Inscription : {url}", url_style)])
            if qr_pngs.get(m.id):
                card_rows.append([Image(io.BytesIO(qr_pngs[m.id]), width=2.0*cm, height=2.0*cm)])

            card = Table(card_rows, colWidths=[doc.width])
            card.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, 0), TEAL_LIGHT),
                ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                ("BOX", (0, 0), (-1, -1), 0.5, TEAL_BORDER),
                ("LINEBELOW", (0, 0), (0, 0), 0.5, TEAL_BORDER),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("ROUNDEDCORNERS", [4, 4, 4, 4]),
            ]))
            story.append(card)
            story.append(Spacer(1, 0.3*cm))

        # ── Ordre du jour commun ──
        if lodge and lodge.common_agenda:
            story.append(Paragraph("▲ Ordre du jour commun à toutes les TTen∴", meeting_title))
            for line in lodge.common_agenda.split("\n"):
                stripped = line.strip()
                if stripped:
                    p = _safe_paragraph(_escape_stray_amp(stripped), small)
                    if p:
                        story.append(p)
            story.append(Spacer(1, 0.3*cm))

        # ── À noter ──
        if program.next_meetings_text:
            import re as _re
            note_clean = _escape_stray_amp(_re.sub(r"<[^>]+>", " ", program.next_meetings_text).strip())
            p = _safe_paragraph(note_clean, body)
            if p:
                story.append(p)
            story.append(Spacer(1, 0.3*cm))

        # ── Footer VM / Temple / Sec ──
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e5e7eb")))
        story.append(Spacer(1, 0.2*cm))

        vm_lines = [Paragraph("V∴M∴", footer_label)]
        if lodge and lodge.vm_name_display:
            vm_lines.append(_safe_paragraph(_escape_stray_amp(lodge.vm_name_display), footer_val) or Paragraph("", footer_val))
        if lodge and lodge.vm_email_display:
            vm_lines.append(_safe_paragraph(_escape_stray_amp(lodge.vm_email_display), footer_val) or Paragraph("", footer_val))

        temple_lines = [Paragraph("Temple", footer_label)]
        if lodge and lodge.temple_name:
            temple_lines.append(_safe_paragraph(_escape_stray_amp(lodge.temple_name), footer_val) or Paragraph("", footer_val))
        if lodge and lodge.temple_address:
            temple_lines.append(_safe_paragraph(_escape_stray_amp(lodge.temple_address), footer_val) or Paragraph("", footer_val))

        sec_lines = [Paragraph("Sec∴", footer_label)]
        if lodge and lodge.secretary_name_display:
            sec_lines.append(_safe_paragraph(_escape_stray_amp(lodge.secretary_name_display), footer_val) or Paragraph("", footer_val))
        if lodge and lodge.secretary_email_display:
            sec_lines.append(_safe_paragraph(_escape_stray_amp(lodge.secretary_email_display), footer_val) or Paragraph("", footer_val))

        footer_table = Table([[vm_lines, temple_lines, sec_lines]], colWidths=[doc.width/3]*3)
        footer_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(footer_table)

        doc.build(story)
        return buf.getvalue()
    except Exception as _e:
        logger.warning("PDF non généré (ReportLab) : %s", _e, exc_info=True)
        return None


async def _get_lodge(db: AsyncSession) -> Optional[LodgeSettings]:
    r = await db.execute(select(LodgeSettings).limit(1))
    return r.scalar_one_or_none()


# ══════════════════════════════════════════════════════════════════════════════
# LISTE
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/", response_class=HTMLResponse)
async def programs_list(
    request: Request,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    r = await db.execute(
        select(Program)
        .options(selectinload(Program.meetings))
        .order_by(Program.year.desc(), Program.month.desc())
    )
    programs = r.scalars().all()

    can_manage = user.is_admin or member.lodge_function in _PROGRAM_MANAGERS
    return templates.TemplateResponse(request, "pages/programs/list.html", {
        "current_member": member,
        "current_user": user,
        "programs": programs,
        "MOIS_FR": MOIS_FR,
        "is_admin": user.is_admin,
        "can_manage_programs": user.is_admin or member.lodge_function in _PROGRAM_MANAGERS,
        "can_manage_programs": can_manage,
        "now": datetime.now(),
    })


# ══════════════════════════════════════════════════════════════════════════════
# CRÉATION
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/create", response_class=HTMLResponse)
async def programs_create_form(
    request: Request,
    ctx: Annotated[object, Depends(_require_program_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx

    # Années maçonniques
    ry = await db.execute(select(MasonicYear).order_by(MasonicYear.start_date.desc()))
    years = ry.scalars().all()

    # Toutes les tenues (passées + à venir) pour permettre les programmes rétrospectifs
    rm = await db.execute(
        select(Meeting)
        .order_by(Meeting.meeting_date.desc())
    )
    all_meetings = rm.scalars().all()
    upcoming = [m for m in all_meetings if m.meeting_date >= date.today()]
    past = [m for m in all_meetings if m.meeting_date < date.today()]

    current_month = date.today().month
    current_year  = date.today().year

    return templates.TemplateResponse(request, "pages/programs/create.html", {
        "current_member": member,
        "current_user": user,
        "years": years,
        "upcoming": upcoming,
        "past": past,
        "current_month": current_month,
        "current_year": current_year,
        "MOIS_FR": MOIS_FR,
        "MEETING_TYPE_LABELS": MEETING_TYPE_LABELS,
        "GRADE_LABELS": GRADE_LABELS,
        "is_admin": user.is_admin,
        "can_manage_programs": user.is_admin or member.lodge_function in _PROGRAM_MANAGERS,
    })


@router.post("/create")
async def programs_create(
    request: Request,
    ctx: Annotated[object, Depends(_require_program_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    form = await request.form()

    month     = int(form.get("month", date.today().month))
    year      = int(form.get("year",  date.today().year))
    year_id   = form.get("masonic_year_id")
    title     = form.get("title", "").strip()
    notes     = form.get("notes", "").strip()
    next_txt  = form.get("next_meetings_text", "").strip()
    meeting_ids = form.getlist("meeting_ids")

    if not title:
        title = f"Programme — {MOIS_FR[month].capitalize()} {year + 4000} E∴L∴"

    program = Program(
        masonic_year_id=int(year_id) if year_id else None,
        title=title,
        month=month,
        year=year,
        content_html=notes or None,
        next_meetings_text=next_txt or None,
        created_by_id=member.id,
    )
    db.add(program)
    await db.flush()

    for pos, mid in enumerate(meeting_ids):
        mid = int(mid)
        # Récupérer le token de la tenue pour générer l'URL
        mtg = await db.get(Meeting, mid)
        reg_url = _inscription_url(request, mtg.token) if mtg else None
        # Mettre à jour le numéro de tenue si fourni
        num_raw = form.get(f"meeting_number_{mid}", "").strip()
        if mtg and num_raw.isdigit():
            mtg.meeting_number = int(num_raw)
        db.add(ProgramMeeting(
            program_id=program.id,
            meeting_id=mid,
            order_position=pos,
            registration_url=reg_url,
        ))

    await db.commit()
    return RedirectResponse(url=f"/programs/{program.id}", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# DÉTAIL / APERÇU (= page imprimable)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/{program_id}", response_class=HTMLResponse)
async def program_detail(
    program_id: int,
    request: Request,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    print_mode: bool = False,
):
    user, member = ctx
    program = await db.get(
        Program, program_id,
        options=[
            selectinload(Program.meetings)
            .selectinload(ProgramMeeting.meeting)
            .selectinload(Meeting.degrees)
        ]
    )
    if not program:
        raise HTTPException(404)

    lodge = await _get_lodge(db)

    # Trier les tenues par date (filtrer les ProgramMeeting orphelins)
    pm_sorted = sorted(
        [pm for pm in program.meetings if pm.meeting is not None],
        key=lambda pm: pm.meeting.meeting_date,
    )

    # Générer les QR codes SVG pour chaque tenue
    qr_codes: dict[int, str] = {}
    for pm in pm_sorted:
        url = _inscription_url(request, pm.meeting.token)
        qr_codes[pm.meeting_id] = _qr_svg(url)

    r_contacts = await db.execute(
        select(ExternalContact).where(ExternalContact.is_active == True)
        .order_by(ExternalContact.contact_type, ExternalContact.name)
    )
    external_contacts = r_contacts.scalars().all()

    # Listes de diffusion existantes (module Diffusion) pour sélection rapide
    # des destinataires externes — cf. app/routers/mailing.py
    r_lists = await db.execute(
        select(MailingList.id, MailingList.name, MailingListExternal.external_id)
        .join(MailingListExternal, MailingListExternal.list_id == MailingList.id)
        .join(ExternalContact, ExternalContact.id == MailingListExternal.external_id)
        .where(
            MailingListExternal.unsubscribed_at.is_(None),
            ExternalContact.is_active == True,
        )
        .order_by(MailingList.name)
    )
    mailing_lists_map: dict[int, dict] = {}
    for list_id, list_name, external_id in r_lists.all():
        entry = mailing_lists_map.setdefault(list_id, {"id": list_id, "name": list_name, "external_ids": []})
        entry["external_ids"].append(external_id)
    mailing_lists_for_program = [ml for ml in mailing_lists_map.values() if ml["external_ids"]]

    return templates.TemplateResponse(request, "pages/programs/detail.html", {
        "current_member": member,
        "current_user": user,
        "program": program,
        "pm_sorted": pm_sorted,
        "qr_codes": qr_codes,
        "lodge": lodge,
        "MOIS_FR": MOIS_FR,
        "MEETING_TYPE_LABELS": MEETING_TYPE_LABELS,
        "GRADE_LABELS": GRADE_LABELS,
        "date_al": _date_al,
        "date_civil": _date_civil,
        "inscription_url": lambda token: _inscription_url(request, token),
        "is_admin": user.is_admin,
        "can_manage_programs": user.is_admin or member.lodge_function in _PROGRAM_MANAGERS,
        "print_mode": print_mode,
        "now": datetime.now(),
        "external_contacts": external_contacts,
        "mailing_lists_for_program": mailing_lists_for_program,
        "email_sent": request.query_params.get("email_sent"),
        "email_already": request.query_params.get("email_already"),
        "email_attempted": request.query_params.get("email_attempted"),
        "imap_inbox": __import__('app.config', fromlist=['get_settings']).get_settings().imap_user or None,
    })


# ══════════════════════════════════════════════════════════════════════════════
# ÉDITION
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/{program_id}/edit", response_class=HTMLResponse)
async def program_edit_form(
    program_id: int,
    request: Request,
    ctx: Annotated[object, Depends(_require_program_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    program = await db.get(
        Program, program_id,
        options=[selectinload(Program.meetings).selectinload(ProgramMeeting.meeting)],
    )
    if not program:
        raise HTTPException(404)

    ry = await db.execute(select(MasonicYear).order_by(MasonicYear.start_date.desc()))
    years = ry.scalars().all()

    rm = await db.execute(select(Meeting).order_by(Meeting.meeting_date.desc()))
    all_meetings = rm.scalars().all()
    upcoming = [m for m in all_meetings if m.meeting_date >= date.today()]
    past     = [m for m in all_meetings if m.meeting_date <  date.today()]

    # IDs déjà dans le programme
    selected_ids = {pm.meeting_id for pm in program.meetings if pm.meeting is not None}

    return templates.TemplateResponse(request, "pages/programs/edit.html", {
        "current_member": member,
        "current_user": user,
        "program": program,
        "years": years,
        "upcoming": upcoming,
        "past": past,
        "selected_ids": selected_ids,
        "MOIS_FR": MOIS_FR,
        "MEETING_TYPE_LABELS": MEETING_TYPE_LABELS,
        "GRADE_LABELS": GRADE_LABELS,
        "is_admin": user.is_admin,
        "can_manage_programs": user.is_admin or member.lodge_function in _PROGRAM_MANAGERS,
    })


@router.post("/{program_id}/edit")
async def program_edit_save(
    program_id: int,
    request: Request,
    ctx: Annotated[object, Depends(_require_program_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    program = await db.get(
        Program, program_id,
        options=[selectinload(Program.meetings)],
    )
    if not program:
        raise HTTPException(404)

    form = await request.form()

    # Mettre à jour les champs texte
    new_title = form.get("title", "").strip()
    if new_title:
        program.title = new_title
    program.content_html = form.get("notes", "").strip() or None
    program.next_meetings_text = form.get("next_meetings_text", "").strip() or None

    year_id = form.get("masonic_year_id")
    if year_id:
        program.masonic_year_id = int(year_id)

    # Mettre à jour les tenues : supprimer les anciennes liaisons
    for pm in list(program.meetings):
        await db.delete(pm)
    await db.flush()

    # Recréer les liaisons avec les tenues cochées
    meeting_ids = form.getlist("meeting_ids")
    for pos, mid_str in enumerate(meeting_ids):
        mid = int(mid_str)
        mtg_r = await db.execute(select(Meeting).where(Meeting.id == mid))
        mtg = mtg_r.scalar_one_or_none()
        if not mtg:
            continue
        num_str = form.get(f"meeting_number_{mid}", "")
        if num_str.strip().isdigit():
            mtg.meeting_number = int(num_str)
        pm = ProgramMeeting(
            program_id=program_id,
            meeting_id=mid,
            order_position=pos,
            registration_url=_inscription_url(request, mtg.token),
        )
        db.add(pm)

    await db.commit()
    return RedirectResponse(url=f"/programs/{program_id}", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# SUPPRESSION
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/{program_id}/delete")
async def program_delete(
    program_id: int,
    ctx: Annotated[object, Depends(_require_program_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    program = await db.get(Program, program_id)
    if not program:
        raise HTTPException(404)
    from sqlalchemy import delete as sql_delete
    await db.execute(sql_delete(ProgramMeeting).where(ProgramMeeting.program_id == program_id))
    await db.delete(program)
    await db.commit()
    return RedirectResponse(url="/programs/", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# TRANSMISSION & ARCHIVAGE GED
# ══════════════════════════════════════════════════════════════════════════════

PROG_UPLOAD_DIR = Path("uploads/documents/programmes")
PROG_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


async def _find_ged_folder(db: AsyncSession, year_label: str) -> DocFolder | None:
    """
    Trouve le dossier GED 'Planches programmes YYYY-YYYY'.
    year_label : ex. "2024-2025"
    """
    folder_name = f"Planches programmes {year_label}"

    # Cherche l'espace "Planches Programmes"
    r_space = await db.execute(
        select(DocSpace).where(DocSpace.name == "Planches Programmes")
    )
    space = r_space.scalar_one_or_none()
    if not space:
        return None

    # Cherche le dossier par année dans cet espace
    r_folder = await db.execute(
        select(DocFolder).where(
            DocFolder.space_id == space.id,
            DocFolder.name == folder_name,
        )
    )
    folder = r_folder.scalar_one_or_none()

    # Si le dossier n'existe pas encore, on le crée automatiquement
    if not folder:
        from app.models.documents import MinGrade
        folder = DocFolder(
            name=folder_name,
            space_id=space.id,
            parent_id=None,
            min_grade=MinGrade.APPRENTI,
            order_position=0,
        )
        db.add(folder)
        await db.flush()

    return folder


@router.post("/{program_id}/transmit")
async def program_transmit(
    program_id: int,
    request: Request,
    ctx: Annotated[object, Depends(_require_program_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Marque le programme comme transmis et l'archive dans la GED.
    Archive le même PDF que celui envoyé aux correspondants externes
    (mise en page et QR codes préservés, contrairement à un export HTML).
    """
    user, member = ctx

    program = await db.get(
        Program, program_id,
        options=[
            selectinload(Program.meetings)
            .selectinload(ProgramMeeting.meeting)
            .selectinload(Meeting.degrees)
        ]
    )
    if not program:
        raise HTTPException(404)

    lodge = await _get_lodge(db)
    masonic_year = await db.get(MasonicYear, program.masonic_year_id) if program.masonic_year_id else None

    # ── 1. Générer le PDF du programme (même document que l'envoi externe) ──
    pm_sorted = sorted(program.meetings, key=lambda pm: pm.meeting.meeting_date)
    qr_pngs: dict[int, bytes] = {}
    for pm in pm_sorted:
        url = pm.registration_url or _inscription_url(request, pm.meeting.token)
        qr_pngs[pm.meeting_id] = _qr_png(url)

    pdf_bytes = await _generate_program_pdf(request, program_id, program, pm_sorted, lodge, qr_pngs)

    # ── 2. Sauvegarder le fichier ─────────────────────────────────────────
    safe_title = "".join(c for c in program.title if c.isalnum() or c in " -_").strip()
    if pdf_bytes:
        filename    = f"{safe_title}.pdf"
        stored_name = f"{uuid.uuid4().hex}.pdf"
        dest_path   = PROG_UPLOAD_DIR / stored_name
        dest_path.write_bytes(pdf_bytes)
        mime_type = "application/pdf"
    else:
        # Repli extrême (rendu navigateur ET ReportLab indisponibles) :
        # version HTML autonome, pour ne jamais bloquer l'archivage.
        logger.warning("PDF indisponible pour l'archivage du programme %s, repli HTML", program_id)
        qr_codes: dict[int, str] = {}
        for pm in pm_sorted:
            url = pm.registration_url or _inscription_url(request, pm.meeting.token)
            qr_codes[pm.meeting_id] = _qr_svg(url)
        html_content = templates.TemplateResponse(
            request,
            "pages/programs/detail.html",
            {
                "current_member": member,
                "current_user": user,
                "program": program,
                "pm_sorted": pm_sorted,
                "qr_codes": qr_codes,
                "lodge": lodge,
                "MOIS_FR": MOIS_FR,
                "MEETING_TYPE_LABELS": MEETING_TYPE_LABELS,
                "GRADE_LABELS": GRADE_LABELS,
                "date_al": _date_al,
                "date_civil": _date_civil,
                "inscription_url": lambda token: _inscription_url(request, token),
                "is_admin": False,   # mode consultation — pas de boutons admin
                "can_manage_programs": False,
                "print_mode": True,
                "now": datetime.now(),
                "email_sent": None,
                "email_already": None,
                "email_attempted": None,
                # Inject Tailwind CDN pour le fichier autonome
                "_standalone": True,
            },
        )
        html_str = html_content.body.decode("utf-8")
        filename    = f"{safe_title}.html"
        stored_name = f"{uuid.uuid4().hex}.html"
        dest_path   = PROG_UPLOAD_DIR / stored_name
        dest_path.write_text(html_str, encoding="utf-8")
        mime_type = "text/html"

    # ── 3. Trouver / créer le dossier GED ───────────────────────────────────
    if masonic_year:
        year_label = f"{masonic_year.start_date.year}-{masonic_year.end_date.year}"
    else:
        # Fallback : année civile du programme
        year_label = f"{program.year}-{program.year + 1}"

    ged_folder = await _find_ged_folder(db, year_label)

    # ── 4. Créer l'entrée Document dans la GED ──────────────────────────────
    doc_id: Optional[int] = None
    if ged_folder:
        # Vérifier si ce programme est déjà archivé (éviter doublons)
        existing_doc = await db.execute(
            select(Document).where(
                Document.folder_id == ged_folder.id,
                Document.name == program.title,
            )
        )
        existing = existing_doc.scalar_one_or_none()
        if existing:
            # Mettre à jour le fichier existant
            try:
                Path(existing.storage_path).unlink(missing_ok=True)
            except Exception:
                pass
            existing.storage_path = str(dest_path)
            existing.original_filename = filename
            existing.mime_type = mime_type
            existing.file_size = dest_path.stat().st_size
            doc_id = existing.id
        else:
            doc = Document(
                folder_id=ged_folder.id,
                name=program.title,
                original_filename=filename,
                mime_type=mime_type,
                file_size=dest_path.stat().st_size,
                storage_path=str(dest_path),
                status=DocStatus.PUBLISHED,
                author_id=member.id,
            )
            db.add(doc)
            await db.flush()
            doc_id = doc.id

    # ── 5. Marquer le programme comme transmis ───────────────────────────────
    program.sent_at = datetime.now()
    program.sent_by_id = member.id
    program.pdf_path = str(dest_path)  # réutilise le champ pour stocker le chemin

    await db.commit()

    # ── 6. Notifier les membres (message interne + email) ────────────────────
    # Un programme archivé n'est utile que si les membres savent où le trouver —
    # contrairement à l'envoi aux correspondants externes (bouton séparé), ceci
    # les prévient simplement que la version archivée est disponible.
    base_url = str(request.base_url).rstrip("/")
    if doc_id:
        doc_url = f"{base_url}/documents/file/{doc_id}/view"
        push_url = f"/documents/file/{doc_id}/view"
    elif ged_folder:
        doc_url = f"{base_url}/documents/folder/{ged_folder.id}"
        push_url = f"/documents/folder/{ged_folder.id}"
    else:
        doc_url = f"{base_url}/programs/{program_id}"
        push_url = f"/programs/{program_id}"

    # L'audience de la notification doit correspondre à qui peut réellement
    # ouvrir le document archivé — sinon certains membres reçoivent un lien
    # qui leur renvoie une 403 (dossier/espace GED restreint par grade ou
    # groupe). On reprend la restriction la plus stricte entre le dossier et
    # l'espace documentaire.
    notif_min_grade: Optional[str] = None
    notif_group_id: Optional[int] = None
    if ged_folder:
        from app.models.documents import DocSpace as _DocSpace, MinGrade as _MinGrade
        doc_space = await db.get(_DocSpace, ged_folder.space_id)
        if ged_folder.group_id:
            notif_group_id = ged_folder.group_id
        elif doc_space and doc_space.group_id:
            notif_group_id = doc_space.group_id
        else:
            _lvl = {_MinGrade.ALL: 0, _MinGrade.APPRENTI: 1, _MinGrade.COMPAGNON: 2, _MinGrade.MAITRE: 3}
            effective = max(
                _lvl.get(ged_folder.min_grade, 0),
                _lvl.get(doc_space.min_grade, 0) if doc_space else 0,
            )
            if effective >= 3:
                notif_min_grade = "MAITRE"
            elif effective >= 2:
                notif_min_grade = "COMPAGNON"
            # ALL / APPRENTI → aucune restriction, tout membre actif y a accès

    from app.utils.notifications import send_notification
    await send_notification(
        db, member.id,
        f"📋 Programme archivé : {program.title}",
        f"Le programme « {program.title} » a été transmis et archivé, vous pouvez le consulter dans la bibliothèque :\n\n{doc_url}",
        min_grade=notif_min_grade,
        target_group_id=notif_group_id,
        send_email=True,
        portal_base_url=base_url,
        push_url=push_url,
        push_body=f"Disponible dans la bibliothèque — {program.title}",
    )
    await db.commit()

    return RedirectResponse(
        url=f"/programs/{program_id}?transmitted=1",
        status_code=303,
    )


# ══════════════════════════════════════════════════════════════════════════════
# PRÉVISUALISATION EMAIL EXTERNE
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/{program_id}/preview-email", response_class=HTMLResponse)
async def program_preview_email(
    program_id: int,
    request: Request,
    ctx: Annotated[object, Depends(_require_program_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Rendu de l'email externe dans le navigateur — aucun envoi."""
    program = await db.get(
        Program, program_id,
        options=[selectinload(Program.meetings).selectinload(ProgramMeeting.meeting).selectinload(Meeting.degrees)],
    )
    if not program:
        raise HTTPException(404)

    lodge = await _get_lodge(db)
    pm_sorted = sorted(
        [pm for pm in program.meetings if pm.meeting is not None],
        key=lambda pm: pm.meeting.meeting_date,
    )

    from app.config import get_settings as _gs
    _imap_user = _gs().imap_user or None

    # Dans le vrai email, le QR code est une image intégrée (cid:) attachée au
    # message — un navigateur ne sait pas résoudre cid: en dehors d'un client
    # mail, donc pour cette prévisualisation on l'encode en data: URI à la
    # place, afin que le QR s'affiche réellement (le rendu final envoyé reste
    # inchangé, cid:, cf. program_send).
    qr_pngs: dict[int, bytes] = {}
    for pm in pm_sorted:
        m = pm.meeting
        qr_url = pm.registration_url or _inscription_url(request, m.token)
        qr_pngs[m.id] = _qr_png(qr_url)

    def _qr_src(meeting_id: int) -> str:
        png = qr_pngs.get(meeting_id)
        if not png:
            return ""
        return f"data:image/png;base64,{base64.b64encode(png).decode()}"

    html_content = templates.TemplateResponse(request, "emails/programme.html", {
        "program": program,
        "pm_sorted": pm_sorted,
        "lodge": lodge,
        "GRADE_LABELS": GRADE_LABELS,
        "date_civil": _date_civil,
        "inscription_url": lambda token: _inscription_url(request, token),
        "qr_src": _qr_src,
        "greeting": "Mon T∴C∴F∴, ma T∴C∴S∴,",
        "base_url": str(request.base_url).rstrip("/"),
        "has_attachment": False,
        "attachment_name": None,
        "imap_inbox": _imap_user,
        "remove_url": "#exemple-desinscription",  # placeholder visible en prévisualisation
    })
    return HTMLResponse(content=html_content.body.decode("utf-8"))


# ENVOI EMAIL AUX CORRESPONDANTS EXTERNES
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/{program_id}/send-external")
async def program_send_external(
    program_id: int,
    request: Request,
    ctx: Annotated[object, Depends(_require_program_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from app.services.email import _send_raw
    user, member = ctx

    program = await db.get(
        Program, program_id,
        options=[selectinload(Program.meetings).selectinload(ProgramMeeting.meeting).selectinload(Meeting.degrees)]
    )
    if not program:
        raise HTTPException(404)

    lodge = await _get_lodge(db)
    form = await request.form()

    # Contacts cochés
    contact_ids = [int(v) for v in form.getlist("contact_ids") if v.isdigit()]
    extra_emails_raw = form.get("extra_emails", "").strip()
    extra_emails = [e.strip() for e in extra_emails_raw.replace(";", ",").split(",") if e.strip() and "@" in e]

    # Récupérer les emails des contacts sélectionnés
    recipients = []  # (name, email, remove_url | None)
    base_url_str = str(request.base_url).rstrip("/")
    if contact_ids:
        from app.services.contact_confirmation import make_cc_token
        r = await db.execute(select(ExternalContact).where(ExternalContact.id.in_(contact_ids), ExternalContact.is_active == True))
        for c in r.scalars().all():
            remove_url = f"{base_url_str}/contacts/remove/{make_cc_token(c.id, 'remove')}"
            recipients.append((c.name, c.email, remove_url))
    for e in extra_emails:
        recipients.append(("", e, None))

    if not recipients:
        return RedirectResponse(url=f"/programs/{program_id}?email_sent=0", status_code=303)

    # Pièce jointe optionnelle (affiche, flyer…)
    attachments = []
    attach_field = form.get("attachment")
    if attach_field and getattr(attach_field, "filename", None):
        attach_bytes = await attach_field.read()
        if attach_bytes:
            attachments.append((
                attach_field.filename,
                attach_bytes,
                attach_field.content_type or "application/octet-stream",
            ))

    # Générer le HTML du programme via le template email dédié
    pm_sorted = sorted([pm for pm in program.meetings if pm.meeting], key=lambda pm: pm.meeting.meeting_date)

    # QR codes PNG par tenue — pour intégration inline dans l'email (cid:)
    qr_pngs: dict[int, bytes] = {}
    for pm in pm_sorted:
        m = pm.meeting
        qr_url = pm.registration_url or _inscription_url(request, m.token)
        qr_pngs[m.id] = _qr_png(qr_url)

    # ── PDF du programme (pièce jointe systématique) ───────────────────────
    pdf_bytes = await _generate_program_pdf(request, program_id, program, pm_sorted, lodge, qr_pngs)
    if pdf_bytes:
        attachments.insert(0, (f"{program.title}.pdf", pdf_bytes, "application/pdf"))

    lodge_name = lodge.name if lodge else "La Loge"
    subject = f"[{lodge_name}] {program.title}"
    base_url = str(request.base_url).rstrip("/")

    # Ne jamais renvoyer à quelqu'un qui a déjà reçu CE programme avec succès —
    # permet de recliquer sur "Envoyer" après un envoi partiellement échoué
    # (ex : relais SMTP saturé en cours de route) sans spammer ceux qui l'ont
    # déjà reçu. Le sujet identifie le programme de façon fiable.
    from app.models.system import EmailLog, EmailStatus
    r_already_sent = await db.execute(
        select(EmailLog.recipient).where(
            EmailLog.subject == subject, EmailLog.status == EmailStatus.SENT
        )
    )
    already_sent = {r.strip().lower() for r in r_already_sent.scalars().all()}
    skipped = [r for r in recipients if r[1].strip().lower() in already_sent]
    recipients = [r for r in recipients if r[1].strip().lower() not in already_sent]

    if not recipients:
        return RedirectResponse(
            url=f"/programs/{program_id}?email_sent=0&email_already={len(skipped)}&email_attempted=0",
            status_code=303,
        )

    # QR codes en images inline (cid:) — référencées dans emails/programme.html
    inline_images = [
        (f"qr{meeting_id}", png, "image/png") for meeting_id, png in qr_pngs.items()
    ]

    from app.config import get_settings as _get_settings
    _imap_user = _get_settings().imap_user or None

    sent = 0
    for name, email, remove_url in recipients:
        greeting = f"Bonjour{' ' + name if name else ''},"
        html_content = templates.TemplateResponse(request, "emails/programme.html", {
            "program": program,
            "pm_sorted": pm_sorted,
            "lodge": lodge,
            "GRADE_LABELS": GRADE_LABELS,
            "date_civil": _date_civil,
            "inscription_url": lambda token: _inscription_url(request, token),
            "qr_src": lambda meeting_id: f"cid:qr{meeting_id}",
            "greeting": greeting,
            "base_url": base_url,
            "has_attachment": attachments is not None,
            "attachment_name": attachments[0][0] if attachments else None,
            "imap_inbox": _imap_user,
            "remove_url": remove_url,
        })
        html_str = html_content.body.decode("utf-8")

        # Texte alternatif plain-text
        text_lines = [greeting, "", program.title, ""]
        for pm in pm_sorted:
            m = pm.meeting
            url = pm.registration_url or _inscription_url(request, m.token)
            text_lines.append(f"△ {_date_civil(m.meeting_date)}")
            text_lines.append(f"   Inscription : {url}")
            text_lines.append("")
        if attachments:
            text_lines.append(f"📎 Pièce jointe : {attachments[0][0]}")
            text_lines.append("")
        text_lines.append(f"— {lodge_name}")
        text = "\n".join(text_lines)

        ok, _ = await _send_raw(
            email, subject, html_str, text,
            attachments=attachments or None,
            inline_images=inline_images or None,
        )
        if ok:
            sent += 1
        # Pause entre chaque envoi — un relais mutualisé (LWS/cPanel) rejette
        # en rafale les envois en masse ouverts trop vite (cf. NOTIFY_EMAIL_DELAY_MS
        # dans app/routers/messages.py, même logique ici).
        await asyncio.sleep(0.3)

    return RedirectResponse(
        url=f"/programs/{program_id}?email_sent={sent}&email_already={len(skipped)}&email_attempted={len(recipients)}",
        status_code=303,
    )
