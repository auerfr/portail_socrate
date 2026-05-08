"""Router Programmes — génération mensuelle avec URL d'inscription et QR codes"""
import io
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Optional

import qrcode
import qrcode.image.svg

from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import require_auth, require_admin
from app.models.programs import Program, ProgramMeeting
from app.models.meetings import Meeting, MeetingType, MeetingGrade
from app.models.lodge import MasonicYear, LodgeSettings
from app.models.documents import DocFolder, DocSpace, DocStatus, Document

router = APIRouter(prefix="/programs", tags=["programs"])
templates = Jinja2Templates(directory="app/templates")

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

    return templates.TemplateResponse(request, "pages/programs/list.html", {
        "current_member": member,
        "current_user": user,
        "programs": programs,
        "MOIS_FR": MOIS_FR,
        "is_admin": user.is_admin,
        "now": datetime.now(),
    })


# ══════════════════════════════════════════════════════════════════════════════
# CRÉATION
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/create", response_class=HTMLResponse)
async def programs_create_form(
    request: Request,
    ctx: Annotated[object, Depends(require_admin)],
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
    })


@router.post("/create")
async def programs_create(
    request: Request,
    ctx: Annotated[object, Depends(require_admin)],
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
        url = pm.registration_url or _inscription_url(request, pm.meeting.token)
        qr_codes[pm.meeting_id] = _qr_svg(url)

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
        "print_mode": print_mode,
        "now": datetime.now(),
    })


# ══════════════════════════════════════════════════════════════════════════════
# ÉDITION
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/{program_id}/edit", response_class=HTMLResponse)
async def program_edit_form(
    program_id: int,
    request: Request,
    ctx: Annotated[object, Depends(require_admin)],
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
    })


@router.post("/{program_id}/edit")
async def program_edit_save(
    program_id: int,
    request: Request,
    ctx: Annotated[object, Depends(require_admin)],
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
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    program = await db.get(Program, program_id)
    if not program:
        raise HTTPException(404)
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
    ctx: Annotated[object, Depends(require_admin)],
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
