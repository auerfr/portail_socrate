"""Router — Formation (guides de révision Apprenti/Compagnon, marque et nom
compagnonnique). Menu séparé de Pierre d'Angle (cf. échange du 03/10/2026)."""
import copy
import json
from pathlib import Path
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin, require_auth
from app.models.content import FormationResource, FormationProgress, FormationContent
from app.models.identity import MasonicGrade

router = APIRouter(tags=["formation"])
from app.template_engine import templates

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_data_cache: dict[str, dict] = {}


def _asset_version(*static_paths: str) -> int:
    """Horodatage (mtime) le plus récent parmi les fichiers statiques donnés,
    utilisé en paramètre ?v= pour casser le cache navigateur à chaque
    modification de formation.css/formation.js — sans ça, un membre qui a
    déjà chargé la page une fois garde l'ancienne feuille de style/script en
    cache et ne voit jamais les correctifs tant qu'il ne force pas un
    rechargement complet."""
    static_dir = Path(__file__).resolve().parent.parent / "static"
    mtimes = []
    for rel in static_paths:
        p = static_dir / rel
        try:
            mtimes.append(int(p.stat().st_mtime))
        except OSError:
            pass
    return max(mtimes) if mtimes else 0


_FORMATION_ASSET_VERSION = _asset_version("css/formation.css", "js/formation.js")


def _load_module_data(filename: str) -> dict:
    """Charge le contenu JSON *d'origine* d'un module de formation (fichier
    versionné dans le dépôt), mis en cache en mémoire pour la durée du
    process. Ce contenu ne change qu'à un redéploiement — les corrections
    ponctuelles passent par FormationContent (cf. _get_effective_data)."""
    if filename not in _data_cache:
        path = _DATA_DIR / filename
        with path.open(encoding="utf-8") as f:
            _data_cache[filename] = json.load(f)
    return _data_cache[filename]


# Modules dont le contenu est éditable depuis /admin/formation/<module> —
# chaque entrée pointe vers le fichier JSON d'origine servant de source tant
# qu'aucune correction n'a été enregistrée en base (FormationContent).
_MODULE_DATA_FILE = {
    "godf": "formation_godf.json",
}


async def _get_effective_data(db: AsyncSession, module: str) -> dict:
    """Contenu actuellement servi pour un module : la correction enregistrée
    en base si elle existe, sinon le fichier JSON d'origine.

    Renvoie toujours une copie profonde : _load_module_data met le fichier
    d'origine en cache en mémoire pour tout le process, et les routes
    d'admin qui appellent cette fonction modifient le dict reçu en place
    avant de le sauvegarder — sans copie, une première correction sans
    FormationContent existant muterait irrémédiablement le contenu
    "d'origine" mis en cache, et "Revenir au contenu d'origine" ne
    reviendrait alors plus jamais vraiment à l'original."""
    row = (await db.execute(
        select(FormationContent).where(FormationContent.module == module)
    )).scalar_one_or_none()
    if row is not None:
        return copy.deepcopy(row.data)
    return copy.deepcopy(_load_module_data(_MODULE_DATA_FILE[module]))


async def _save_module_content(db: AsyncSession, module: str, data: dict, member_id: Optional[int]) -> None:
    row = (await db.execute(
        select(FormationContent).where(FormationContent.module == module)
    )).scalar_one_or_none()
    if row is None:
        row = FormationContent(module=module)
        db.add(row)
    row.data = data
    row.updated_by_id = member_id
    await db.commit()

_GRADE_ORDER = {MasonicGrade.APPRENTI: 1, MasonicGrade.COMPAGNON: 2, MasonicGrade.MAITRE: 3}

# Grade minimum requis pour chaque volet — le contenu rituel de la Pierre
# Dégrossie (Apprenti) et de la Pierre Taillée (Compagnon) ne doit jamais
# être servi à un compte qui n'a pas encore ce grade (cf. échange sur
# Pierre d'Angle/Pierre Taillée : traitement invisible, pas un simple flou).
_MODULE_MIN_GRADE = {
    "apprenti": 1,   # APPRENTI
    "compagnon": 2,  # COMPAGNON
    "marque": 2,     # COMPAGNON (marque + nom compagnonnique)
    "godf": 1,       # APPRENTI — ouvert à tous les membres, aucun contenu rituel
}

_MODULES = {
    "apprenti": {
        "title": "Pierre Dégrossie — Apprenti",
        "subtitle": "Guide de révision pour l'examen de passage Apprenti → Apprenti Accompli",
        "ready": True,
    },
    "godf": {
        "title": "L'organisation du G∴O∴D∴F∴",
        "subtitle": "De la Loge au Conseil de l'Ordre — pour les Apprentis et pour se remémorer",
        "ready": True,
    },
    "compagnon": {
        "title": "Pierre Taillée — Compagnon",
        "subtitle": "Guide de révision pour l'examen de Compagnon-Accompli",
        "ready": False,
    },
    "marque": {
        "title": "Marque & nom compagnonnique",
        "subtitle": "Fabrication de la marque et recherche du nom compagnonnique",
        "ready": False,
    },
}


class ProgressPayload(BaseModel):
    completed_steps: list[str] = []
    quiz_scores: dict[str, int] = {}
    final_quiz_score: Optional[int] = None


def _member_level(user, member) -> int:
    if user.is_admin:
        return 99
    return _GRADE_ORDER.get(member.masonic_grade, 0)


@router.get("/formation", response_class=HTMLResponse)
async def formation_index(request: Request, ctx: Annotated[tuple, Depends(require_auth)], db: Annotated[AsyncSession, Depends(get_db)]):
    user, member = ctx
    lvl = _member_level(user, member)
    modules = []
    for key, info in _MODULES.items():
        modules.append({
            "key": key,
            "title": info["title"],
            "subtitle": info["subtitle"],
            "ready": info["ready"],
            "accessible": lvl >= _MODULE_MIN_GRADE[key],
        })
    return templates.TemplateResponse(request, "pages/formation/index.html", {
        "modules": modules,
        "is_admin": user.is_admin,
        "current_user": user,
        "current_member": member,
    })


@router.get("/formation/apprenti", response_class=HTMLResponse)
async def formation_apprenti(request: Request, ctx: Annotated[tuple, Depends(require_auth)], db: Annotated[AsyncSession, Depends(get_db)]):
    user, member = ctx
    lvl = _member_level(user, member)
    if lvl < _MODULE_MIN_GRADE["apprenti"]:
        return templates.TemplateResponse(request, "pages/formation/unavailable.html", {
            "current_user": user, "current_member": member,
        }, status_code=403)

    res = (await db.execute(
        select(FormationResource).where(FormationResource.module == "apprenti")
    )).scalar_one_or_none()
    pdf_document_id = res.pdf_document_id if res else None

    return templates.TemplateResponse(request, "pages/formation/apprenti.html", {
        "is_admin": user.is_admin,
        "pdf_document_id": pdf_document_id,
        "current_user": user,
        "current_member": member,
    })


@router.get("/formation/godf", response_class=HTMLResponse)
async def formation_godf(request: Request, ctx: Annotated[tuple, Depends(require_auth)], db: Annotated[AsyncSession, Depends(get_db)]):
    user, member = ctx
    lvl = _member_level(user, member)
    if lvl < _MODULE_MIN_GRADE["godf"]:
        return templates.TemplateResponse(request, "pages/formation/unavailable.html", {
            "current_user": user, "current_member": member,
        }, status_code=403)

    data = await _get_effective_data(db, "godf")

    progress = (await db.execute(
        select(FormationProgress).where(
            FormationProgress.member_id == member.id,
            FormationProgress.module == "godf",
        )
    )).scalar_one_or_none()
    progress_state = progress.state if progress else {}

    return templates.TemplateResponse(request, "pages/formation/godf.html", {
        "is_admin": user.is_admin,
        "module_data": data,
        "module_data_json": json.dumps(data, ensure_ascii=False),
        "progress_json": json.dumps(progress_state, ensure_ascii=False),
        "asset_v": _FORMATION_ASSET_VERSION,
        "current_user": user,
        "current_member": member,
    })


@router.post("/formation/godf/progress")
async def formation_godf_save_progress(
    payload: ProgressPayload,
    ctx: Annotated[tuple, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    lvl = _member_level(user, member)
    if lvl < _MODULE_MIN_GRADE["godf"]:
        return {"ok": False}

    row = (await db.execute(
        select(FormationProgress).where(
            FormationProgress.member_id == member.id,
            FormationProgress.module == "godf",
        )
    )).scalar_one_or_none()
    if row is None:
        row = FormationProgress(member_id=member.id, module="godf")
        db.add(row)

    row.state = payload.model_dump()
    await db.commit()
    return {"ok": True}


@router.get("/admin/formation", response_class=HTMLResponse)
async def formation_admin(request: Request, ctx: Annotated[tuple, Depends(require_admin)], db: Annotated[AsyncSession, Depends(get_db)], saved: int = 0):
    user, member = ctx
    rows = (await db.execute(select(FormationResource))).scalars().all()
    by_module = {r.module: r for r in rows}
    modules = []
    for key, info in _MODULES.items():
        modules.append({
            "key": key,
            "title": info["title"],
            "pdf_document_id": by_module[key].pdf_document_id if key in by_module else None,
            "has_content_editor": key in _MODULE_DATA_FILE,
        })
    return templates.TemplateResponse(request, "pages/formation/admin.html", {
        "modules": modules,
        "saved": saved,
        "current_user": user,
        "current_member": member,
    })


@router.post("/admin/formation/{module}/save")
async def formation_admin_save(
    request: Request,
    module: str,
    ctx: Annotated[tuple, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    pdf_document_id: str = Form(""),
):
    user, member = ctx
    if module not in _MODULES:
        return RedirectResponse("/admin/formation", status_code=303)

    res = (await db.execute(
        select(FormationResource).where(FormationResource.module == module)
    )).scalar_one_or_none()
    if res is None:
        res = FormationResource(module=module)
        db.add(res)

    res.pdf_document_id = int(pdf_document_id) if pdf_document_id.strip().isdigit() else None
    res.updated_by_id = member.id if member else None
    await db.commit()
    return RedirectResponse("/admin/formation?saved=1", status_code=303)


# ── Édition du contenu — module "godf" ─────────────────────────────────────
# Formulaires HTML classiques (sans JS) : chaque liste modifiable affiche ses
# éléments existants (avec une case "Supprimer") puis 2 lignes vierges pour
# en ajouter — pas de recopie/suppression dynamique en JS, pour rester un
# simple formulaire serveur robuste, cohérent avec le reste de l'admin.
_EXTRA_BLANK_ROWS = 2


@router.get("/admin/formation/godf", response_class=HTMLResponse)
async def formation_admin_godf_index(request: Request, ctx: Annotated[tuple, Depends(require_admin)], db: Annotated[AsyncSession, Depends(get_db)], saved: int = 0):
    user, member = ctx
    data = await _get_effective_data(db, "godf")
    is_customized = (await db.execute(
        select(FormationContent).where(FormationContent.module == "godf")
    )).scalar_one_or_none() is not None
    return templates.TemplateResponse(request, "pages/formation/admin_godf_index.html", {
        "module_data": data,
        "is_customized": is_customized,
        "saved": saved,
        "current_user": user,
        "current_member": member,
    })


@router.post("/admin/formation/godf/reset")
async def formation_admin_godf_reset(ctx: Annotated[tuple, Depends(require_admin)], db: Annotated[AsyncSession, Depends(get_db)]):
    row = (await db.execute(
        select(FormationContent).where(FormationContent.module == "godf")
    )).scalar_one_or_none()
    if row is not None:
        await db.delete(row)
        await db.commit()
    return RedirectResponse("/admin/formation/godf?saved=1", status_code=303)


@router.get("/admin/formation/godf/step/{step_id}", response_class=HTMLResponse)
async def formation_admin_godf_step(request: Request, step_id: str, ctx: Annotated[tuple, Depends(require_admin)], db: Annotated[AsyncSession, Depends(get_db)], saved: int = 0):
    user, member = ctx
    data = await _get_effective_data(db, "godf")
    step = next((s for s in data["steps"] if s["id"] == step_id), None)
    if step is None:
        return RedirectResponse("/admin/formation/godf", status_code=303)
    return templates.TemplateResponse(request, "pages/formation/admin_godf_step.html", {
        "step": step,
        "extra_rows": _EXTRA_BLANK_ROWS,
        "saved": saved,
        "current_user": user,
        "current_member": member,
    })


@router.post("/admin/formation/godf/step/{step_id}/save")
async def formation_admin_godf_step_save(request: Request, step_id: str, ctx: Annotated[tuple, Depends(require_admin)], db: Annotated[AsyncSession, Depends(get_db)]):
    user, member = ctx
    data = await _get_effective_data(db, "godf")
    step = next((s for s in data["steps"] if s["id"] == step_id), None)
    if step is None:
        return RedirectResponse("/admin/formation/godf", status_code=303)

    form = await request.form()

    new_sections = []
    i = 0
    while f"sec_label_{i}" in form:
        label = (form.get(f"sec_label_{i}") or "").strip()
        text = (form.get(f"sec_text_{i}") or "").strip()
        deleted = form.get(f"sec_delete_{i}") == "1"
        if label and text and not deleted:
            new_sections.append({"label": label, "text": text})
        i += 1
    step["sections"] = new_sections

    new_quiz = []
    i = 0
    while f"q_question_{i}" in form:
        question = (form.get(f"q_question_{i}") or "").strip()
        options = [(form.get(f"q_opt_{i}_{j}") or "").strip() for j in range(4)]
        correct_raw = form.get(f"q_correct_{i}")
        explanation = (form.get(f"q_explain_{i}") or "").strip()
        deleted = form.get(f"q_delete_{i}") == "1"
        if question and all(options) and correct_raw is not None and not deleted:
            new_quiz.append({
                "question": question,
                "options": options,
                "correct": int(correct_raw),
                "explanation": explanation,
            })
        i += 1
    step["quiz"] = new_quiz

    await _save_module_content(db, "godf", data, member.id if member else None)
    return RedirectResponse(f"/admin/formation/godf/step/{step_id}?saved=1", status_code=303)


@router.get("/admin/formation/godf/glossary", response_class=HTMLResponse)
async def formation_admin_godf_glossary(request: Request, ctx: Annotated[tuple, Depends(require_admin)], db: Annotated[AsyncSession, Depends(get_db)], saved: int = 0):
    user, member = ctx
    data = await _get_effective_data(db, "godf")
    return templates.TemplateResponse(request, "pages/formation/admin_godf_glossary.html", {
        "glossary": data["glossary"],
        "extra_rows": _EXTRA_BLANK_ROWS,
        "saved": saved,
        "current_user": user,
        "current_member": member,
    })


@router.post("/admin/formation/godf/glossary/save")
async def formation_admin_godf_glossary_save(request: Request, ctx: Annotated[tuple, Depends(require_admin)], db: Annotated[AsyncSession, Depends(get_db)]):
    user, member = ctx
    data = await _get_effective_data(db, "godf")
    form = await request.form()

    new_glossary = {}
    i = 0
    while f"g_term_{i}" in form:
        term = (form.get(f"g_term_{i}") or "").strip()
        definition = (form.get(f"g_def_{i}") or "").strip()
        deleted = form.get(f"g_delete_{i}") == "1"
        if term and definition and not deleted:
            new_glossary[term] = definition
        i += 1
    data["glossary"] = new_glossary

    await _save_module_content(db, "godf", data, member.id if member else None)
    return RedirectResponse("/admin/formation/godf/glossary?saved=1", status_code=303)


@router.get("/admin/formation/godf/final-quiz", response_class=HTMLResponse)
async def formation_admin_godf_final_quiz(request: Request, ctx: Annotated[tuple, Depends(require_admin)], db: Annotated[AsyncSession, Depends(get_db)], saved: int = 0):
    user, member = ctx
    data = await _get_effective_data(db, "godf")
    return templates.TemplateResponse(request, "pages/formation/admin_godf_final_quiz.html", {
        "questions": data["final_quiz"],
        "extra_rows": _EXTRA_BLANK_ROWS,
        "saved": saved,
        "current_user": user,
        "current_member": member,
    })


@router.post("/admin/formation/godf/final-quiz/save")
async def formation_admin_godf_final_quiz_save(request: Request, ctx: Annotated[tuple, Depends(require_admin)], db: Annotated[AsyncSession, Depends(get_db)]):
    user, member = ctx
    data = await _get_effective_data(db, "godf")
    form = await request.form()

    new_quiz = []
    i = 0
    while f"fq_question_{i}" in form:
        question = (form.get(f"fq_question_{i}") or "").strip()
        options = [(form.get(f"fq_opt_{i}_{j}") or "").strip() for j in range(4)]
        correct_raw = form.get(f"fq_correct_{i}")
        deleted = form.get(f"fq_delete_{i}") == "1"
        if question and all(options) and correct_raw is not None and not deleted:
            new_quiz.append({"question": question, "options": options, "correct": int(correct_raw)})
        i += 1
    data["final_quiz"] = new_quiz

    await _save_module_content(db, "godf", data, member.id if member else None)
    return RedirectResponse("/admin/formation/godf/final-quiz?saved=1", status_code=303)


@router.get("/admin/formation/godf/diagram", response_class=HTMLResponse)
async def formation_admin_godf_diagram(request: Request, ctx: Annotated[tuple, Depends(require_admin)], db: Annotated[AsyncSession, Depends(get_db)], saved: int = 0):
    user, member = ctx
    data = await _get_effective_data(db, "godf")
    return templates.TemplateResponse(request, "pages/formation/admin_godf_diagram.html", {
        "diagram": data["diagram"],
        "saved": saved,
        "current_user": user,
        "current_member": member,
    })


@router.post("/admin/formation/godf/diagram/save")
async def formation_admin_godf_diagram_save(request: Request, ctx: Annotated[tuple, Depends(require_admin)], db: Annotated[AsyncSession, Depends(get_db)]):
    user, member = ctx
    data = await _get_effective_data(db, "godf")
    form = await request.form()

    for node in data["diagram"]["nodes"]:
        nid = node["id"]
        label = (form.get(f"node_label_{nid}") or "").strip()
        qui = (form.get(f"node_qui_{nid}") or "").strip()
        elu = (form.get(f"node_elu_{nid}") or "").strip()
        role = (form.get(f"node_role_{nid}") or "").strip()
        if label:
            node["label"] = label
        if qui:
            node["qui"] = qui
        if elu:
            node["elu"] = elu
        if role:
            node["role"] = role

    await _save_module_content(db, "godf", data, member.id if member else None)
    return RedirectResponse("/admin/formation/godf/diagram?saved=1", status_code=303)
