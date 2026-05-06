"""Point d'entrée FastAPI — Portail Socrate"""
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from typing import Annotated
from app.config import get_settings
from app.database import engine, Base, get_db
from app.dependencies import get_current_user
from app.routers import auth, members, meetings
from sqlalchemy.ext.asyncio import AsyncSession

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Démarrage : créer les tables si elles n'existent pas
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    # Arrêt
    await engine.dispose()


import traceback as _tb
from fastapi.responses import PlainTextResponse

app = FastAPI(
    title="Portail Socrate",
    description="Plateforme unifiée — Loge Socrate Raison et Progrès",
    version="1.0.0",
    docs_url="/api/docs" if settings.environment == "development" else None,
    redoc_url=None,
    lifespan=lifespan,
)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    tb = _tb.format_exc()
    return PlainTextResponse(f"Erreur interne:\n{tb}", status_code=500)

# ── Static files ───────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")

# ── Routers ────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(members.router)
app.include_router(meetings.router)

# (les autres routers seront ajoutés ici au fur et à mesure)
# app.include_router(programs.router)
# app.include_router(finance.router)
# app.include_router(documents.router)
# app.include_router(calendar.router)
# app.include_router(forum.router)
# app.include_router(chat.router)
# app.include_router(admin.router)


# ── Page d'accueil ─────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    ctx: Annotated[object, Depends(get_current_user)],
):
    if not ctx:
        return RedirectResponse(url="/auth/login")
    user, member = ctx
    from datetime import datetime
    return templates.TemplateResponse(request, "pages/dashboard.html", {
        "app_name": settings.app_name,
        "current_member": member,
        "current_user": user,
        "now": datetime.now(),
        "unread_notifications": 0,
        "unread_messages": 0,
    })


# ── Lien public inscription (alias court pour les programmes PDF) ──────────
# Ex: https://portail.amisdesocrate.fr/inscription/abc123

@app.get("/inscription/{token}", response_class=HTMLResponse)
async def public_registration(token: str):
    """Redirige vers la page d'inscription publique de la tenue."""
    return RedirectResponse(url=f"/meetings/public/{token}", status_code=302)


# ── Health check ───────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "app": settings.app_name}
