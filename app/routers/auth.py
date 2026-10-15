"""Router Auth — Login, logout, refresh, reset password"""
import secrets
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import (
    verify_password, hash_password, create_access_token, create_refresh_token,
    decode_token, get_current_user, require_auth, require_guest_auth
)
from app.models.identity import User, Member
from app.models.lodge import LodgeSettings
from app.services.email import _send_raw


async def _get_lodge(db: AsyncSession):
    r = await db.execute(select(LodgeSettings).limit(1))
    return r.scalar_one_or_none()

router = APIRouter(prefix="/auth", tags=["auth"])
from app.template_engine import templates


async def _try_guest_login(request: Request, db: AsyncSession, identifier: str, password: str, next: str):
    """Essaie d'authentifier `identifier`/`password` comme compte visiteur
    (GuestAccount, identifiant = email). Retourne la redirection de login à
    renvoyer si ça marche, None sinon (pour laisser /auth/login retomber sur
    l'échec "identifiant/mot de passe incorrect" générique — on ne révèle
    jamais si un email correspond à un compte membre ou visiteur)."""
    from app.models.guest import GuestAccount, GuestSession

    guest = (
        await db.execute(select(GuestAccount).where(GuestAccount.email == identifier))
    ).scalar_one_or_none()
    if not guest or not guest.is_active or not guest.password_hash:
        return None
    if guest.expires_at and guest.expires_at < datetime.utcnow():
        return None
    if not verify_password(password, guest.password_hash):
        return None

    guest.last_login_at = datetime.utcnow()
    try:
        from app.services.audit import log_audit
        await log_audit(
            db, actor_id=None, action="GUEST_LOGIN",
            target_label=f"{guest.full_name} ({guest.email})",
            request=request,
        )
    except Exception:
        pass
    await db.commit()

    token_data = {"sub": str(guest.id), "principal": "guest"}
    if guest.totp_enabled:
        token_data["2fa_pending"] = True
    access_token = create_access_token(token_data)

    try:
        payload = decode_token(access_token)
        jti = payload.get("jti")
        exp = payload.get("exp")
        if jti:
            expires = datetime.utcfromtimestamp(exp) if exp else None
            db.add(GuestSession(
                guest_account_id=guest.id, jti=jti,
                ip_address=request.client.host if request.client else None,
                user_agent=(request.headers.get("user-agent", "")[:300]),
                expires_at=expires,
            ))
            await db.commit()
    except Exception:
        pass

    # "/" (page d'accueil membre, défaut du formulaire de login) n'est jamais
    # une destination valide pour un visiteur — sans ce garde-fou, un visiteur
    # qui se connecte directement sur /auth/login (plutôt que via un lien
    # profond) atterrit sur "/", inaccessible pour lui, et rebondit vers le
    # login sans message d'erreur visible.
    safe_next = next if (next and next not in ("/", "") and next.startswith("/") and not next.startswith("//")) else "/visiteur/"
    if guest.totp_enabled:
        from urllib.parse import quote
        redirect = RedirectResponse(url=f"/auth/2fa/verify?next={quote(safe_next, safe='')}", status_code=status.HTTP_302_FOUND)
    else:
        redirect = RedirectResponse(url=safe_next, status_code=status.HTTP_302_FOUND)
    redirect.set_cookie(
        "access_token", access_token,
        httponly=True, samesite="lax", secure=False,
        max_age=60 * 60 * 8,
    )
    return redirect


# ── Login (form web) ───────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    next: str = "/",
):
    lodge = await _get_lodge(db)
    return templates.TemplateResponse(request, "pages/auth/login.html", {
        "lodge": lodge,
        "next": next,
    })


@router.post("/login")
async def login_submit(
    request: Request,
    response: Response,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[AsyncSession, Depends(get_db)],
    next: str = Form("/"),
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
        # Connexion unifiée : pas de compte membre trouvé/mot de passe
        # invalide -> on essaie un compte visiteur (GuestAccount) avant de
        # déclarer l'échec, pour que /auth/login reste le seul point
        # d'entrée, membre comme visiteur.
        guest_redirect = await _try_guest_login(request, db, identifier, form_data.password, next)
        if guest_redirect is not None:
            return guest_redirect
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

    token_data = {"sub": str(user.id)}
    if user.totp_enabled:
        token_data["2fa_pending"] = True
    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token({"sub": str(user.id)})

    # Enregistrement session pour la révocation
    try:
        from app.models.system import UserSession
        from app.dependencies import decode_token
        from datetime import datetime as _dt
        payload = decode_token(access_token)
        jti = payload.get("jti")
        exp = payload.get("exp")
        if jti:
            expires = _dt.utcfromtimestamp(exp) if exp else None
            db.add(UserSession(
                user_id=user.id, jti=jti,
                ip_address=request.client.host if request.client else None,
                user_agent=(request.headers.get("user-agent", "")[:300]),
                expires_at=expires,
            ))
            await db.commit()
    except Exception:
        pass

    # Rediriger vers la page d'origine si fournie (et sécurisée)
    safe_next = next if (next and next.startswith("/") and not next.startswith("//")) else "/"
    if user.totp_enabled:
        from urllib.parse import quote
        redirect = RedirectResponse(url=f"/auth/2fa/verify?next={quote(safe_next, safe='')}", status_code=status.HTTP_302_FOUND)
    else:
        redirect = RedirectResponse(url=safe_next, status_code=status.HTTP_302_FOUND)
    redirect.set_cookie(
        "access_token", access_token,
        httponly=True, samesite="lax", secure=False,  # secure=False en prod
        max_age=60 * 60 * 8,
    )
    redirect.set_cookie(
        "refresh_token", refresh_token,
        httponly=True, samesite="lax", secure=False,
        max_age=60 * 60 * 24 * 30,
    )
    return redirect


# ── Accès visiteur : lien magique + onboarding 2FA obligatoire ────────────

@router.get("/magic/{token}", response_class=HTMLResponse)
async def guest_magic_login(
    token: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Première connexion visiteur via lien magique à usage unique — pose
    un cookie de session visiteur avec onboarding_pending tant que le
    mot de passe + la 2FA ne sont pas configurés (voir
    two_factor_gate_middleware dans app/main.py, qui force la redirection
    vers /auth/visiteur/onboarding et empêche tout contournement)."""
    from app.models.guest import GuestAccount, GuestSession

    lodge = await _get_lodge(db)
    guest = (
        await db.execute(select(GuestAccount).where(GuestAccount.magic_token == token))
    ).scalar_one_or_none()

    invalid = (
        not guest or not guest.is_active
        or not guest.magic_token_expires_at
        or guest.magic_token_expires_at < datetime.utcnow()
    )
    if invalid:
        return templates.TemplateResponse(request, "pages/auth/login.html", {
            "lodge": lodge,
            "error": "Ce lien d'invitation est invalide ou a expiré. Contactez l'officier qui vous a invité.",
        }, status_code=status.HTTP_400_BAD_REQUEST)

    guest.last_login_at = datetime.utcnow()
    try:
        from app.services.audit import log_audit
        await log_audit(
            db, actor_id=None, action="GUEST_MAGIC_LOGIN",
            target_label=f"{guest.full_name} ({guest.email})",
            request=request,
        )
    except Exception:
        pass
    await db.commit()

    token_data = {"sub": str(guest.id), "principal": "guest"}
    if not guest.onboarding_completed:
        token_data["onboarding_pending"] = True
    access_token = create_access_token(token_data)

    try:
        payload = decode_token(access_token)
        jti = payload.get("jti")
        exp = payload.get("exp")
        if jti:
            expires = datetime.utcfromtimestamp(exp) if exp else None
            db.add(GuestSession(
                guest_account_id=guest.id, jti=jti,
                ip_address=request.client.host if request.client else None,
                user_agent=(request.headers.get("user-agent", "")[:300]),
                expires_at=expires,
            ))
            await db.commit()
    except Exception:
        pass

    target = "/auth/visiteur/onboarding" if not guest.onboarding_completed else "/visiteur/"
    redirect = RedirectResponse(url=target, status_code=status.HTTP_302_FOUND)
    redirect.set_cookie(
        "access_token", access_token,
        httponly=True, samesite="lax", secure=False,
        max_age=60 * 60 * 8,
    )
    return redirect


@router.get("/visiteur/onboarding", response_class=HTMLResponse)
async def guest_onboarding_page(
    request: Request,
    guest: Annotated[object, Depends(require_guest_auth)],
):
    """Onboarding visiteur obligatoire (mot de passe puis 2FA), non
    contournable — voir two_factor_gate_middleware."""
    if guest.onboarding_completed:
        return RedirectResponse(url="/visiteur/", status_code=303)

    step = "password" if not guest.password_hash else "totp"
    ctx = {"guest": guest, "step": step}
    if step == "totp":
        import pyotp, qrcode, io, base64
        temp_secret = pyotp.random_base32()
        totp = pyotp.TOTP(temp_secret)
        provisioning_url = totp.provisioning_uri(
            name=guest.email, issuer_name="Portail Socrate — Visiteur",
        )
        qr = qrcode.make(provisioning_url)
        buf = io.BytesIO()
        qr.save(buf, format="PNG")
        ctx["temp_secret"] = temp_secret
        ctx["qr_b64"] = base64.b64encode(buf.getvalue()).decode()
    return templates.TemplateResponse(request, "pages/auth/visiteur_onboarding.html", ctx)


@router.post("/visiteur/onboarding/password")
async def guest_onboarding_set_password(
    request: Request,
    guest: Annotated[object, Depends(require_guest_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    if guest.onboarding_completed:
        return RedirectResponse(url="/visiteur/", status_code=303)
    if len(new_password) < 8:
        return templates.TemplateResponse(request, "pages/auth/visiteur_onboarding.html", {
            "guest": guest, "step": "password",
            "error": "Le mot de passe doit contenir au moins 8 caractères.",
        }, status_code=400)
    if new_password != confirm_password:
        return templates.TemplateResponse(request, "pages/auth/visiteur_onboarding.html", {
            "guest": guest, "step": "password",
            "error": "Les deux mots de passe ne correspondent pas.",
        }, status_code=400)
    guest.password_hash = hash_password(new_password)
    await db.commit()
    return RedirectResponse(url="/auth/visiteur/onboarding", status_code=303)


@router.post("/visiteur/onboarding/totp")
async def guest_onboarding_enable_totp(
    request: Request,
    guest: Annotated[object, Depends(require_guest_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    temp_secret: str = Form(...),
    code: str = Form(...),
):
    """Dernière étape de l'onboarding — active la 2FA (obligatoire) et lève
    le claim onboarding_pending du token en le re-signant, comme
    /auth/2fa/verify le fait pour 2fa_pending."""
    import pyotp
    if guest.onboarding_completed:
        return RedirectResponse(url="/visiteur/", status_code=303)
    if not guest.password_hash:
        return RedirectResponse(url="/auth/visiteur/onboarding", status_code=303)

    totp = pyotp.TOTP(temp_secret)
    if not totp.verify(code.strip(), valid_window=1):
        raise HTTPException(400, "Code invalide — vérifiez l'heure de votre appareil")

    guest.totp_secret = temp_secret
    guest.totp_enabled = True
    guest.onboarding_completed = True
    guest.magic_token = None
    guest.magic_token_expires_at = None
    await db.commit()

    redirect = RedirectResponse(url="/visiteur/", status_code=303)
    raw_token = request.cookies.get("access_token")
    try:
        payload = decode_token(raw_token)
        from jose import jwt as _jwt
        from app.config import get_settings as _get_settings
        _settings = _get_settings()
        cleared_payload = {k: v for k, v in payload.items() if k != "onboarding_pending"}
        new_access_token = _jwt.encode(cleared_payload, _settings.secret_key, algorithm=_settings.algorithm)
        redirect.set_cookie(
            "access_token", new_access_token,
            httponly=True, samesite="lax", secure=False,
            max_age=60 * 60 * 8,
        )
    except Exception:
        pass
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

    # Enregistrer la session pour que la validation JTI passe
    try:
        from app.models.system import UserSession
        from datetime import datetime as _dt
        payload = decode_token(target_token)
        jti = payload.get("jti")
        exp = payload.get("exp")
        if jti:
            expires = _dt.utcfromtimestamp(exp) if exp else None
            db.add(UserSession(
                user_id=target_user.id, jti=jti,
                ip_address=request.client.host if request.client else None,
                user_agent=(request.headers.get("user-agent", "")[:300]),
                expires_at=expires,
            ))
            await db.commit()
    except Exception:
        pass

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

# ── 2FA TOTP ──────────────────────────────────────────────────────────────────

@router.get("/2fa/setup", response_class=HTMLResponse)
async def totp_setup_page(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Page de configuration 2FA (accessible à tout membre connecté)."""
    user_obj, member = ctx
    import pyotp, qrcode, io, base64
    # Génère un secret temporaire (pas encore activé)
    temp_secret = pyotp.random_base32()
    totp = pyotp.TOTP(temp_secret)
    provisioning_url = totp.provisioning_uri(
        name=user_obj.login,
        issuer_name=f"Portail Socrate",
    )
    # Génère le QR code
    qr = qrcode.make(provisioning_url)
    buf = io.BytesIO()
    qr.save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode()

    return templates.TemplateResponse(request, "pages/auth/2fa_setup.html", {
        "current_user": user_obj,
        "current_member": member,
        "temp_secret": temp_secret,
        "qr_b64": qr_b64,
        "already_enabled": bool(user_obj.totp_enabled),
    })


@router.post("/2fa/enable")
async def totp_enable(
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    temp_secret: str = Form(...),
    code: str = Form(...),
):
    """Active la 2FA après vérification du code."""
    import pyotp
    user_obj, member = ctx
    totp = pyotp.TOTP(temp_secret)
    if not totp.verify(code.strip(), valid_window=1):
        raise HTTPException(400, "Code invalide — vérifiez l'heure de votre appareil")
    user_obj.totp_secret = temp_secret
    user_obj.totp_enabled = True
    await db.commit()
    return RedirectResponse(url="/auth/2fa/setup?_msg=enabled", status_code=303)


@router.post("/2fa/disable")
async def totp_disable(
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    code: str = Form(...),
):
    """Désactive la 2FA après vérification."""
    import pyotp
    user_obj, member = ctx
    if not user_obj.totp_enabled or not user_obj.totp_secret:
        raise HTTPException(400, "2FA non activée")
    totp = pyotp.TOTP(user_obj.totp_secret)
    if not totp.verify(code.strip(), valid_window=1):
        raise HTTPException(400, "Code invalide")
    user_obj.totp_secret = None
    user_obj.totp_enabled = False
    await db.commit()
    return RedirectResponse(url="/auth/2fa/setup?_msg=disabled", status_code=303)


@router.get("/2fa/verify", response_class=HTMLResponse)
async def totp_verify_page(request: Request, next: str = "/"):
    """Page de saisie du code TOTP (après login si 2FA activé)."""
    return templates.TemplateResponse(request, "pages/auth/2fa_verify.html", {
        "next": next,
    })


@router.post("/2fa/verify")
async def totp_verify(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    code: str = Form(...),
    next: str = Form("/"),
):
    """Vérifie le code TOTP post-login (membre ou visiteur)."""
    import pyotp
    # Récupère le principal depuis le cookie (pas encore require_auth/
    # require_guest_auth car 2FA pas encore validée)
    raw_token = request.cookies.get("access_token")
    if not raw_token:
        return RedirectResponse(url="/auth/login", status_code=303)
    try:
        from app.dependencies import decode_token
        payload = decode_token(raw_token)
        principal_id = int(payload.get("sub"))
        is_guest = payload.get("principal") == "guest"
    except Exception:
        return RedirectResponse(url="/auth/login", status_code=303)

    from sqlalchemy import select
    if is_guest:
        from app.models.guest import GuestAccount
        u = (await db.execute(select(GuestAccount).where(GuestAccount.id == principal_id))).scalar_one_or_none()
    else:
        from app.models.identity import User
        u = (await db.execute(select(User).where(User.id == principal_id))).scalar_one_or_none()
    if not u or not u.totp_enabled or not u.totp_secret:
        return RedirectResponse(url=next or "/", status_code=303)

    totp = pyotp.TOTP(u.totp_secret)
    if not totp.verify(code.strip(), valid_window=1):
        return templates.TemplateResponse(request, "pages/auth/2fa_verify.html", {
            "error": "Code invalide. Réessayez.", "next": next,
        })

    # Re-signe le même token (même jti/exp) sans le claim "2fa_pending"
    from jose import jwt as _jwt
    from app.config import get_settings as _get_settings
    _settings = _get_settings()
    cleared_payload = {k: v for k, v in payload.items() if k != "2fa_pending"}
    new_access_token = _jwt.encode(cleared_payload, _settings.secret_key, algorithm=_settings.algorithm)

    safe_next = next if (next and next.startswith("/") and not next.startswith("//")) else "/"
    redirect = RedirectResponse(url=safe_next, status_code=303)
    redirect.set_cookie(
        "access_token", new_access_token,
        httponly=True, samesite="lax", secure=False,
        max_age=60 * 60 * 8,
    )
    return redirect


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


# ── Export RGPD self-service (droit à la portabilité) ─────────────────────

@router.get("/my-data")
async def export_my_data(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Permet à tout membre de télécharger ses propres données (RGPD)."""
    from fastapi.responses import StreamingResponse
    from app.services.rgpd import build_member_export_zip

    user, member = ctx
    buf = await build_member_export_zip(
        db, member, requested_by=f"{member.last_name} {member.first_name} (lui-même)"
    )

    try:
        from app.services.audit import log_audit
        await log_audit(
            db, actor_id=member.id, action="RGPD_SELF_EXPORT",
            target_type="member", target_id=member.id,
            target_label=f"{member.last_name} {member.first_name}",
            request=request, commit=True,
        )
    except Exception:
        pass

    fname = f"mes-donnees-{datetime.utcnow().strftime('%Y%m%d')}.zip"
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


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
    user.last_login_at = datetime.utcnow()
    await db.commit()

    # Auto-connexion immédiate — évite de renvoyer l'utilisateur sur le formulaire
    access_token = create_access_token({"sub": str(user.id)})
    refresh_token = create_refresh_token({"sub": str(user.id)})
    try:
        from app.models.system import UserSession
        payload = decode_token(access_token)
        jti = payload.get("jti")
        exp = payload.get("exp")
        if jti:
            from datetime import datetime as _dt
            expires = _dt.utcfromtimestamp(exp) if exp else None
            db.add(UserSession(
                user_id=user.id, jti=jti,
                ip_address=request.client.host if request.client else None,
                user_agent=(request.headers.get("user-agent", "")[:300]),
                expires_at=expires,
            ))
            await db.commit()
    except Exception:
        pass

    redirect = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    redirect.set_cookie("access_token", access_token, httponly=True, samesite="lax", secure=False, max_age=60 * 60 * 8)
    redirect.set_cookie("refresh_token", refresh_token, httponly=True, samesite="lax", secure=False, max_age=60 * 60 * 24 * 30)
    return redirect


# ── Logout ─────────────────────────────────────────────────────────────────

@router.get("/logout")
@router.post("/logout")
async def logout():
    response = RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")
    return response
