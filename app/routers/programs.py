"""Router Programmes — génération mensuelle avec URL d'inscription et QR codes"""
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
    base = str(request.base_url).rstrip("/")
    return f"{base}/inscription/{token}"


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
        "email_sent": request.query_params.get("email_sent"),
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
    Génère une version HTML autonome consultable depuis la bibliothèque.
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

    # ── 1. Rendre le programme en HTML autonome ──────────────────────────────
    pm_sorted = sorted(program.meetings, key=lambda pm: pm.meeting.meeting_date)
    qr_codes: dict[int, str] = {}
    for pm in pm_sorted:
        url = _inscription_url(request, pm.meeting.token)
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
            "print_mode": True,
            "now": datetime.now(),
            # Inject Tailwind CDN pour le fichier autonome
            "_standalone": True,
        },
    )
    html_str = html_content.body.decode("utf-8")

    # ── 2. Sauvegarder le fichier HTML ───────────────────────────────────────
    safe_title = "".join(c for c in program.title if c.isalnum() or c in " -_").strip()
    filename    = f"{safe_title}.html"
    stored_name = f"{uuid.uuid4().hex}.html"
    dest_path   = PROG_UPLOAD_DIR / stored_name
    dest_path.write_text(html_str, encoding="utf-8")

    # ── 3. Trouver / créer le dossier GED ───────────────────────────────────
    if masonic_year:
        year_label = f"{masonic_year.start_date.year}-{masonic_year.end_date.year}"
    else:
        # Fallback : année civile du programme
        year_label = f"{program.year}-{program.year + 1}"

    ged_folder = await _find_ged_folder(db, year_label)

    # ── 4. Créer l'entrée Document dans la GED ──────────────────────────────
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
            existing.file_size = dest_path.stat().st_size
        else:
            doc = Document(
                folder_id=ged_folder.id,
                name=program.title,
                original_filename=filename,
                mime_type="text/html",
                file_size=dest_path.stat().st_size,
                storage_path=str(dest_path),
                status=DocStatus.PUBLISHED,
                author_id=member.id,
            )
            db.add(doc)

    # ── 5. Marquer le programme comme transmis ───────────────────────────────
    program.sent_at = datetime.now()
    program.sent_by_id = member.id
    program.pdf_path = str(dest_path)  # réutilise le champ pour stocker le chemin

    await db.commit()

    return RedirectResponse(
        url=f"/programs/{program_id}?transmitted=1",
        status_code=303,
    )


# ══════════════════════════════════════════════════════════════════════════════
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
    recipients = []
    if contact_ids:
        r = await db.execute(select(ExternalContact).where(ExternalContact.id.in_(contact_ids), ExternalContact.is_active == True))
        for c in r.scalars().all():
            recipients.append((c.name, c.email))
    for e in extra_emails:
        recipients.append(("", e))

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

    # ── PDF du programme (pièce jointe systématique) ──────────────────────
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
        from reportlab.lib.enums import TA_CENTER, TA_LEFT

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
        lodge_name = lodge.name if lodge else "Socrate — Raison et Progrès"
        obedience = lodge.obedience if lodge else "Grand Orient de France"
        orient = lodge.orient_city if lodge else ""
        loge_num = f" — R∴L∴ n°{lodge.loge_number}" if lodge and lodge.loge_number else ""
        rite = lodge.rite if lodge and lodge.rite else None

        header_text = [
            Paragraph(lodge_name, h1),
            Paragraph(f"Au nom et sous les auspices du {obedience}<br/>Or∴ de {orient}{loge_num}", sub),
        ]
        if rite:
            header_text.append(Paragraph(f"— ϕ — {rite} — ϕ —", sub))

        header_table = Table([[header_text]], colWidths=[doc.width])
        header_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), TEAL),
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
            clean_intro = _re.sub(r"<[^>]+>", " ", program.content_html).strip()
            story.append(Paragraph(clean_intro, body))
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
            title_str = f"△ {num_label}{type_label} du {_date_civil(m.meeting_date)} en Loge d'{grade_label}"

            card_rows = [[Paragraph(title_str, meeting_title)]]

            if m.agenda_html:
                import re as _re
                agenda_clean = _re.sub(r"<[^>]+>", " ", m.agenda_html).strip()
                card_rows.append([Paragraph(agenda_clean, small)])

            if m.degrees and len(m.degrees) > 1:
                for deg in m.degrees:
                    deg_label = deg.description or GRADE_LABELS.get(deg.grade.value, deg.grade.value)
                    card_rows.append([Paragraph(f"• {deg_label}", small)])

            if m.agape_enabled:
                agape_text = f"• Agape fraternelle à l'issue"
                if m.agape_location:
                    agape_text += f" — {m.agape_location}"
                agape_text += " <font color='#b45309'><b>(Réservation impérative)</b></font>"
                card_rows.append([Paragraph(agape_text, small)])

            card_rows.append([Paragraph(f"Inscription : {url}", url_style)])

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
            story.append(Paragraph("△ Ordre du jour commun à toutes les TTen∴", meeting_title))
            for line in lodge.common_agenda.split("\n"):
                stripped = line.strip()
                if stripped:
                    story.append(Paragraph(stripped, small))
            story.append(Spacer(1, 0.3*cm))

        # ── À noter ──
        if program.next_meetings_text:
            import re as _re
            note_clean = _re.sub(r"<[^>]+>", " ", program.next_meetings_text).strip()
            story.append(Paragraph(note_clean, body))
            story.append(Spacer(1, 0.3*cm))

        # ── Footer VM / Temple / Sec ──
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e5e7eb")))
        story.append(Spacer(1, 0.2*cm))

        vm_lines = [Paragraph("V∴M∴", footer_label)]
        if lodge and lodge.vm_name_display:
            vm_lines.append(Paragraph(lodge.vm_name_display, footer_val))
        if lodge and lodge.vm_email_display:
            vm_lines.append(Paragraph(lodge.vm_email_display, footer_val))

        temple_lines = [Paragraph("Temple", footer_label)]
        if lodge and lodge.temple_name:
            temple_lines.append(Paragraph(lodge.temple_name, footer_val))
        if lodge and lodge.temple_address:
            temple_lines.append(Paragraph(lodge.temple_address, footer_val))

        sec_lines = [Paragraph("Sec∴", footer_label)]
        if lodge and lodge.secretary_name_display:
            sec_lines.append(Paragraph(lodge.secretary_name_display, footer_val))
        if lodge and lodge.secretary_email_display:
            sec_lines.append(Paragraph(lodge.secretary_email_display, footer_val))

        footer_table = Table([[vm_lines, temple_lines, sec_lines]], colWidths=[doc.width/3]*3)
        footer_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(footer_table)

        doc.build(story)
        pdf_bytes = buf.getvalue()
        attachments.insert(0, (f"{program.title}.pdf", pdf_bytes, "application/pdf"))
    except Exception as _e:
        logger.warning("PDF non généré (ReportLab) : %s", _e, exc_info=True)

    lodge_name = lodge.name if lodge else "La Loge"
    subject = f"[{lodge_name}] {program.title}"
    base_url = str(request.base_url).rstrip("/")

    sent = 0
    for name, email in recipients:
        greeting = f"Bonjour{' ' + name if name else ''},"
        html_content = templates.TemplateResponse(request, "emails/programme.html", {
            "program": program,
            "pm_sorted": pm_sorted,
            "lodge": lodge,
            "GRADE_LABELS": GRADE_LABELS,
            "date_civil": _date_civil,
            "inscription_url": lambda token: _inscription_url(request, token),
            "greeting": greeting,
            "base_url": base_url,
            "has_attachment": attachments is not None,
            "attachment_name": attachments[0][0] if attachments else None,
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

        ok, _ = await _send_raw(email, subject, html_str, text, attachments=attachments or None)
        if ok:
            sent += 1

    return RedirectResponse(url=f"/programs/{program_id}?email_sent={sent}", status_code=303)
