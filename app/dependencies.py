"""Dépendances FastAPI — Auth, DB, Permissions"""
from datetime import datetime, timedelta
from typing import Optional, Annotated

import bcrypt as _bcrypt

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.identity import Member, User, MasonicGrade, LodgeFunction

settings = get_settings()

# ── Crypto ─────────────────────────────────────────────────────────────────

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=False)


def hash_password(password: str) -> str:
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


def hash_pin(pin: str) -> str:
    return hash_password(pin)


def verify_pin(plain_pin: str, hashed_pin: str) -> bool:
    return verify_password(plain_pin, hashed_pin)


# ── JWT ────────────────────────────────────────────────────────────────────

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    )
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


def create_refresh_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=settings.refresh_token_expire_days)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])


# ── Résolution de l'utilisateur courant ────────────────────────────────────

async def get_current_user(
    request: Request,
    token: Annotated[Optional[str], Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Optional[tuple[User, Member]]:
    """
    Résout l'utilisateur depuis :
    1. Bearer token (API / mobile)
    2. Cookie de session (navigateur web)
    Retourne (user, member) ou None si non authentifié.
    """
    # Récupération du token : header ou cookie
    raw_token = token
    if not raw_token:
        raw_token = request.cookies.get("access_token")

    if not raw_token:
        return None

    try:
        payload = decode_token(raw_token)
        user_id: int = int(payload.get("sub"))
    except (JWTError, TypeError, ValueError):
        return None

    result = await db.execute(
        select(User).where(User.id == user_id, User.is_active == True)
    )
    user = result.scalar_one_or_none()
    if not user:
        return None

    member_result = await db.execute(
        select(Member).where(Member.id == user.member_id)
    )
    member = member_result.scalar_one_or_none()
    return (user, member) if member else None


async def require_auth(
    ctx: Annotated[Optional[tuple], Depends(get_current_user)]
) -> tuple[User, Member]:
    """Exige une authentification — lève 401 sinon."""
    if ctx is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentification requise",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return ctx


async def require_active_member(
    ctx: Annotated[tuple, Depends(require_auth)]
) -> tuple[User, Member]:
    """Exige un membre actif."""
    user, member = ctx
    if not member.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Compte membre inactif"
        )
    return ctx


# ── Vérifications de grade / fonction ──────────────────────────────────────

def _grade_level(grade: MasonicGrade) -> int:
    return {
        MasonicGrade.APPRENTI: 1,
        MasonicGrade.COMPAGNON: 2,
        MasonicGrade.MAITRE: 3,
    }.get(grade, 0)


def require_grade(min_grade: MasonicGrade):
    """Dépendance factory : exige un grade minimum."""
    async def _check(ctx: Annotated[tuple, Depends(require_active_member)]):
        _, member = ctx
        if _grade_level(member.masonic_grade) < _grade_level(min_grade):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Grade {min_grade} requis"
            )
        return ctx
    return _check


def require_function(*functions: LodgeFunction):
    """Dépendance factory : exige une fonction spécifique (ou admin)."""
    async def _check(ctx: Annotated[tuple, Depends(require_active_member)]):
        user, member = ctx
        if user.is_admin:
            return ctx
        if member.lodge_function not in functions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Fonction insuffisante pour cette action"
            )
        return ctx
    return _check


async def require_admin(
    ctx: Annotated[tuple, Depends(require_active_member)]
) -> tuple[User, Member]:
    """Exige un administrateur technique."""
    user, member = ctx
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès administrateur requis"
        )
    return ctx


# ── Helpers de permission ───────────────────────────────────────────────────

def can_manage_meeting(member: Member) -> bool:
    """Peut créer/modifier une tenue."""
    return member.lodge_function in (
        LodgeFunction.VM,
        LodgeFunction.SECRETAIRE,
        LodgeFunction.PREMIER_S,
        LodgeFunction.SECOND_S,
    )


def can_lock_meeting(member: Member) -> bool:
    """Peut verrouiller une tenue."""
    return member.lodge_function == LodgeFunction.VM


def can_manage_finance(member: Member) -> bool:
    """Peut gérer les cotisations et la trésorerie (VM, Trésorier)."""
    return member.lodge_function in (LodgeFunction.VM, LodgeFunction.TRESORIER)


async def require_finance_manager(
    ctx: Annotated[tuple, Depends(require_active_member)]
) -> tuple:
    """Exige admin, VM ou Trésorier — pour les actions d'écriture finance."""
    user, member = ctx
    if user.is_admin or can_manage_finance(member):
        return ctx
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Réservé au Vénérable Maître, au Trésorier ou à l'administrateur",
    )


def can_manage_members(member: Member) -> bool:
    """Peut créer/modifier les membres."""
    return member.lodge_function in (
        LodgeFunction.VM,
        LodgeFunction.SECRETAIRE,
    )


def can_manage_attendance(member: Member) -> bool:
    """Peut émarger et consulter les présences (VM, Secrétaire, 1er et 2e Surveillant)."""
    return member.lodge_function in (
        LodgeFunction.VM,
        LodgeFunction.SECRETAIRE,
        LodgeFunction.PREMIER_S,
        LodgeFunction.SECOND_S,
    )
