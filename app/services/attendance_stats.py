"""Service — assiduité globale de la loge pour une année maçonnique.

Source de vérité unique, utilisée à la fois par le widget "Loge cette
année" du tableau de bord (app/main.py) et par la page détaillée
Présences & Assiduité (app/routers/attendance.py) — pour qu'ils affichent
toujours exactement le même pourcentage, au lieu de deux calculs divergents
(l'un naïf sur tous les enregistrements de présence, l'autre tenant compte
du grade de chaque tenue et de la fenêtre d'appartenance de chaque membre).
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import Member, MemberStatus, User, member_liable_for_year_condition
from app.models.lodge import MasonicYear
from app.models.meetings import Meeting, Attendance

_GRADE_ORDER = {"APPRENTI": 1, "COMPAGNON": 2, "MAITRE": 3, "ALL": 0}


@dataclass
class LodgeAttendanceStats:
    past_meetings: list
    active_members: list
    stats: dict = field(default_factory=dict)          # member_id -> {PRESENT/EXCUSED/ABSENT: n}
    grid: dict = field(default_factory=dict)            # member_id -> {meeting_id: status_value}
    member_applicable: dict = field(default_factory=dict)  # member_id -> {meeting_id, ...}
    expected: int = 0
    present: int = 0
    excused: int = 0
    absent: int = 0

    @property
    def past_ids(self) -> list[int]:
        return [m.id for m in self.past_meetings]

    @property
    def pct_present(self) -> int:
        return round(self.present * 100 / self.expected) if self.expected else 0

    def member_pct(self, member_id: int) -> Optional[int]:
        applicable = self.member_applicable.get(member_id, set())
        if not applicable:
            return None
        m_grid = self.grid.get(member_id, {})
        present = sum(1 for mid in applicable if m_grid.get(mid) == "PRESENT")
        return round(present * 100 / len(applicable))

    def member_present_total(self, member_id: int) -> tuple[int, int]:
        """(présences, tenues applicables) pour un membre donné."""
        applicable = self.member_applicable.get(member_id, set())
        m_grid = self.grid.get(member_id, {})
        present = sum(1 for mid in applicable if m_grid.get(mid) == "PRESENT")
        return present, len(applicable)


async def compute_lodge_attendance(db: AsyncSession, masonic_year: Optional[MasonicYear]) -> LodgeAttendanceStats:
    """Assiduité de la loge sur les tenues passées d'une année maçonnique.

    Tient compte du grade de chaque tenue (une tenue Maîtres n'est pas
    "attendue" pour un Apprenti), de la fenêtre d'appartenance de chaque
    membre (arrivée/départ en cours d'année — son absence après son départ
    ne doit pas pénaliser le taux), et exclut les comptes techniques admin.
    """
    year_filter = (Meeting.masonic_year_id == masonic_year.id) if masonic_year else True

    past_r = await db.execute(
        select(Meeting).where(year_filter, Meeting.meeting_date <= date.today())
        .order_by(Meeting.meeting_date)
    )
    past_meetings = past_r.scalars().all()
    past_ids = [m.id for m in past_meetings]

    admin_ids_r = await db.execute(
        select(User.member_id).where(User.is_admin == True, User.member_id.isnot(None))
    )
    admin_ids = {row[0] for row in admin_ids_r}

    liable_condition = (
        member_liable_for_year_condition(masonic_year.start_date)
        if masonic_year else Member.status == MemberStatus.ACTIVE
    )
    members_r = await db.execute(
        select(Member).where(liable_condition, Member.id.notin_(admin_ids))
        .order_by(Member.last_name, Member.first_name)
    )
    active_members = members_r.scalars().all()

    stats: dict = {}
    grid: dict = {}
    if past_ids:
        att_r = await db.execute(select(Attendance).where(Attendance.meeting_id.in_(past_ids)))
        for att in att_r.scalars().all():
            s = stats.setdefault(att.member_id, {"PRESENT": 0, "EXCUSED": 0, "ABSENT": 0})
            s[att.status.value] = s.get(att.status.value, 0) + 1
            grid.setdefault(att.member_id, {})[att.meeting_id] = att.status.value

    member_applicable: dict = {}
    for mbr in active_members:
        grade_val = mbr.masonic_grade.value if hasattr(mbr.masonic_grade, "value") else str(mbr.masonic_grade)
        start = mbr.membership_start_date
        left = mbr.status_date if mbr.status != MemberStatus.ACTIVE else None
        applicable = set()
        for mtg in past_meetings:
            if start and mtg.meeting_date < start:
                continue
            if left and mtg.meeting_date > left:
                continue
            mtg_grade = mtg.grade.value if hasattr(mtg.grade, "value") else str(mtg.grade)
            if mtg_grade == "ALL" or _GRADE_ORDER.get(grade_val, 0) >= _GRADE_ORDER.get(mtg_grade, 0):
                applicable.add(mtg.id)
        member_applicable[mbr.id] = applicable

    expected = present = excused = absent = 0
    for mbr in active_members:
        applic = member_applicable[mbr.id]
        expected += len(applic)
        m_grid = grid.get(mbr.id, {})
        for mid in applic:
            v = m_grid.get(mid, "")
            if v == "PRESENT":
                present += 1
            elif v == "EXCUSED":
                excused += 1
            elif v == "ABSENT":
                absent += 1

    return LodgeAttendanceStats(
        past_meetings=past_meetings, active_members=active_members,
        stats=stats, grid=grid, member_applicable=member_applicable,
        expected=expected, present=present, excused=excused, absent=absent,
    )
