"""Confirmation annuelle des correspondants externes — pages publiques (aucune
authentification requise, les liens sont dans un email envoyé à des
personnes qui n'ont pas de compte sur le portail)."""
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.lodge import ExternalContact

router = APIRouter(prefix="/contacts", tags=["contact-confirmation"])
from app.template_engine import templates


@router.get("/confirm/{token}", response_class=HTMLResponse)
async def contact_confirm(token: str, request: Request, db: Annotated[AsyncSession, Depends(get_db)]):
    from datetime import datetime
    from app.services.contact_confirmation import verify_cc_token

    parsed = verify_cc_token(token)
    contact = None
    if parsed:
        contact_id, kind = parsed
        if kind == "confirm":
            contact = await db.get(ExternalContact, contact_id)
            if contact:
                contact.last_confirmed_at = datetime.utcnow()
                await db.commit()

    return templates.TemplateResponse(request, "pages/contacts/confirm.html", {
        "ok": contact is not None,
        "contact": contact,
    })


@router.get("/update-email/{token}", response_class=HTMLResponse)
async def contact_update_email_form(token: str, request: Request, db: Annotated[AsyncSession, Depends(get_db)]):
    from app.services.contact_confirmation import verify_cc_token

    parsed = verify_cc_token(token)
    contact = None
    if parsed:
        contact_id, kind = parsed
        if kind == "update":
            contact = await db.get(ExternalContact, contact_id)

    return templates.TemplateResponse(request, "pages/contacts/update_email.html", {
        "valid": contact is not None,
        "contact": contact,
        "token": token,
        "done": False,
    })


@router.post("/update-email/{token}", response_class=HTMLResponse)
async def contact_update_email_submit(
    token: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    email: Annotated[str, Form()],
):
    from datetime import datetime
    from app.services.contact_confirmation import verify_cc_token

    parsed = verify_cc_token(token)
    contact = None
    error = None
    if parsed:
        contact_id, kind = parsed
        if kind == "update":
            contact = await db.get(ExternalContact, contact_id)

    new_email = email.strip().lower()
    if contact and "@" in new_email:
        contact.email = new_email
        contact.last_confirmed_at = datetime.utcnow()
        await db.commit()
    elif contact:
        error = "Adresse email invalide."

    return templates.TemplateResponse(request, "pages/contacts/update_email.html", {
        "valid": contact is not None,
        "contact": contact,
        "token": token,
        "done": contact is not None and not error,
        "error": error,
    })
