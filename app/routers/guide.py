"""Guide utilisateur — page publique (aucune authentification requise), pour
qu'un membre puisse la consulter avant même d'avoir défini son mot de passe."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from app.config import get_settings

router = APIRouter(tags=["guide"])
from app.template_engine import templates


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
