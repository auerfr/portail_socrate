"""Router — Sondages & Votes"""
from datetime import datetime, timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, delete, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_auth
from app.models.content import Poll, PollOption, PollVote
from app.models.groups import LodgeGroup, GroupMembership, GroupType
from app.models.identity import Member, MasonicGrade, LodgeFunction, MemberStatus

router = APIRouter(prefix="/polls", tags=["polls"])
from app.template_engine import templates

_GRADE_ORDER = {"APPRENTI": 1, "COMPAGNON": 2, "MAITRE": 3}

_OFFICER_FUNCTIONS = {
    LodgeFunction.VM, LodgeFunction.PREMIER_S, LodgeFunction.SECOND_S,
    LodgeFunction.ORATEUR, LodgeFunction.SECRETAIRE, LodgeFunction.TRESORIER,
    LodgeFunction.EXPERT, LodgeFunction.MAITRE_CEREMONIES, LodgeFunction.HARMONISTE,
    LodgeFunction.HOSPITALIER, LodgeFunction.TUILEUR, LodgeFunction.ARCHITECTE,
    LodgeFunction.MAITRE_BANQUETS,
}


def _parse_target(target: str) -> tuple[Optional[str], Optional[int]]:
    if not target:
        return None, None
    if target.startswith("group:"):
        try:
            return None, int(target[6:])
        except ValueError:
            return None, None
    return target, None


def _target_value(poll: Poll) -> str:
    if poll.target_group_id:
        return f"group:{poll.target_group_id}"
    return poll.min_grade or ""


_JOURS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
            "août", "septembre", "octobre", "novembre", "décembre"]


def _format_slot_label(start: datetime, end: Optional[datetime]) -> str:
    jour = _JOURS_FR[start.weekday()].capitalize()
    mois = _MOIS_FR[start.month - 1]
    txt = f"{jour} {start.day} {mois} {start.year} · {start.strftime('%Hh%M')}"
    if end:
        txt += f"–{end.strftime('%Hh%M')}"
    return txt


async def _check_schedule_conflicts(options: list["PollOption"], db: AsyncSession) -> dict[int, list[str]]:
    """Pour un sondage SCHEDULE, détecte les créneaux proposés qui chevauchent
    un événement d'agenda ou une tenue déjà planifiée. Recalculé à chaque
    affichage (l'agenda peut évoluer après la création du sondage)."""
    from app.models.calendar import Event
    from app.models.meetings import Meeting

    slots = [(opt.id, opt.slot_start, opt.slot_end) for opt in options if opt.slot_start]
    if not slots:
        return {}

    lo = min(s[1] for s in slots).date()
    hi = max((s[2] or s[1]) for s in slots).date()

    ev_r = await db.execute(
        select(Event).where(
            Event.date_start >= datetime.combine(lo, datetime.min.time()),
            Event.date_start <= datetime.combine(hi, datetime.max.time()),
        )
    )
    events = ev_r.scalars().all()
    mt_r = await db.execute(
        select(Meeting).where(Meeting.meeting_date >= lo, Meeting.meeting_date <= hi)
    )
    meetings = mt_r.scalars().all()

    conflicts: dict[int, list[str]] = {}
    for opt_id, start, end in slots:
        end_eff = end or (start + timedelta(hours=2))
        msgs = []
        for ev in events:
            ev_end = ev.date_end or (ev.date_start + timedelta(hours=2))
            if ev.date_start < end_eff and ev_end > start:
                msgs.append(f"Événement « {ev.title} »")
        for mt in meetings:
            if mt.meeting_date == start.date():
                msgs.append("Tenue" + (f" « {mt.title} »" if mt.title else "") + f" du {mt.meeting_date.strftime('%d/%m/%Y')}")
        if msgs:
            conflicts[opt_id] = msgs
    return conflicts


async def _check_group_access(member: Member, group_id: int, db: AsyncSession) -> bool:
    group = await db.get(LodgeGroup, group_id)
    if not group:
        return False
    if group.group_type == GroupType.GRADE:
        if group.grade_filter is None:
            return True
        return bool(member.masonic_grade and member.masonic_grade.value == group.grade_filter)
    elif group.group_type == GroupType.COUNCIL:
        return member.lodge_function in _OFFICER_FUNCTIONS
    elif group.group_type == GroupType.PAIR:
        import json
        functions = set(json.loads(group.function_filter or "[]"))
        return bool(member.lodge_function and member.lodge_function.value in functions)
    else:
        r = await db.execute(
            select(GroupMembership).where(
                GroupMembership.group_id == group_id,
                GroupMembership.member_id == member.id,
            )
        )
        return r.scalar_one_or_none() is not None


async def _can_access(poll: Poll, member: Member, is_admin: bool, db: AsyncSession) -> bool:
    if is_admin:
        return True
    if poll.target_member_ids:
        return member.id in poll.target_member_ids
    if poll.target_group_id:
        return await _check_group_access(member, poll.target_group_id, db)
    if poll.min_grade:
        return _GRADE_ORDER.get(member.masonic_grade.value, 0) >= _GRADE_ORDER.get(poll.min_grade, 0)
    return True


def _can_manage(member: Member, is_admin: bool) -> bool:
    return True  # Tous les membres actifs peuvent créer un sondage


def _is_open(poll: Poll) -> bool:
    if poll.ends_at and poll.ends_at < datetime.now():
        return False
    return True


async def _load_groups(db: AsyncSession) -> list[LodgeGroup]:
    r = await db.execute(select(LodgeGroup).order_by(LodgeGroup.name))
    return r.scalars().all()


@router.get("/", response_class=HTMLResponse)
async def polls_list(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    r = await db.execute(
        select(Poll)
        .options(selectinload(Poll.options), selectinload(Poll.votes))
        .order_by(Poll.created_at.desc())
    )
    all_polls = r.scalars().all()
    polls = []
    for p in all_polls:
        if await _can_access(p, member, user.is_admin, db):
            polls.append(p)

    my_votes_r = await db.execute(
        select(PollVote).where(PollVote.member_id == member.id)
    )
    my_votes = my_votes_r.scalars().all()
    voted_poll_ids = {v.poll_id for v in my_votes}

    return templates.TemplateResponse(request, "pages/polls/list.html", {
        "current_member": member,
        "current_user": user,
        "polls": polls,
        "voted_poll_ids": voted_poll_ids,
        "is_open": _is_open,
        "can_manage": _can_manage(member, user.is_admin),
        "now": datetime.now(),
    })


@router.get("/new", response_class=HTMLResponse)
async def polls_new_form(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not _can_manage(member, user.is_admin):
        raise HTTPException(status_code=403)
    r_members = await db.execute(
        select(Member).where(Member.status == MemberStatus.ACTIVE)
        .order_by(Member.last_name, Member.first_name)
    )
    return templates.TemplateResponse(request, "pages/polls/form.html", {
        "current_member": member,
        "current_user": user,
        "poll": None,
        "groups": await _load_groups(db),
        "target_value": "",
        "active_members": r_members.scalars().all(),
    })


@router.post("/new")
async def polls_create(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    title: str = Form(""),
    description: str = Form(""),
    options_raw: str = Form(""),
    is_multiple: str = Form(""),
    is_anonymous: str = Form(""),
    is_public_vote: str = Form(""),
    target: str = Form(""),
    ends_at: str = Form(""),
    notify_members: str = Form(""),
    vote_type: str = Form("CHOICE"),
    rating_winners: str = Form("3"),
    member_ids: str = Form(""),
):
    user, member = ctx
    if not _can_manage(member, user.is_admin):
        raise HTTPException(status_code=403)

    title = title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Titre requis")

    ea = None
    if ends_at.strip():
        try:
            ea = datetime.fromisoformat(ends_at)
        except ValueError:
            pass

    target_member_ids = None
    if target == "members":
        try:
            target_member_ids = [int(x) for x in member_ids.split(",") if x.strip()]
        except ValueError:
            target_member_ids = []
        if not target_member_ids:
            raise HTTPException(status_code=400, detail="Sélectionnez au moins un membre")
        min_grade, target_group_id = None, None
    else:
        min_grade, target_group_id = _parse_target(target)

    vt = vote_type if vote_type in ("RANKING", "SCHEDULE") else "CHOICE"
    try:
        r_winners = max(1, int(rating_winners))
    except ValueError:
        r_winners = 3

    poll = Poll(
        title=title,
        description=description.strip() or None,
        is_multiple=(bool(is_multiple) and vt == "CHOICE") or vt == "SCHEDULE",
        is_anonymous=bool(is_anonymous),
        is_public_vote=bool(is_public_vote),
        min_grade=min_grade,
        target_group_id=target_group_id,
        target_member_ids=target_member_ids,
        ends_at=ea,
        created_by_id=member.id,
        vote_type=vt,
        rating_winners=r_winners if vt == "RANKING" else None,
    )
    db.add(poll)
    await db.flush()

    if vt == "SCHEDULE":
        form = await request.form()
        slot_dates = form.getlist("slot_date")
        slot_start_times = form.getlist("slot_start_time")
        slot_end_times = form.getlist("slot_end_time")
        i = 0
        for d, st, en in zip(slot_dates, slot_start_times, slot_end_times):
            if not d.strip() or not st.strip():
                continue
            try:
                start_dt = datetime.fromisoformat(f"{d}T{st}")
            except ValueError:
                continue
            end_dt = None
            if en.strip():
                try:
                    end_dt = datetime.fromisoformat(f"{d}T{en}")
                except ValueError:
                    end_dt = None
            db.add(PollOption(
                poll_id=poll.id, label=_format_slot_label(start_dt, end_dt),
                order_position=i, slot_start=start_dt, slot_end=end_dt,
            ))
            i += 1
        if i == 0:
            await db.rollback()
            raise HTTPException(status_code=400, detail="Ajoutez au moins un créneau")
    else:
        labels = [l.strip() for l in options_raw.splitlines() if l.strip()]
        for i, label in enumerate(labels):
            db.add(PollOption(poll_id=poll.id, label=label, order_position=i))

    await db.commit()

    if notify_members:
        from app.utils.notifications import send_notification
        view_url = str(request.base_url).rstrip("/") + f"/polls/{poll.id}"
        await send_notification(
            db, member.id,
            f"🗳️ Nouveau sondage : {poll.title}",
            f"Un nouveau sondage vous attend :\n\n{poll.title}\n\n{view_url}",
            min_grade=poll.min_grade,
            target_group_id=poll.target_group_id,
            member_ids=poll.target_member_ids,
            push_url=f"/polls/{poll.id}",
            push_body=f"Cliquez pour voter — {poll.title}",
        )
        await db.commit()

    return RedirectResponse(url=f"/polls/{poll.id}", status_code=303)


async def _compute_results(poll: Poll, my_option_ids: set, db: AsyncSession) -> tuple[list, int]:
    """Calcule les résultats d'un sondage (CHOICE ou RANKING), factorisé pour
    être partagé entre l'affichage web et l'export PDF."""
    if poll.vote_type == "RANKING":
        n_options = len(poll.options)
        results = []
        for opt in sorted(poll.options, key=lambda o: o.order_position):
            ranks = [v.score for v in poll.votes if v.option_id == opt.id and v.score is not None]
            avg = round(sum(ranks) / len(ranks), 2) if ranks else None
            voters = []
            if poll.is_public_vote and not poll.is_anonymous:
                voter_ids = [v.member_id for v in poll.votes if v.option_id == opt.id and v.member_id]
                if voter_ids:
                    vr = await db.execute(select(Member).where(Member.id.in_(voter_ids)))
                    voters = vr.scalars().all()
            results.append({
                "option": opt,
                "count": len(ranks),
                "avg": avg,
                # Barre visuelle : rang 1 (préféré) = 100%, rang n = ~0%.
                "pct": round((1 - (avg - 1) / (n_options - 1)) * 100) if avg and n_options > 1 else (100 if avg else 0),
                "is_mine": opt.id in my_option_ids,
                "voters": voters,
            })
        # Rang moyen le plus BAS = option la plus préférée → en tête.
        results.sort(key=lambda r: (r["avg"] is None, r["avg"]))
        n_winners = poll.rating_winners or 3
        for i, r in enumerate(results):
            r["is_winner"] = i < n_winners and r["avg"] is not None
            r["rank"] = i + 1
        total_votes = max((r["count"] for r in results), default=0)
    else:
        total_votes = len(poll.votes)
        results = []
        if poll.vote_type == "SCHEDULE":
            opts_sorted = sorted(poll.options, key=lambda o: o.slot_start or datetime.max)
        else:
            opts_sorted = sorted(poll.options, key=lambda o: o.order_position)
        for opt in opts_sorted:
            count = sum(1 for v in poll.votes if v.option_id == opt.id)
            pct = round(count * 100 / total_votes) if total_votes else 0
            voters = []
            if poll.is_public_vote and not poll.is_anonymous:
                voter_ids = [v.member_id for v in poll.votes if v.option_id == opt.id and v.member_id]
                if voter_ids:
                    vr = await db.execute(select(Member).where(Member.id.in_(voter_ids)))
                    voters = vr.scalars().all()
            results.append({
                "option": opt,
                "count": count,
                "pct": pct,
                "is_mine": opt.id in my_option_ids,
                "voters": voters,
            })
    return results, total_votes


@router.get("/{poll_id}", response_class=HTMLResponse)
async def poll_detail(
    poll_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    poll = await db.get(Poll, poll_id, options=[
        selectinload(Poll.options).selectinload(PollOption.votes).selectinload(PollVote.poll),
        selectinload(Poll.votes),
    ])
    if not poll:
        raise HTTPException(status_code=404)
    if not await _can_access(poll, member, user.is_admin, db):
        raise HTTPException(status_code=403)

    my_votes_r = await db.execute(
        select(PollVote)
        .where(PollVote.poll_id == poll_id, PollVote.member_id == member.id)
    )
    my_votes = my_votes_r.scalars().all()
    my_option_ids = {v.option_id for v in my_votes}
    my_ranks = {v.option_id: v.score for v in my_votes if v.score is not None}
    has_voted = bool(my_votes)
    edit_mode = has_voted and request.query_params.get("edit") == "1"

    results, total_votes = await _compute_results(poll, my_option_ids, db)

    # Ordre stable et identique pour tous les votants (indépendant des
    # résultats en cours) — sinon un votant plus tardif verrait les options
    # déjà réordonnées par le classement provisoire, ce qui biaise le vote.
    # Pour un sondage SCHEDULE, l'ordre chronologique des créneaux est bien
    # plus lisible que l'ordre de création.
    if poll.vote_type == "SCHEDULE":
        vote_options = sorted(poll.options, key=lambda o: o.slot_start or datetime.max)
    else:
        vote_options = sorted(poll.options, key=lambda o: o.order_position)

    schedule_conflicts = {}
    if poll.vote_type == "SCHEDULE":
        schedule_conflicts = await _check_schedule_conflicts(poll.options, db)

    author = await db.get(Member, poll.created_by_id) if poll.created_by_id else None

    is_creator = bool(member and poll.created_by_id == member.id)

    return templates.TemplateResponse(request, "pages/polls/detail.html", {
        "current_member": member,
        "current_user": user,
        "poll": poll,
        "results": results,
        "vote_options": vote_options,
        "total_votes": total_votes,
        "has_voted": has_voted,
        "my_option_ids": my_option_ids,
        "my_ranks": my_ranks,
        "edit_mode": edit_mode,
        "schedule_conflicts": schedule_conflicts,
        "is_open": _is_open(poll),
        "can_manage": _can_manage(member, user.is_admin),
        "author": author,
        "is_creator": is_creator,
        "vote_error": request.query_params.get("error"),
    })


@router.get("/{poll_id}/export.pdf")
async def poll_export_pdf(
    poll_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    import io
    import re as _re
    from fastapi.responses import StreamingResponse
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
    )

    user, member = ctx
    poll = await db.get(Poll, poll_id, options=[
        selectinload(Poll.options).selectinload(PollOption.votes),
        selectinload(Poll.votes),
    ])
    if not poll:
        raise HTTPException(status_code=404)
    if not await _can_access(poll, member, user.is_admin, db):
        raise HTTPException(status_code=403)

    my_votes_r = await db.execute(
        select(PollVote).where(PollVote.poll_id == poll_id, PollVote.member_id == member.id)
    )
    has_voted = bool(my_votes_r.scalars().first())
    is_creator = bool(poll.created_by_id == member.id)
    # Mêmes règles de visibilité que la page web : pas de résultats en avance
    # sur un sondage encore ouvert tant qu'on n'a pas voté soi-même.
    if _is_open(poll) and not has_voted and not is_creator and not user.is_admin:
        raise HTTPException(status_code=403, detail="Votez d'abord pour voir les résultats")

    results, total_votes = await _compute_results(poll, set(), db)
    author = await db.get(Member, poll.created_by_id) if poll.created_by_id else None

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm,
        title=poll.title, author="Portail Socrate",
    )

    TEAL_DARK  = colors.HexColor("#1a5252")
    TEAL       = colors.HexColor("#2c7a7b")
    GRAY       = colors.HexColor("#374151")
    GRAY_LIGHT = colors.HexColor("#9ca3af")
    AMBER_BG   = colors.HexColor("#fffbeb")
    AMBER_BR   = colors.HexColor("#fcd34d")
    ROW_BG     = colors.HexColor("#f9fafb")

    styles = getSampleStyleSheet()
    H1   = ParagraphStyle("H1", parent=styles["Normal"], fontSize=16, textColor=TEAL_DARK,
                           fontName="Helvetica-Bold", spaceAfter=4, leading=20)
    META = ParagraphStyle("META", parent=styles["Normal"], fontSize=9, textColor=GRAY_LIGHT,
                           fontName="Helvetica", spaceAfter=4)
    RANK = ParagraphStyle("RANK", parent=styles["Normal"], fontSize=11, textColor=GRAY_LIGHT,
                           fontName="Helvetica-Bold")
    LABEL = ParagraphStyle("LABEL", parent=styles["Normal"], fontSize=11, textColor=GRAY,
                            fontName="Helvetica-Bold", leading=14)
    VALUE = ParagraphStyle("VALUE", parent=styles["Normal"], fontSize=10, textColor=GRAY,
                            fontName="Helvetica", alignment=2)

    story = [Paragraph(poll.title, H1)]
    if poll.description:
        story.append(Paragraph(poll.description, META))
    meta_bits = [f"{total_votes} vote(s)"]
    if author:
        meta_bits.append(f"créé par {author.last_name or ''} {author.first_name or ''}".strip())
    meta_bits.append("clôturé" if not _is_open(poll) else "en cours")
    if poll.ends_at:
        meta_bits.append(f"clôture {poll.ends_at.strftime('%d/%m/%Y à %H:%M')}")
    meta_bits.append("classement" if poll.vote_type == "RANKING" else "choix")
    story.append(Paragraph(" · ".join(meta_bits) +
                            f" · exporté le {datetime.now().strftime('%d/%m/%Y à %H:%M')}", META))
    story.append(HRFlowable(width="100%", color=TEAL, thickness=1.2, spaceBefore=4, spaceAfter=10))

    for r in results:
        if poll.vote_type == "RANKING":
            rank_txt = f"#{r['rank']}"
            value_txt = (f"rang {r['avg']}" if r["avg"] is not None else "—") + f"  ({r['count']} classement(s))"
        else:
            rank_txt = ""
            value_txt = f"{r['count']}  ({r['pct']}%)"
        row = Table(
            [[Paragraph(rank_txt, RANK) if rank_txt else "",
              Paragraph(r["option"].label, LABEL),
              Paragraph(value_txt, VALUE)]],
            colWidths=[1.2*cm if poll.vote_type == "RANKING" else 0, doc.width * 0.6, doc.width * 0.4 - (1.2*cm if poll.vote_type == "RANKING" else 0)],
        )
        is_winner = poll.vote_type == "RANKING" and r.get("is_winner")
        row.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("BACKGROUND", (0, 0), (-1, -1), AMBER_BG if is_winner else ROW_BG),
            ("BOX", (0, 0), (-1, -1), 0.6, AMBER_BR if is_winner else colors.HexColor("#e5e7eb")),
        ]))
        story.append(row)
        story.append(Spacer(1, 4))

    doc.build(story)
    buf.seek(0)

    safe_title = _re.sub(r"[^A-Za-z0-9_-]+", "_", poll.title)[:60] or f"sondage_{poll.id}"
    filename = f"sondage_{poll.id}_{safe_title}.pdf"
    return StreamingResponse(
        buf, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.post("/{poll_id}/vote")
async def poll_vote(
    poll_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    poll = await db.get(Poll, poll_id, options=[selectinload(Poll.options)])
    if not poll:
        raise HTTPException(status_code=404)
    if not await _can_access(poll, member, user.is_admin, db):
        raise HTTPException(status_code=403)
    if not _is_open(poll):
        return RedirectResponse(url=f"/polls/{poll_id}?error=poll_closed", status_code=303)

    existing_r = await db.execute(
        select(PollVote).where(PollVote.poll_id == poll_id, PollVote.member_id == member.id)
    )
    existing_votes = existing_r.scalars().all()
    if existing_votes:
        # Modification autorisée tant que le sondage est ouvert : on retire
        # l'ancien vote avant d'enregistrer le nouveau.
        for v in existing_votes:
            await db.delete(v)

    form = await request.form()
    member_id = None if poll.is_anonymous else member.id

    if poll.vote_type == "RANKING":
        option_ids = [opt.id for opt in poll.options]
        n = len(option_ids)
        ranks: dict[int, int] = {}
        incomplete = False
        for opt_id in option_ids:
            raw = form.get(f"rank_{opt_id}", "")
            try:
                ranks[opt_id] = int(raw)
            except (TypeError, ValueError):
                incomplete = True

        # Doit être une permutation exacte de 1..n (chaque rang utilisé une
        # seule fois) — sinon la moyenne des rangs n'a pas de sens.
        if incomplete or sorted(ranks.values()) != list(range(1, n + 1)):
            return RedirectResponse(
                url=f"/polls/{poll_id}?error=ranking_invalid", status_code=303
            )

        for opt_id, rank in ranks.items():
            db.add(PollVote(poll_id=poll_id, option_id=opt_id, member_id=member_id, score=rank))
    else:
        option_ids_raw = form.getlist("option_id")
        if not option_ids_raw:
            return RedirectResponse(url=f"/polls/{poll_id}", status_code=303)

        valid_ids = {opt.id for opt in poll.options}
        chosen = []
        for oid_str in option_ids_raw:
            try:
                oid = int(oid_str)
                if oid in valid_ids:
                    chosen.append(oid)
            except ValueError:
                pass

        if not poll.is_multiple and len(chosen) > 1:
            chosen = chosen[:1]

        for oid in chosen:
            db.add(PollVote(poll_id=poll_id, option_id=oid, member_id=member_id))

    await db.commit()
    return RedirectResponse(url=f"/polls/{poll_id}", status_code=303)


@router.post("/{poll_id}/vote/delete")
async def poll_vote_delete(
    poll_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Retire son propre vote tant que le sondage est ouvert."""
    user, member = ctx
    poll = await db.get(Poll, poll_id)
    if not poll:
        raise HTTPException(status_code=404)
    if not await _can_access(poll, member, user.is_admin, db):
        raise HTTPException(status_code=403)
    if not _is_open(poll):
        return RedirectResponse(url=f"/polls/{poll_id}?error=poll_closed", status_code=303)

    await db.execute(
        delete(PollVote).where(PollVote.poll_id == poll_id, PollVote.member_id == member.id)
    )
    await db.commit()
    return RedirectResponse(url=f"/polls/{poll_id}", status_code=303)


@router.post("/{poll_id}/close")
async def poll_close(
    poll_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not _can_manage(member, user.is_admin):
        raise HTTPException(status_code=403)
    poll = await db.get(Poll, poll_id)
    if not poll:
        raise HTTPException(status_code=404)
    poll.ends_at = datetime.now()
    await db.commit()
    return RedirectResponse(url=f"/polls/{poll_id}", status_code=303)


@router.post("/{poll_id}/delete")
async def poll_delete(
    poll_id: int,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not _can_manage(member, user.is_admin):
        raise HTTPException(status_code=403)
    poll = await db.get(Poll, poll_id)
    if not poll:
        raise HTTPException(status_code=404)
    await db.execute(delete(PollVote).where(PollVote.poll_id == poll_id))
    await db.execute(delete(PollOption).where(PollOption.poll_id == poll_id))
    await db.delete(poll)
    await db.commit()
    return RedirectResponse(url="/polls/", status_code=303)
