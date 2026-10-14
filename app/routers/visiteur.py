"""Router — Espace visiteur (accès portail restreint). Coquille Phase 1 :
aucun contenu consultable pour l'instant (programme/annuaire/documents en
Phase 2, forum/sondages en Phase 3) — juste l'accueil, le badge d'identité
et le changement de mot de passe/2FA. Toute page de ce module dépend de
require_guest_auth, jamais de require_auth (voir app/dependencies.py)."""
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_guest_auth, verify_password, hash_password
from app.models.guest import GuestAccount

router = APIRouter(prefix="/visiteur", tags=["visiteur"])
from app.template_engine import templates


@router.get("/", response_class=HTMLResponse)
async def visiteur_dashboard(
    request: Request,
    guest: Annotated[GuestAccount, Depends(require_guest_auth)],
):
    return templates.TemplateResponse(request, "pages/visiteur/dashboard.html", {
        "guest": guest,
    })


@router.get("/mot-de-passe", response_class=HTMLResponse)
async def visiteur_password_page(
    request: Request,
    guest: Annotated[GuestAccount, Depends(require_guest_auth)],
):
    return templates.TemplateResponse(request, "pages/visiteur/password.html", {
        "guest": guest,
    })


@router.post("/mot-de-passe", response_class=HTMLResponse)
async def visiteur_password_submit(
    request: Request,
    guest: Annotated[GuestAccount, Depends(require_guest_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    def error(msg: str):
        return templates.TemplateResponse(request, "pages/visiteur/password.html", {
            "guest": guest, "error": msg,
        }, status_code=400)

    if not guest.password_hash or not verify_password(current_password, guest.password_hash):
        return error("Mot de passe actuel incorrect.")
    if len(new_password) < 8:
        return error("Le nouveau mot de passe doit contenir au moins 8 caractères.")
    if new_password != confirm_password:
        return error("Les deux nouveaux mots de passe ne correspondent pas.")

    guest.password_hash = hash_password(new_password)
    await db.commit()
    return templates.TemplateResponse(request, "pages/visiteur/password.html", {
        "guest": guest, "success": True,
    })
