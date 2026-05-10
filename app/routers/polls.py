"""Router — Sondages & Votes"""
from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, delete, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_auth
from app.models.content import Poll, PollOption, PollVote
from app.models.groups import LodgeGroup, GroupMembership, GroupType
from app.models.identity import Member, MasonicGrade, LodgeFunction

router = APIRouter(prefix="/polls", tags=["polls"])
templates = Jinja2Templates(directory="app/templates")

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
    return templates.TemplateResponse(request, "pages/polls/form.html", {
        "current_member": member,
        "current_user": user,
        "poll": None,
        "groups": await _load_groups(db),
        "target_value": "",
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

    min_grade, target_group_id = _parse_target(target)

    poll = Poll(
        title=title,
        description=description.strip() or None,
        is_multiple=bool(is_multiple),
        is_anonymous=bool(is_anonymous),
        is_public_vote=bool(is_public_vote),
        min_grade=min_grade,
        target_group_id=target_group_id,
        ends_at=ea,
        created_by_id=member.id,
    )
    db.add(poll)
    await db.flush()

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
            push_url=f"/polls/{poll.id}",
            push_body=f"Cliquez pour voter — {poll.title}",
        )
        await db.commit()

    return RedirectResponse(url=f"/polls/{poll.id}", status_code=303)


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
    has_voted = bool(my_votes)

    total_votes = len(poll.votes)
    results = []
    for opt in sorted(poll.options, key=lambda o: o.order_position):
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

    author = await db.get(Member, poll.created_by_id) if poll.created_by_id else None

    return templates.TemplateResponse(request, "pages/polls/detail.html", {
        "current_member": member,
        "current_user": user,
        "poll": poll,
        "results": results,
        "total_votes": total_votes,
        "has_voted": has_voted,
        "my_option_ids": my_option_ids,
        "is_open": _is_open(poll),
        "can_manage": _can_manage(member, user.is_admin),
        "author": author,
    })


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
        raise HTTPException(status_code=400, detail="Sondage clôturé")

    existing_r = await db.execute(
        select(PollVote).where(PollVote.poll_id == poll_id, PollVote.member_id == member.id)
    )
    if existing_r.scalars().first():
        return RedirectResponse(url=f"/polls/{poll_id}", status_code=303)

    form = await request.form()
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

    member_id = None if poll.is_anonymous else member.id
    for oid in chosen:
        db.add(PollVote(poll_id=poll_id, option_id=oid, member_id=member_id))

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
