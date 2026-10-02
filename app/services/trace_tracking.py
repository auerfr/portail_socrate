"""Service — suivi des tracés : où en est le tracé de chaque tenue passée ?

Combine le circuit du portail (MeetingReport : brouillon → soumis → approuvé
→ adopté → archivé avec PDF signé) et les tracés déposés directement dans la
GED sous la nomenclature « SRP tracé tenue NNN - JJ mois AAAA … » (anciens
tracés, ou tracés jamais passés par le circuit).
"""
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.documents import Document
from app.models.identity import Member
from app.models.meetings import Meeting
from app.models.reports import MeetingReport, ReportStatus

# État → (libellé, ordre de priorité : plus petit = à traiter en premier, couleur)
STATES = {
    "MISSING":   ("Aucun tracé", 0, "red"),
    "DRAFT":     ("Brouillon en cours", 1, "amber"),
    "SUBMITTED": ("Soumis au V∴M∴", 2, "amber"),
    "APPROVED":  ("Approuvé — à adopter en tenue", 3, "blue"),
    "ADOPTED":   ("Adopté — PDF signé attendu", 4, "blue"),
    "GED_ONLY":  ("Dans la GED (hors circuit)", 5, "gray"),
    "ARCHIVED":  ("Archivé, PDF signé", 6, "green"),
}
TO_CHASE = {"MISSING", "DRAFT", "SUBMITTED", "APPROVED", "ADOPTED"}

_MOIS = {"jan": 1, "janv": 1, "janvier": 1, "fev": 2, "fevr": 2, "fevrier": 2, "mar": 3, "mars": 3,
         "avr": 4, "avril": 4, "mai": 5, "jun": 6, "juin": 6, "jul": 7, "juil": 7, "juillet": 7,
         "aou": 8, "aout": 8, "sep": 9, "sept": 9, "septembre": 9, "oct": 10, "octobre": 10,
         "nov": 11, "novembre": 11, "dec": 12, "decembre": 12}
_TRACE_RE = re.compile(r"trac[ée] tenue (\d{3})\s*-?\s*(\d{1,2}) (\w+)\.? (\d{4})", re.I)


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


@dataclass
class TraceRow:
    meeting: Meeting
    state: str
    report: Optional[MeetingReport]
    author: Optional[Member]
    ged_doc_id: Optional[int]
    days_since: int

    @property
    def label(self) -> str:
        return STATES[self.state][0]

    @property
    def color(self) -> str:
        return STATES[self.state][2]

    @property
    def to_chase(self) -> bool:
        return self.state in TO_CHASE


async def compute_trace_tracking(db: AsyncSession, year_id: Optional[int]) -> list[TraceRow]:
    today = date.today()
    q = select(Meeting).where(Meeting.meeting_date <= today)
    if year_id:
        q = q.where(Meeting.masonic_year_id == year_id)
    meetings = list((await db.execute(q)).scalars())
    if not meetings:
        return []
    ids = [m.id for m in meetings]

    reports = {r.meeting_id: r for r in (await db.execute(
        select(MeetingReport).where(MeetingReport.meeting_id.in_(ids))
    )).scalars()}
    author_ids = {r.author_id for r in reports.values() if r.author_id}
    authors = {m.id: m for m in (await db.execute(
        select(Member).where(Member.id.in_(author_ids))
    )).scalars()} if author_ids else {}

    # Tracés de la GED, repérés par leur nom (numéro + date de la tenue)
    ged_by_date: dict[date, list[tuple[int, int, str]]] = {}
    for doc_id, name, mime in await db.execute(
        select(Document.id, Document.name, Document.mime_type).where(Document.deleted_at.is_(None))
    ):
        m = _TRACE_RE.search(name or "")
        if not m:
            continue
        month = _MOIS.get(_ascii(m[3]))
        try:
            d = date(int(m[4]), month, int(m[2])) if month else None
        except ValueError:
            d = None
        if d:
            # Le PDF passe avant le Word
            ged_by_date.setdefault(d, []).append((0 if mime == "application/pdf" else 1, doc_id, name))

    rows = []
    for mtg in meetings:
        rep = reports.get(mtg.id)
        ged = sorted(ged_by_date.get(mtg.meeting_date, []))
        if rep and rep.status == ReportStatus.ARCHIVE and rep.signed_pdf_doc_id:
            state = "ARCHIVED"
        elif rep and rep.status in (ReportStatus.ARCHIVE, ReportStatus.ADOPTE):
            state = "ADOPTED"
        elif rep and rep.status == ReportStatus.APPROUVE and rep.approved_at is None and rep.archived_doc_id:
            # Rattaché à un fichier de la GED par la reprise d'historique, jamais passé par le circuit
            state = "GED_ONLY"
        elif rep and rep.status == ReportStatus.APPROUVE:
            state = "APPROVED"
        elif rep and rep.status == ReportStatus.SOUMIS:
            state = "SUBMITTED"
        elif ged:
            state = "GED_ONLY"
        elif rep or (mtg.compte_rendu_html or "").strip():
            state = "DRAFT"
        else:
            state = "MISSING"
        rows.append(TraceRow(
            meeting=mtg, state=state, report=rep,
            author=authors.get(rep.author_id) if rep and rep.author_id else None,
            ged_doc_id=ged[0][1] if ged else (rep.archived_doc_id if rep else None),
            days_since=(today - mtg.meeting_date).days,
        ))

    # À relancer d'abord (du plus ancien au plus récent), puis le reste du plus récent au plus ancien
    rows.sort(key=lambda r: (not r.to_chase,
                             r.meeting.meeting_date.toordinal() * (1 if r.to_chase else -1)))
    return rows
