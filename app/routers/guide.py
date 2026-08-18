"""Guide utilisateur — page publique (aucune authentification requise), pour
qu'un membre puisse la consulter avant même d'avoir défini son mot de passe.
Guides de formation par rôle : authentifiés, accès restreint selon la fonction."""
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.dependencies import require_auth

router = APIRouter(tags=["guide"])
from app.template_engine import templates


# ── Helpers ──────────────────────────────────────────────────────────────────

def _function(member) -> str | None:
    return member.lodge_function.value if member and member.lodge_function else None


# ── Guide public ──────────────────────────────────────────────────────────────

@router.get("/guide", response_class=HTMLResponse)
async def guide_page(request: Request):
    settings = get_settings()
    return templates.TemplateResponse(request, "pages/guide/index.html", {
        "lodge_name": settings.lodge_name,
        "portal_url": str(request.base_url).rstrip("/"),
    })


@router.get("/guide.pdf")
async def guide_pdf(request: Request):
    import io
    from app.services.member_access import build_guide_pdf
    settings = get_settings()
    base_url = str(request.base_url).rstrip("/")
    pdf = build_guide_pdf(settings.lodge_name, base_url)
    return StreamingResponse(
        io.BytesIO(pdf), media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="guide-utilisateur-portail.pdf"'},
    )


# ── Guides de formation par rôle ─────────────────────────────────────────────

@router.get("/guides/membre", response_class=HTMLResponse)
async def guide_membre(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
):
    user, member = ctx
    return templates.TemplateResponse(request, "pages/guide/membre.html", {
        "current_user": user, "current_member": member,
    })


@router.get("/guides/tresorier", response_class=HTMLResponse)
async def guide_tresorier(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
):
    user, member = ctx
    is_admin = bool(getattr(user, "is_admin", False))
    fn = _function(member)
    if not (is_admin or fn in ("VM", "TRESORIER")):
        raise HTTPException(403)
    return templates.TemplateResponse(request, "pages/guide/tresorier.html", {
        "current_user": user, "current_member": member,
    })


@router.get("/guides/secretaire", response_class=HTMLResponse)
async def guide_secretaire(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
):
    user, member = ctx
    is_admin = bool(getattr(user, "is_admin", False))
    fn = _function(member)
    if not (is_admin or fn in ("VM", "SECRETAIRE")):
        raise HTTPException(403)
    return templates.TemplateResponse(request, "pages/guide/secretaire.html", {
        "current_user": user, "current_member": member,
    })


@router.get("/guides/maitre-banquets", response_class=HTMLResponse)
async def guide_maitre_banquets(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
):
    user, member = ctx
    is_admin = bool(getattr(user, "is_admin", False))
    fn = _function(member)
    if not (is_admin or fn in ("VM", "MAITRE_BANQUETS")):
        raise HTTPException(403)
    return templates.TemplateResponse(request, "pages/guide/maitre_banquets.html", {
        "current_user": user, "current_member": member,
    })


@router.get("/guides/vm", response_class=HTMLResponse)
async def guide_vm(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
):
    user, member = ctx
    is_admin = bool(getattr(user, "is_admin", False))
    fn = _function(member)
    if not (is_admin or fn == "VM"):
        raise HTTPException(403)
    return templates.TemplateResponse(request, "pages/guide/vm.html", {
        "current_user": user, "current_member": member,
    })


# ── Suivi ouverture/clic des emails d'accès (pas d'auth — appelé depuis l'email) ──

@router.get("/access/open/{token}")
async def access_track_open(token: str, db: Annotated[AsyncSession, Depends(get_db)]):
    """Pixel 1×1 de suivi d'ouverture de l'email d'accès."""
    from app.models.system import EmailLog
    from app.services.member_access import verify_access_track_token

    parsed = verify_access_track_token(token)
    if parsed:
        log_id, _ = parsed
        log = await db.get(EmailLog, log_id)
        if log and not log.opened_at:
            log.opened_at = datetime.utcnow()
            await db.commit()

    gif = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!"
           b"\xf9\x04\x00\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;")
    return Response(content=gif, media_type="image/gif",
                     headers={"Cache-Control": "no-cache, no-store"})


@router.get("/access/click/{token}")
async def access_track_click(
    token: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    url: str = "",
):
    """Enregistre le clic sur le lien de l'email d'accès puis redirige vers l'URL réelle."""
    from app.models.system import EmailLog
    from app.services.member_access import verify_access_track_token

    parsed = verify_access_track_token(token)
    if parsed:
        log_id, _ = parsed
        log = await db.get(EmailLog, log_id)
        if log:
            log.clicked_at = log.clicked_at or datetime.utcnow()
            await db.commit()

    target = url or "/"
    if not target.startswith("http"):
        target = "/"
    return RedirectResponse(url=target, status_code=302)
