"""FAQ du portail — page authentifiée, avec des sections par fonction
(Trésorier, Secrétaire, Maître des Banquets, Vénérable Maître) visibles
uniquement aux personnes concernées (+ VM/admin qui supervisent tout)."""
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.dependencies import require_auth

router = APIRouter(tags=["faq"])
from app.template_engine import templates


@router.get("/faq", response_class=HTMLResponse)
async def faq_page(
    request: Request,
    ctx: Annotated[tuple, Depends(require_auth)],
):
    user, member = ctx
    is_admin = bool(getattr(user, "is_admin", False))
    function = member.lodge_function.value if member and member.lodge_function else None

    show_tresorier = is_admin or function in ("VM", "TRESORIER")
    show_secretaire = is_admin or function in ("VM", "SECRETAIRE")
    show_banquets = is_admin or function in ("VM", "MAITRE_BANQUETS")
    show_vm = is_admin or function == "VM"

    return templates.TemplateResponse(request, "pages/faq/index.html", {
        "current_user": user,
        "current_member": member,
        "show_tresorier": show_tresorier,
        "show_secretaire": show_secretaire,
        "show_banquets": show_banquets,
        "show_vm": show_vm,
        # guide links visibility (admin sees all)
        "show_guide_tresorier": show_tresorier,
        "show_guide_secretaire": show_secretaire,
        "show_guide_banquets": show_banquets,
        "show_guide_vm": show_vm,
    })
