"""Router Programmes — génération mensuelle avec URL d'inscription et QR codes"""
import io
from datetime import date, datetime
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

    # Tenues à venir (non verrouillées, dans le futur)
    rm = await db.execute(
        select(Meeting)
        .where(Meeting.meeting_date >= date.today())
        .order_by(Meeting.meeting_date)
    )
    upcoming = rm.scalars().all()

    current_month = date.today().month
    current_year  = date.today().year

    return templates.TemplateResponse(request, "pages/programs/create.html", {
        "current_member": member,
        "current_user": user,
        "years": years,
        "upcoming": upcoming,
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
    meeting_ids = form.getlist("meeting_ids")

    if not title:
        title = f"Programme — {MOIS_FR[month].capitalize()} {year + 4000} E∴L∴"

    program = Program(
        masonic_year_id=int(year_id) if year_id else None,
        title=title,
        month=month,
        year=year,
        content_html=notes or None,
        created_by_id=member.id,
    )
    db.add(program)
    await db.flush()

    for pos, mid in enumerate(meeting_ids):
        mid = int(mid)
        # Récupérer le token de la tenue pour générer l'URL
        mtg = await db.get(Meeting, mid)
        reg_url = _inscription_url(request, mtg.token) if mtg else None
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

    # Trier les tenues par date
    pm_sorted = sorted(program.meetings, key=lambda pm: pm.meeting.meeting_date)

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
