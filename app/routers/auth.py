"""Router Auth — Login, logout, refresh, reset password"""
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import (
    verify_password, create_access_token, create_refresh_token,
    decode_token, get_current_user, require_auth
)
from app.models.identity import User, Member

router = APIRouter(prefix="/auth", tags=["auth"])
templates = Jinja2Templates(directory="app/templates")


# ── Login (form web) ───────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "pages/auth/login.html")


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

    if not user or not verify_password(form_data.password, user.password_hash):
        return templates.TemplateResponse(
            request, "pages/auth/login.html",
            {"error": "Identifiant ou mot de passe incorrect"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    if not user.is_active:
        return templates.TemplateResponse(
            request, "pages/auth/login.html",
            {"error": "Compte désactivé"},
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


# ── Logout ─────────────────────────────────────────────────────────────────

@router.get("/logout")
@router.post("/logout")
async def logout():
    response = RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")
    return response
