"""Router — Gestion des groupes de membres"""
import json
from typing import Annotated, Optional, List

from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import require_auth, require_finance_manager, can_manage_finance
from app.models.groups import LodgeGroup as Group, GroupMembership, GroupType, SYSTEM_GROUPS
from app.models.identity import Member, MemberStatus, LodgeFunction, MasonicGrade

router = APIRouter(prefix="/groups", tags=["groups"])
templates = Jinja2Templates(directory="app/templates")

GRADE_ORDER = {
    MasonicGrade.APPRENTI: 1,
    MasonicGrade.COMPAGNON: 2,
    MasonicGrade.MAITRE: 3,
}

# Officiers = tout sauf FRERE
OFFICER_FUNCTIONS = {
    LodgeFunction.VM, LodgeFunction.PREMIER_S, LodgeFunction.SECOND_S,
    LodgeFunction.ORATEUR, LodgeFunction.SECRETAIRE, LodgeFunction.TRESORIER,
    LodgeFunction.EXPERT, LodgeFunction.MAITRE_CEREMONIES, LodgeFunction.HARMONISTE,
    LodgeFunction.HOSPITALIER, LodgeFunction.TUILEUR, LodgeFunction.ARCHITECTE,
    LodgeFunction.MAITRE_BANQUETS,
}


def _can_manage_groups(user, member: Member) -> bool:
    return user.is_admin or member.lodge_function in (
        LodgeFunction.VM, LodgeFunction.SECRETAIRE
    )


async def _get_active_members(db: AsyncSession) -> list[Member]:
    r = await db.execute(
        select(Member)
        .where(Member.status == MemberStatus.ACTIVE)
        .order_by(Member.last_name, Member.first_name)
    )
    return r.scalars().all()


async def resolve_group_members(db: AsyncSession, group: Group) -> list[Member]:
    """
    Calcule dynamiquement les membres d'un groupe.
    - GRADE / COUNCIL / PAIR : calculé depuis les attributs Member
    - COMMISSION / CUSTOM : depuis GroupMembership en DB
    """
    all_active = await _get_active_members(db)

    if group.group_type == GroupType.GRADE:
        if group.grade_filter is None:
            return all_active  # "Tous les membres"
        return [m for m in all_active if m.masonic_grade and m.masonic_grade.value == group.grade_filter]

    elif group.group_type == GroupType.COUNCIL:
        return [m for m in all_active if m.lodge_function in OFFICER_FUNCTIONS]

    elif group.group_type == GroupType.PAIR:
        functions = set(json.loads(group.function_filter or "[]"))
        return [m for m in all_active if m.lodge_function and m.lodge_function.value in functions]

    else:
        # COMMISSION ou CUSTOM → membership explicite
        r = await db.execute(
            select(GroupMembership.member_id).where(GroupMembership.group_id == group.id)
        )
        member_ids = {row[0] for row in r.all()}
        return [m for m in all_active if m.id in member_ids]


async def resolve_group_member_ids(db: AsyncSession, group: Group) -> list[int]:
    members = await resolve_group_members(db, group)
    return [m.id for m in members]


async def ensure_system_groups(db: AsyncSession) -> None:
    """Crée les groupes système s'ils n'existent pas encore."""
    for slug, cfg in SYSTEM_GROUPS.items():
        r = await db.execute(select(Group).where(Group.slug == slug))
        if not r.scalar_one_or_none():
            g = Group(  # LodgeGroup aliasé en Group
                slug=slug,
                name=cfg["name"],
                group_type=cfg["type"],
                is_system=True,
                grade_filter=cfg.get("grade_filter"),
                function_filter=json.dumps(cfg.get("functions", [])) if cfg.get("functions") else None,
            )
            db.add(g)
    await db.flush()


# ══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/", response_class=HTMLResponse)
async def list_groups(
    request: Request,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    await ensure_system_groups(db)
    await db.commit()

    r = await db.execute(
        select(Group)
        .options(selectinload(Group.memberships))
        .order_by(Group.is_system.desc(), Group.name)
    )
    groups = r.scalars().all()

    # Calculer le nb de membres pour chaque groupe
    all_active = await _get_active_members(db)
    group_counts: dict[int, int] = {}
    for g in groups:
        members = await resolve_group_members(db, g)
        group_counts[g.id] = len(members)

    return templates.TemplateResponse(request, "pages/groups/list.html", {
        "current_member": member,
        "current_user": user,
        "groups": groups,
        "group_counts": group_counts,
        "can_manage": _can_manage_groups(user, member),
        "GroupType": GroupType,
    })


@router.get("/create", response_class=HTMLResponse)
async def create_group_form(
    request: Request,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not _can_manage_groups(user, member):
        raise HTTPException(403)

    return templates.TemplateResponse(request, "pages/groups/create.html", {
        "current_member": member,
        "current_user": user,
        "GroupType": GroupType,
    })


@router.post("/create")
async def create_group(
    request: Request,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    name: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    color: Annotated[str, Form()] = "#3b5bdb",
    group_type: Annotated[str, Form()] = "CUSTOM",
):
    user, member = ctx
    if not _can_manage_groups(user, member):
        raise HTTPException(403)

    g = Group(  # LodgeGroup aliasé en Group
        name=name.strip(),
        description=description.strip() or None,
        color=color,
        group_type=GroupType(group_type),
        is_system=False,
        created_by_id=member.id,
    )
    db.add(g)
    await db.commit()
    return RedirectResponse(url=f"/groups/{g.id}", status_code=303)


@router.get("/{group_id}", response_class=HTMLResponse)
async def group_detail(
    group_id: int,
    request: Request,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    group = await db.get(Group, group_id, options=[selectinload(Group.memberships)])
    if not group:
        raise HTTPException(404)

    members = await resolve_group_members(db, group)
    all_active = await _get_active_members(db)
    member_ids_in_group = {m.id for m in members}
    members_not_in = [m for m in all_active if m.id not in member_ids_in_group]

    return templates.TemplateResponse(request, "pages/groups/detail.html", {
        "current_member": member,
        "current_user": user,
        "group": group,
        "members": members,
        "members_not_in": members_not_in,
        "can_manage": _can_manage_groups(user, member),
        "is_dynamic": group.group_type in (GroupType.GRADE, GroupType.COUNCIL, GroupType.PAIR),
        "GroupType": GroupType,
    })


@router.post("/{group_id}/add-member")
async def add_member(
    group_id: int,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    member_id: Annotated[int, Form()],
):
    user, member = ctx
    if not _can_manage_groups(user, member):
        raise HTTPException(403)

    group = await db.get(Group, group_id)
    if not group or group.is_system or group.group_type in (GroupType.GRADE, GroupType.COUNCIL, GroupType.PAIR):
        raise HTTPException(400, "Ce groupe est géré automatiquement")

    # Vérifier si déjà membre
    r = await db.execute(
        select(GroupMembership).where(
            GroupMembership.group_id == group_id,
            GroupMembership.member_id == member_id,
        )
    )
    if not r.scalar_one_or_none():
        db.add(GroupMembership(
            group_id=group_id,
            member_id=member_id,
            added_by_id=member.id,
        ))
        await db.commit()

    return RedirectResponse(url=f"/groups/{group_id}", status_code=303)


@router.post("/{group_id}/remove-member")
async def remove_member(
    group_id: int,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    member_id: Annotated[int, Form()],
):
    user, member = ctx
    if not _can_manage_groups(user, member):
        raise HTTPException(403)

    r = await db.execute(
        select(GroupMembership).where(
            GroupMembership.group_id == group_id,
            GroupMembership.member_id == member_id,
        )
    )
    gm = r.scalar_one_or_none()
    if gm:
        await db.delete(gm)
        await db.commit()

    return RedirectResponse(url=f"/groups/{group_id}", status_code=303)


@router.post("/{group_id}/delete")
async def delete_group(
    group_id: int,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    if not _can_manage_groups(user, member):
        raise HTTPException(403)

    group = await db.get(Group, group_id)
    if not group:
        raise HTTPException(404)
    if group.is_system:
        raise HTTPException(400, "Les groupes système ne peuvent pas être supprimés")

    await db.delete(group)
    await db.commit()
    return RedirectResponse(url="/groups/", status_code=303)


@router.post("/{group_id}/edit")
async def edit_group(
    group_id: int,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    name: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    color: Annotated[str, Form()] = "#3b5bdb",
):
    user, member = ctx
    if not _can_manage_groups(user, member):
        raise HTTPException(403)

    group = await db.get(Group, group_id)
    if not group or group.is_system:
        raise HTTPException(400)

    group.name = name.strip()
    group.description = description.strip() or None
    group.color = color
    await db.commit()
    return RedirectResponse(url=f"/groups/{group_id}", status_code=303)
