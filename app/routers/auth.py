"""Router Auth — Login, logout, refresh, reset password"""
import secrets
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import (
    verify_password, hash_password, create_access_token, create_refresh_token,
    decode_token, get_current_user, require_auth
)
from app.models.identity import User, Member
from app.models.lodge import LodgeSettings
from app.services.email import _send_raw


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
    identifier = form_data.username.strip().lower()
    result = await db.execute(
        select(User)
        .join(Member, User.member_id == Member.id)
        .where(or_(User.login == identifier, Member.email == identifier))
    )
    user = result.scalar_one_or_none()

    lodge = await _get_lodge(db)

    if not user or not verify_password(form_data.password, user.password_hash):
        try:
            from app.services.audit import log_audit
            await log_audit(
                db, actor_id=(user.member_id if user else None),
                action="LOGIN_FAILED",
                target_label=identifier,
                details="mauvais identifiant/mot de passe",
                request=request, commit=True,
            )
        except Exception:
            pass
        return templates.TemplateResponse(
            request, "pages/auth/login.html",
            {"error": "Identifiant ou mot de passe incorrect", "lodge": lodge},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    if not user.is_active:
        try:
            from app.services.audit import log_audit
            await log_audit(
                db, actor_id=user.member_id,
                action="LOGIN_BLOCKED",
                target_label=identifier, details="compte désactivé",
                request=request, commit=True,
            )
        except Exception:
            pass
        return templates.TemplateResponse(
            request, "pages/auth/login.html",
            {"error": "Compte désactivé", "lodge": lodge},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    # Mettre à jour last_login + audit
    user.last_login_at = datetime.utcnow()
    try:
        from app.services.audit import log_audit
        await log_audit(
            db, actor_id=user.member_id,
            action="LOGIN", target_label=user.login,
            request=request,
        )
    except Exception:
        pass
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
    identifier = form_data.username.strip().lower()
    result = await db.execute(
        select(User)
        .join(Member, User.member_id == Member.id)
        .where(or_(User.login == identifier, Member.email == identifier))
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


# ── Impersonation (admin seulement) ───────────────────────────────────────

@router.post("/impersonate/{member_id}")
async def impersonate(
    member_id: int,
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, _ = ctx
    if not user.is_admin:
        raise HTTPException(status_code=403)

    # Trouver le compte de la cible
    result = await db.execute(select(User).where(User.member_id == member_id, User.is_active == True))
    target_user = result.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="Ce membre n'a pas de compte actif")

    # Sauvegarder le token admin actuel
    origin_token = request.cookies.get("access_token", "")

    # Créer un token pour la cible
    target_token = create_access_token({"sub": str(target_user.id)})

    redirect = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    redirect.set_cookie("access_token", target_token, httponly=True, samesite="lax", secure=False, max_age=60 * 60 * 8)
    redirect.set_cookie("impersonate_origin_token", origin_token, httponly=True, samesite="lax", secure=False, max_age=60 * 60 * 8)
    return redirect


@router.post("/stop-impersonate")
async def stop_impersonate(request: Request):
    origin_token = request.cookies.get("impersonate_origin_token", "")
    if not origin_token:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    redirect = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    redirect.set_cookie("access_token", origin_token, httponly=True, samesite="lax", secure=False, max_age=60 * 60 * 8)
    redirect.delete_cookie("impersonate_origin_token")
    return redirect


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
        "login_success": False,
        "login_error": None,
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


# ── Changement d'identifiant (utilisateur connecté) ───────────────────────

@router.post("/username", response_class=HTMLResponse)
async def change_username(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    new_login: str = Form(...),
):
    user, member = ctx
    lodge = await _get_lodge(db)
    new_login = new_login.strip().lower()

    def _render(login_error=None, login_success=False):
        return templates.TemplateResponse(request, "pages/auth/change_password.html", {
            "lodge": lodge, "current_user": user, "current_member": member,
            "login_error": login_error, "login_success": login_success,
        })

    if len(new_login) < 3 or " " in new_login:
        return _render(login_error="L'identifiant doit contenir au moins 3 caractères sans espace.")

    existing = await db.execute(select(User).where(User.login == new_login, User.id != user.id))
    if existing.scalar_one_or_none():
        return _render(login_error="Cet identifiant est déjà utilisé par un autre compte.")

    user.login = new_login
    await db.commit()
    return _render(login_success=True)


# ── Réinitialisation de mot de passe (public) ─────────────────────────────

@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request, db: Annotated[AsyncSession, Depends(get_db)]):
    lodge = await _get_lodge(db)
    return templates.TemplateResponse(request, "pages/auth/reset_password.html", {"lodge": lodge})


@router.post("/reset-password", response_class=HTMLResponse)
async def reset_password_request(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    email: str = Form(...),
):
    lodge = await _get_lodge(db)
    result = await db.execute(
        select(User).join(Member, User.member_id == Member.id).where(Member.email == email.strip().lower())
    )
    user = result.scalar_one_or_none()

    if user and user.is_active:
        member = await db.get(Member, user.member_id)
        token = secrets.token_urlsafe(32)
        user.reset_token = token
        user.reset_token_expires = datetime.utcnow() + timedelta(hours=2)
        await db.commit()

        base_url = str(request.base_url).rstrip("/")
        reset_url = f"{base_url}/auth/reset-password/{token}"
        prenom = member.first_name if member else ""
        html = f"""<p>Bonjour {prenom},</p>
<p>Vous avez demandé la réinitialisation de votre mot de passe sur le portail de la loge.</p>
<p><a href="{reset_url}" style="background:#2c7a7b;color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none;display:inline-block;">
  Réinitialiser mon mot de passe →
</a></p>
<p style="color:#6b7280;font-size:13px;">Ce lien est valable 2 heures. Si vous n'avez pas fait cette demande, ignorez cet email.</p>"""
        text = f"Bonjour {prenom},\n\nRéinitialisez votre mot de passe ici (valable 2h) :\n{reset_url}\n\nSi vous n'avez pas fait cette demande, ignorez cet email."
        await _send_raw(member.email, "[Portail Loge] Réinitialisation de mot de passe", html, text)

    return templates.TemplateResponse(request, "pages/auth/reset_password.html", {
        "lodge": lodge,
        "sent": True,
    })


@router.get("/reset-password/{token}", response_class=HTMLResponse)
async def reset_password_form(
    token: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    lodge = await _get_lodge(db)
    result = await db.execute(select(User).where(User.reset_token == token))
    user = result.scalar_one_or_none()
    if not user or not user.reset_token_expires or user.reset_token_expires < datetime.utcnow():
        return templates.TemplateResponse(request, "pages/auth/reset_password.html", {
            "lodge": lodge, "token_invalid": True,
        })
    return templates.TemplateResponse(request, "pages/auth/reset_password_form.html", {
        "lodge": lodge, "token": token,
    })


@router.post("/reset-password/{token}", response_class=HTMLResponse)
async def reset_password_confirm(
    token: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    lodge = await _get_lodge(db)
    result = await db.execute(select(User).where(User.reset_token == token))
    user = result.scalar_one_or_none()
    if not user or not user.reset_token_expires or user.reset_token_expires < datetime.utcnow():
        return templates.TemplateResponse(request, "pages/auth/reset_password.html", {
            "lodge": lodge, "token_invalid": True,
        })
    if len(new_password) < 8:
        return templates.TemplateResponse(request, "pages/auth/reset_password_form.html", {
            "lodge": lodge, "token": token, "error": "Le mot de passe doit contenir au moins 8 caractères.",
        })
    if new_password != confirm_password:
        return templates.TemplateResponse(request, "pages/auth/reset_password_form.html", {
            "lodge": lodge, "token": token, "error": "Les deux mots de passe ne correspondent pas.",
        })
    user.password_hash = hash_password(new_password)
    user.reset_token = None
    user.reset_token_expires = None
    await db.commit()
    return RedirectResponse(url="/auth/login?reset=1", status_code=status.HTTP_302_FOUND)


# ── Logout ─────────────────────────────────────────────────────────────────

@router.get("/logout")
@router.post("/logout")
async def logout():
    response = RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")
    return response
