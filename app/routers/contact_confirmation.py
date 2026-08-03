"""Confirmation annuelle des correspondants externes — pages publiques (aucune
authentification requise, les liens sont dans un email envoyé à des
personnes qui n'ont pas de compte sur le portail)."""
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.lodge import ExternalContact
from app.template_engine import templates

router = APIRouter(prefix="/contacts", tags=["contact-confirmation"])


# ── Mise à jour du profil (= confirmation) ─────────────────────────────────

@router.get("/update/{token}", response_class=HTMLResponse)
async def contact_update_form(token: str, request: Request, db: Annotated[AsyncSession, Depends(get_db)]):
    from app.services.contact_confirmation import verify_cc_token, make_cc_token
    parsed = verify_cc_token(token)
    contact = None
    remove_token = None
    if parsed:
        contact_id, kind = parsed
        if kind in ("update", "confirm"):
            contact = await db.get(ExternalContact, contact_id)
            if contact:
                remove_token = make_cc_token(contact_id, "remove")

    return templates.TemplateResponse(request, "pages/contacts/update.html", {
        "valid": contact is not None,
        "contact": contact,
        "token": token,
        "remove_token": remove_token,
        "done": False,
        "error": None,
    })


@router.post("/update/{token}", response_class=HTMLResponse)
async def contact_update_submit(
    token: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    first_name: Annotated[str, Form()] = "",
    last_name: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    lodge_name: Annotated[str, Form()] = "",
    orient: Annotated[str, Form()] = "",
    obedience: Annotated[str, Form()] = "",
):
    from datetime import datetime
    from app.services.contact_confirmation import verify_cc_token

    parsed = verify_cc_token(token)
    contact = None
    error = None
    if parsed:
        contact_id, kind = parsed
        if kind in ("update", "confirm"):
            contact = await db.get(ExternalContact, contact_id)

    new_email = email.strip().lower()
    if contact:
        if new_email and "@" not in new_email:
            error = "Adresse email invalide."
        else:
            fn = first_name.strip()
            ln = last_name.strip()
            if fn or ln:
                contact.first_name = fn or None
                contact.last_name  = ln or None
                contact.name = f"{fn} {ln}".strip() or contact.name
            if new_email and new_email != contact.email:
                contact.email = new_email
            if lodge_name.strip():
                contact.lodge_name = lodge_name.strip()
            if orient.strip():
                contact.orient = orient.strip()
            if obedience.strip():
                contact.obedience = obedience.strip()
            contact.last_confirmed_at = datetime.utcnow()
            await db.commit()

    remove_token = None
    if contact:
        from app.services.contact_confirmation import make_cc_token
        remove_token = make_cc_token(contact.id, "remove")

    return templates.TemplateResponse(request, "pages/contacts/update.html", {
        "valid": contact is not None,
        "contact": contact,
        "token": token,
        "remove_token": remove_token,
        "done": contact is not None and error is None,
        "error": error,
    })


# ── Lien de compatibilité ascendante (ancien lien "confirmer") ─────────────

@router.get("/confirm/{token}", response_class=HTMLResponse)
async def contact_confirm_legacy(token: str, request: Request):
    from app.services.contact_confirmation import verify_cc_token
    parsed = verify_cc_token(token)
    if parsed:
        contact_id, kind = parsed
        new_token = token.replace(f".{kind}.", ".update.")
        from app.services.contact_confirmation import make_cc_token
        new_token = make_cc_token(contact_id, "update")
        return RedirectResponse(url=f"/contacts/update/{new_token}", status_code=302)
    return RedirectResponse(url="/contacts/update/invalid", status_code=302)


# ── Désinscription ─────────────────────────────────────────────────────────

@router.get("/remove/{token}", response_class=HTMLResponse)
async def contact_remove_form(token: str, request: Request, db: Annotated[AsyncSession, Depends(get_db)]):
    from app.services.contact_confirmation import verify_cc_token
    parsed = verify_cc_token(token)
    contact = None
    if parsed:
        contact_id, kind = parsed
        if kind == "remove":
            contact = await db.get(ExternalContact, contact_id)

    return templates.TemplateResponse(request, "pages/contacts/remove.html", {
        "valid": contact is not None,
        "contact": contact,
        "token": token,
        "done": False,
    })


@router.post("/remove/{token}", response_class=HTMLResponse)
async def contact_remove_submit(token: str, request: Request, db: Annotated[AsyncSession, Depends(get_db)]):
    from datetime import datetime
    from app.services.contact_confirmation import verify_cc_token

    parsed = verify_cc_token(token)
    contact = None
    if parsed:
        contact_id, kind = parsed
        if kind == "remove":
            contact = await db.get(ExternalContact, contact_id)
            if contact:
                contact.removal_requested_at = datetime.utcnow()
                contact.is_active = False
                await db.commit()

    return templates.TemplateResponse(request, "pages/contacts/remove.html", {
        "valid": contact is not None,
        "contact": contact,
        "token": token,
        "done": contact is not None,
    })


# ── Compat ancien lien update-email ────────────────────────────────────────

@router.get("/update-email/{token}", response_class=HTMLResponse)
async def contact_update_email_compat(token: str):
    return RedirectResponse(url=f"/contacts/update/{token}", status_code=302)
