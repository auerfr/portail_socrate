"""Service — statistiques de la loge depuis l'allumage des feux.

Alimente la page « Statistiques de la loge », ouverte à tous les membres :
uniquement des agrégats (effectifs, moyennes, pourcentages, comptes), jamais
le nom d'un frère ou d'une sœur. Les noms de loges, d'orients et
d'obédiences des maçons passants sont des données d'organisation, pas de
personnes.
"""
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import mean
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.documents import DocFolder, Document
from app.models.identity import LODGE_FOUNDING_DATE, Member, MemberStatus, User
from app.models.lodge import MasonicYear
from app.models.meetings import (
    Attendance, AttendanceStatus, Meeting, MeetingVisitor, Visitor, VisitorStatus,
)
from app.services.attendance_stats import compute_lodge_attendance

ALLUMAGE_DES_FEUX = LODGE_FOUNDING_DATE

JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
MOIS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
TYPE_LABELS = {
    "SOLENNELLE": "Tenues solennelles", "BLANCHE": "Tenues blanches", "INSTRUCTION": "Instruction",
    "INITIATION": "Initiations", "PASSAGE": "Passages", "ELEVATION": "Élévations",
    "INSTALLATION": "Installations", "ELECTION": "Élections", "FETE": "Tenues de table / fêtes",
    "EXTRA": "Extraordinaires",
}


def _years_between(d1: date, d2: date) -> float:
    return (d2 - d1).days / 365.25


def _grade_at(m: Member, d: date) -> Optional[str]:
    """Grade d'un membre à une date, d'après ses dates d'augmentation de salaire
    (à défaut, son grade actuel)."""
    g = m.masonic_grade.value if hasattr(m.masonic_grade, "value") else str(m.masonic_grade)
    if g == "MAITRE" and m.master_date and m.master_date > d:
        g = "COMPAGNON"
    if g == "COMPAGNON" and m.companion_date and m.companion_date > d:
        g = "APPRENTI"
    return g


def _joined(m: Member) -> date:
    """Date d'entrée dans la loge (jamais avant l'allumage des feux)."""
    start = m.membership_start_date or ALLUMAGE_DES_FEUX
    return max(start, ALLUMAGE_DES_FEUX)


def _left(m: Member) -> Optional[date]:
    """Date de départ, ou None si le membre est toujours là."""
    if m.status == MemberStatus.ACTIVE:
        return None
    return m.status_date or date.min  # départ sans date : considéré comme ancien


def _is_member_on(m: Member, d: date) -> bool:
    left = _left(m)
    return _joined(m) <= d and (left is None or left > d)


def _planche_key(name: str) -> str:
    base = re.sub(r"\.[a-z0-9]{2,5}$", "", name.lower())
    base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", base)


async def compute_lodge_statistics(db: AsyncSession) -> dict:
    today = date.today()

    # ── Données de base ────────────────────────────────────────────────────
    admin_ids = {r[0] for r in await db.execute(
        select(User.member_id).where(User.is_admin == True, User.member_id.isnot(None))
    )}
    members = [m for m in (await db.execute(select(Member))).scalars() if m.id not in admin_ids]

    years = list((await db.execute(select(MasonicYear).order_by(MasonicYear.start_date))).scalars())
    years = [y for y in years if y.start_date <= today]

    meetings = list((await db.execute(
        select(Meeting).where(Meeting.meeting_date <= today).order_by(Meeting.meeting_date, Meeting.id)
    )).scalars())
    meeting_by_id = {m.id: m for m in meetings}

    present_by_meeting = Counter(
        mid for mid, member_id in await db.execute(
            select(Attendance.meeting_id, Attendance.member_id)
            .where(Attendance.status == AttendanceStatus.PRESENT)
        )
        if member_id not in admin_ids
    )

    visits = list(await db.execute(
        select(MeetingVisitor.meeting_id, MeetingVisitor.visitor_id)
        .where(MeetingVisitor.status == VisitorStatus.CONFIRMED)
    ))
    visits = [(mid, vid) for mid, vid in visits if mid in meeting_by_id]
    visitors = {v.id: v for v in (await db.execute(select(Visitor))).scalars()}
    visitors_by_meeting = Counter(mid for mid, _ in visits)

    # ── Chiffres clés d'aujourd'hui ────────────────────────────────────────
    current = [m for m in members if _is_member_on(m, today)]
    soeurs = sum(1 for m in current if m.civility == "S")
    freres = sum(1 for m in current if m.civility == "F")
    ages = [_years_between(m.birth_date, today) for m in current if m.birth_date]
    seniority_masonic = [_years_between(m.initiation_date, today) for m in current if m.initiation_date]
    seniority_lodge = [_years_between(_joined(m), today) for m in current]

    def buckets(values, edges, labels):
        counts = [0] * len(labels)
        for v in values:
            for i, upper in enumerate(edges):
                if v < upper:
                    counts[i] += 1
                    break
            else:
                counts[-1] += 1
        return {"labels": labels, "values": counts}

    age_dist = buckets(ages, [30, 40, 50, 60, 70, 80],
                       ["< 30 ans", "30-39", "40-49", "50-59", "60-69", "70-79", "80 ans et +"])
    masonic_dist = buckets(seniority_masonic, [3, 10, 20, 30],
                           ["< 3 ans", "3-9 ans", "10-19 ans", "20-29 ans", "30 ans et +"])
    grades_now = Counter(_grade_at(m, today) for m in current)

    # ── Par année maçonnique ───────────────────────────────────────────────
    per_year = []
    for i, y in enumerate(years):
        window_end = years[i + 1].start_date - timedelta(days=1) if i + 1 < len(years) else y.start_date.replace(year=y.start_date.year + 1) - timedelta(days=1)
        snapshot = min(y.end_date or window_end, today)
        in_window = lambda d: d is not None and y.start_date <= d <= window_end  # noqa: E731

        snap = [m for m in members if _is_member_on(m, snapshot)]
        snap_ages = [_years_between(m.birth_date, snapshot) for m in snap if m.birth_date]
        snap_grades = Counter(_grade_at(m, snapshot) for m in snap)

        founders = [m for m in members if _joined(m) == ALLUMAGE_DES_FEUX] if in_window(ALLUMAGE_DES_FEUX) else []
        arrivals = [m for m in members if in_window(_joined(m)) and m not in founders]
        initiations = [m for m in members if in_window(m.initiation_date) and m.initiation_date >= ALLUMAGE_DES_FEUX]

        year_meetings = [m for m in meetings if m.masonic_year_id == y.id]
        stats = await compute_lodge_attendance(db, y)
        year_visits = [(mid, vid) for mid, vid in visits if meeting_by_id[mid].masonic_year_id == y.id]

        per_year.append({
            "label": y.label,
            "is_current": y.is_current,
            "snapshot": snapshot.isoformat(),
            "members": len(snap),
            "soeurs": sum(1 for m in snap if m.civility == "S"),
            "freres": sum(1 for m in snap if m.civility == "F"),
            "apprentis": snap_grades.get("APPRENTI", 0),
            "compagnons": snap_grades.get("COMPAGNON", 0),
            "maitres": snap_grades.get("MAITRE", 0),
            "avg_age": round(mean(snap_ages), 1) if len(snap_ages) >= 3 else None,
            "founders": len(founders),
            "initiations": len(initiations),
            "affiliations": len([m for m in arrivals if m not in initiations]),
            "departures": sum(1 for m in members if _left(m) and in_window(_left(m))),
            "passages": sum(1 for m in members if in_window(m.companion_date) and m.companion_date >= ALLUMAGE_DES_FEUX),
            "elevations": sum(1 for m in members if in_window(m.master_date) and m.master_date >= ALLUMAGE_DES_FEUX),
            "meetings": len(year_meetings),
            "meeting_types": dict(Counter(m.type.value for m in year_meetings)),
            "attendance_pct": stats.pct_present if stats.expected else None,
            "avg_present": round(mean(present_by_meeting.get(m.id, 0) for m in year_meetings), 1) if year_meetings else 0,
            "visits": len(year_visits),
            "unique_visitors": len({vid for _, vid in year_visits}),
            "lodges": len({visitors[vid].lodge_name for _, vid in year_visits if visitors.get(vid) and visitors[vid].lodge_name}),
        })

    # Progression de l'effectif d'une année sur l'autre (fin d'année à fin d'année)
    for prev, y in zip(per_year, per_year[1:]):
        y["growth"] = y["members"] - prev["members"]
        y["growth_pct"] = round(y["growth"] * 100 / prev["members"], 1) if prev["members"] else None
    if per_year:
        per_year[0]["growth"] = per_year[0]["growth_pct"] = None

    # ── Tenue par tenue ────────────────────────────────────────────────────
    timeline = [{
        "date": m.meeting_date.strftime("%d/%m/%Y"),
        "label": f"{m.meeting_date.day} {MOIS[m.meeting_date.month - 1]} {m.meeting_date.year}",
        "members": present_by_meeting.get(m.id, 0),
        "visitors": visitors_by_meeting.get(m.id, 0),
    } for m in meetings]

    record = max(meetings, key=lambda m: present_by_meeting.get(m.id, 0) + visitors_by_meeting.get(m.id, 0), default=None)
    record_info = {
        "date": record.meeting_date,
        "title": record.title,
        "total": present_by_meeting.get(record.id, 0) + visitors_by_meeting.get(record.id, 0),
        "visitors": visitors_by_meeting.get(record.id, 0),
    } if record else None

    weekday = defaultdict(list)
    for m in meetings:
        weekday[m.meeting_date.weekday()].append(m)
    weekday_stats = [{
        "day": JOURS[d],
        "count": len(ms),
        "members": round(mean(present_by_meeting.get(m.id, 0) for m in ms), 1),
        "visitors": round(mean(visitors_by_meeting.get(m.id, 0) for m in ms), 1),
    } for d, ms in sorted(weekday.items()) if len(ms) >= 3]

    month = defaultdict(list)
    for m in meetings:
        month[m.meeting_date.month].append(present_by_meeting.get(m.id, 0) + visitors_by_meeting.get(m.id, 0))
    month_order = [9, 10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8]
    month_stats = [{"month": MOIS[mo - 1], "avg": round(mean(month[mo]), 1)} for mo in month_order if month[mo]]

    type_totals = Counter(m.type.value for m in meetings)

    # ── Maçons passants ────────────────────────────────────────────────────
    visits_per_visitor = Counter(vid for _, vid in visits)
    obediences = Counter((visitors[vid].obedience or "").strip().upper() or "Non précisée"
                         for _, vid in visits if vid in visitors)
    top_obed = obediences.most_common(5)
    others = sum(obediences.values()) - sum(n for _, n in top_obed)
    if others:
        top_obed.append(("Autres", others))
    orients = Counter(visitors[vid].orient_city for _, vid in visits if vid in visitors and visitors[vid].orient_city)
    lodges = Counter(visitors[vid].lodge_name for _, vid in visits if vid in visitors and visitors[vid].lodge_name)

    # ── Planches (bibliothèque de la GED) ──────────────────────────────────
    folders = list(await db.execute(select(DocFolder.id, DocFolder.name)))
    planche_folders = {}
    for fid, name in folders:
        n = (name or "").lower()
        if "planche" in n and "programme" not in n:
            if "comp" in n:
                planche_folders[fid] = "Compagnon"
            elif "app" in n:
                planche_folders[fid] = "Apprenti"
            elif "m∴" in n or "maître" in n or "maitre" in n or "milieu" in n:
                planche_folders[fid] = "Maître"
            else:
                planche_folders[fid] = "Autre"
    planches_by_year: dict[int, Counter] = defaultdict(Counter)
    seen = set()
    if planche_folders:
        for name, fid in await db.execute(
            select(Document.name, Document.folder_id)
            .where(Document.folder_id.in_(planche_folders), Document.deleted_at.is_(None))
        ):
            key = _planche_key(name or "")
            if not key or key in seen:
                continue
            seen.add(key)
            m = re.search(r"\b(19\d{2}|20\d{2})\b", name or "")
            if m and int(m.group(1)) >= ALLUMAGE_DES_FEUX.year:
                planches_by_year[int(m.group(1))][planche_folders[fid]] += 1
    planche_years = sorted(planches_by_year)
    planche_grades = [g for g in ("Apprenti", "Compagnon", "Maître", "Autre")
                      if any(planches_by_year[y][g] for y in planche_years)]

    # ── Synthèse ───────────────────────────────────────────────────────────
    att_years = [y for y in per_year if y["attendance_pct"] is not None]
    best_year = max(att_years, key=lambda y: y["attendance_pct"], default=None)

    return {
        "today": today,
        "allumage": ALLUMAGE_DES_FEUX,
        "lodge_age_years": _years_between(ALLUMAGE_DES_FEUX, today),
        "kpi": {
            "members": len(current),
            "soeurs": soeurs,
            "freres": freres,
            "pct_soeurs": round(soeurs * 100 / len(current)) if current else 0,
            "avg_age": round(mean(ages)) if len(ages) >= 3 else None,
            "ages_known": len(ages),
            "avg_masonic": round(mean(seniority_masonic)) if len(seniority_masonic) >= 3 else None,
            "masonic_known": len(seniority_masonic),
            "total_masonic_years": round(sum(seniority_masonic)),
            "avg_lodge": round(mean(seniority_lodge), 1) if seniority_lodge else None,
            "meetings": len(meetings),
            "visits": len(visits),
            "unique_visitors": len(visits_per_visitor),
            "loyal_visitors": sum(1 for n in visits_per_visitor.values() if n >= 2),
            "distinct_lodges": len(lodges),
            "distinct_orients": len(orients),
            "avg_attendance": round(mean(y["attendance_pct"] for y in att_years)) if att_years else None,
            "planches": sum(sum(c.values()) for c in planches_by_year.values()),
        },
        "grades_now": [grades_now.get("APPRENTI", 0), grades_now.get("COMPAGNON", 0), grades_now.get("MAITRE", 0)],
        "age_dist": age_dist,
        "masonic_dist": masonic_dist,
        "per_year": per_year,
        "type_keys": [t for t, _ in type_totals.most_common()],
        "type_labels": TYPE_LABELS,
        "timeline": timeline,
        "record": record_info,
        "best_year": best_year,
        "weekday_stats": weekday_stats,
        "month_stats": month_stats,
        "obediences": top_obed,
        "top_orients": orients.most_common(10),
        "top_lodges": lodges.most_common(10),
        "planche_years": planche_years,
        "planche_grades": planche_grades,
        "planches_by_year": {y: dict(c) for y, c in planches_by_year.items()},
        # Base de la projection de fin d'année : effectif à la fin de l'année précédente
        "previous_year": next((y for y in reversed(per_year) if not y["is_current"]), None)
                         if per_year and per_year[-1]["is_current"] else (per_year[-1] if per_year else None),
        "founders_total": sum(1 for m in members if m.is_founder),
        "founders_active": sum(1 for m in current if m.is_founder),
    }
