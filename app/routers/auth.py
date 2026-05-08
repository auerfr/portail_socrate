"""Router Auth — Login, logout, refresh, reset password"""
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import (
    verify_password, hash_password, create_access_token, create_refresh_token,
    decode_token, get_current_user, require_auth
)
from app.models.identity import User, Member
from app.models.lodge import LodgeSettings


async def _get_lodge(db: AsyncSession):
    r = await db.execute(select(LodgeSettings).limit(1))
    return r.scalar_one_or_none()

router = APIRouter(prefix="/auth", tags=["auth"])
templates = Jinja2Templates(directory="app/templates")


# ── Login (form web) ───────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    lodge = await _get_lodge(db)
    return templates.TemplateResponse(request, "pages/auth/login.html", {"lodge": lodge})


@router.post("/login")
async def login_submit(
    request: Request,
    response: Response,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Authentification web — répond avec cookie + redirect."""
    result = await db.execute(
        select(User).where(User.login == form_data.username)
    )
    user = result.scalar_one_or_none()

    lodge = await _get_lodge(db)

    if not user or not verify_password(form_data.password, user.password_hash):
        return templates.TemplateResponse(
            request, "pages/auth/login.html",
            {"error": "Identifiant ou mot de passe incorrect", "lodge": lodge},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    if not user.is_active:
        return templates.TemplateResponse(
            request, "pages/auth/login.html",
            {"error": "Compte désactivé", "lodge": lodge},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    # Mettre à jour last_login
    user.last_login_at = datetime.utcnow()
    await db.commit()

    access_token = create_access_token({"sub": str(user.id)})
    refresh_token = create_refresh_token({"sub": str(user.id)})

    redirect = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    redirect.set_cookie(
        "access_token", access_token,
        httponly=True, samesite="lax", secure=False,  # secure=True en prod
        max_age=60 * 60 * 8,
    )
    redirect.set_cookie(
        "refresh_token", refresh_token,
        httponly=True, samesite="lax", secure=False,
        max_age=60 * 60 * 24 * 30,
    )
    return redirect


# ── Token API (OAuth2 — mobile / API) ─────────────────────────────────────

@router.post("/token")
async def token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Authentification API — retourne un JWT."""
    result = await db.execute(
        select(User).where(User.login == form_data.username)
    )
    user = result.scalar_one_or_none()

    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identifiant ou mot de passe incorrect",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token  = create_access_token({"sub": str(user.id)})
    refresh_token = create_refresh_token({"sub": str(user.id)})

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


# ── Refresh ────────────────────────────────────────────────────────────────

@router.post("/refresh")
async def refresh(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    raw = request.cookies.get("refresh_token") or (
        (await request.json()).get("refresh_token") if request.headers.get("content-type") == "application/json" else None
    )
    if not raw:
        raise HTTPException(status_code=401, detail="Refresh token manquant")

    try:
        payload = decode_token(raw)
        if payload.get("type") != "refresh":
            raise ValueError
        user_id = int(payload["sub"])
    except Exception:
        raise HTTPException(status_code=401, detail="Refresh token invalide")

    result = await db.execute(select(User).where(User.id == user_id, User.is_active == True))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Utilisateur introuvable")

    new_access = create_access_token({"sub": str(user.id)})
    return {"access_token": new_access, "token_type": "bearer"}


# ── Changement de mot de passe (utilisateur connecté) ─────────────────────

@router.get("/password", response_class=HTMLResponse)
async def change_password_page(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    lodge = await _get_lodge(db)
    return templates.TemplateResponse(request, "pages/auth/change_password.html", {
        "lodge": lodge,
        "current_user": user,
        "current_member": member,
    })


@router.post("/password", response_class=HTMLResponse)
async def change_password_submit(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    user, member = ctx
    lodge = await _get_lodge(db)

    def error(msg: str):
        return templates.TemplateResponse(request, "pages/auth/change_password.html", {
            "lodge": lodge,
            "current_user": user,
            "current_member": member,
            "error": msg,
        }, status_code=400)

    if not verify_password(current_password, user.password_hash):
        return error("Mot de passe actuel incorrect.")

    if len(new_password) < 8:
        return error("Le nouveau mot de passe doit contenir au moins 8 caractères.")

    if new_password != confirm_password:
        return error("Les deux nouveaux mots de passe ne correspondent pas.")

    user.password_hash = hash_password(new_password)
    await db.commit()

    return templates.TemplateResponse(request, "pages/auth/change_password.html", {
        "lodge": lodge,
        "current_user": user,
        "current_member": member,
        "success": True,
    })


# ── Logout ─────────────────────────────────────────────────────────────────

@router.get("/logout")
@router.post("/logout")
async def logout():
    response = RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")
    return response
