"""Router — Espace visiteur (accès portail restreint). Phase 1 : coquille +
auth. Phase 2 : contenu consultable, chacun gated par un opt-in explicite
posé par un officier — jamais de visibilité par défaut (voir
Meeting.visible_to_guests, Document.visible_to_guests). Toute page de ce
module dépend de require_guest_auth, jamais de require_auth (voir
app/dependencies.py)."""
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import require_guest_auth, verify_password, hash_password
from app.models.guest import GuestAccount
from app.models.identity import MasonicGrade
from app.models.meetings import Meeting, MeetingStatus
from app.routers.meetings import _type_label
from app.models.documents import Document, DocFolder, DocSpace, DocStatus
from app.models.lodge import LodgeSettings

router = APIRouter(prefix="/visiteur", tags=["visiteur"])
from app.template_engine import templates

# Le grade réel du visiteur plafonne tout contenu grade-gated qui lui est
# ouvert, en plus de l'opt-in visible_to_guests — un rang absent (compte pas
# encore renseigné) vaut 0 : ne voit que ce qui est ouvert à "ALL" (le plus
# restrictif), jamais "tout voir par défaut".
_GUEST_GRADE_RANK = {MasonicGrade.APPRENTI: 1, MasonicGrade.COMPAGNON: 2, MasonicGrade.MAITRE: 3}
_MEETING_GRADE_RANK = {"APPRENTI": 1, "COMPAGNON": 2, "MAITRE": 3, "ALL": 0}
_MIN_GRADE_RANK = {"ALL": 0, "APPRENTI": 1, "COMPAGNON": 2, "MAITRE": 3}


def _guest_rank(guest: GuestAccount) -> int:
    return _GUEST_GRADE_RANK.get(guest.masonic_grade, 0)


@router.get("/", response_class=HTMLResponse)
async def visiteur_dashboard(
    request: Request,
    guest: Annotated[GuestAccount, Depends(require_guest_auth)],
):
    return templates.TemplateResponse(request, "pages/visiteur/dashboard.html", {
        "guest": guest,
    })


# ── Programme (tenues explicitement ouvertes aux visiteurs) ────────────────

@router.get("/programme", response_class=HTMLResponse)
async def visiteur_programme(
    request: Request,
    guest: Annotated[GuestAccount, Depends(require_guest_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    r = await db.execute(
        select(Meeting)
        .where(
            Meeting.visible_to_guests.is_(True),
            Meeting.status == MeetingStatus.PUBLISHED,
            Meeting.meeting_date >= date.today(),
        )
        .order_by(Meeting.meeting_date, Meeting.meeting_time)
    )
    guest_rank = _guest_rank(guest)
    meetings = [
        m for m in r.scalars().all()
        if guest_rank >= _MEETING_GRADE_RANK.get(m.grade.value, 0)
    ]
    return templates.TemplateResponse(request, "pages/visiteur/programme.html", {
        "guest": guest,
        "meetings": meetings,
        "type_label": _type_label,
    })


# ── Annuaire restreint (VM / Secrétaire uniquement) ─────────────────────────

@router.get("/contacts", response_class=HTMLResponse)
async def visiteur_contacts(
    request: Request,
    guest: Annotated[GuestAccount, Depends(require_guest_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    r = await db.execute(select(LodgeSettings).limit(1))
    lodge = r.scalar_one_or_none()
    return templates.TemplateResponse(request, "pages/visiteur/contacts.html", {
        "guest": guest,
        "lodge": lodge,
    })


# ── Documents : vraie arborescence espace → dossier → sous-dossier,
# limitée à ce qui est ouvert aux visiteurs (opt-in + grade) ───────────────
#
# Calculée en mémoire à chaque requête plutôt qu'en SQL récursif — le volume
# (bibliothèque d'une loge) reste modeste et ça garde la logique lisible.
# Règle simplificatrice assumée : un dossier qui échoue son propre contrôle
# de grade (le sien, pas celui d'un ancêtre — chaque dossier a son min_grade
# indépendant) est exclu avec tout ce qu'il contient, même si un de ses
# sous-dossiers serait par ailleurs ouvert et d'un grade plus permissif —
# cas de configuration incohérent qu'on ne cherche pas à supporter ici.

def _doc_guest_visible(doc: Document, guest_rank: int) -> bool:
    """Un document est visible à ce visiteur si lui-même, son dossier ou son
    espace a été explicitement ouvert aux visiteurs, ET que le grade du
    visiteur couvre à la fois le grade minimum du dossier et de l'espace
    (même logique à deux niveaux que pour un membre — voir
    _get_authorized_doc dans app/routers/documents.py)."""
    folder = doc.folder
    if not folder or folder.personal_owner_id:
        return False  # jamais un dossier personnel, flag ou pas
    space = folder.space
    opened = bool(doc.visible_to_guests or folder.visible_to_guests or (space and space.visible_to_guests))
    if not opened:
        return False
    if guest_rank < _MIN_GRADE_RANK.get(folder.min_grade.value, 0):
        return False
    if space and guest_rank < _MIN_GRADE_RANK.get(space.min_grade.value, 0):
        return False
    return True


async def _build_guest_doc_tree(db: AsyncSession, guest: GuestAccount) -> dict:
    """Retourne {"spaces": [...], "folder_nodes": {folder_id: node}} — la
    liste des espaces accessibles (avec leurs dossiers racine) et un index
    plat de tous les nœuds dossier atteints, pour les pages de détail
    (dossier) et la reconstruction du fil d'Ariane sans requêtes
    supplémentaires."""
    guest_rank = _guest_rank(guest)

    spaces = (
        await db.execute(select(DocSpace).order_by(DocSpace.order_position, DocSpace.name))
    ).scalars().all()
    folders = (
        await db.execute(
            select(DocFolder)
            .where(DocFolder.personal_owner_id.is_(None))
            .order_by(DocFolder.order_position, DocFolder.name)
        )
    ).scalars().all()
    docs = (
        await db.execute(
            select(Document).where(Document.status == DocStatus.PUBLISHED, Document.deleted_at.is_(None))
        )
    ).scalars().all()

    spaces_by_id = {s.id: s for s in spaces}
    children_by_parent: dict = {}
    for f in folders:
        children_by_parent.setdefault(f.parent_id, []).append(f)
    docs_by_folder: dict = {}
    for d in docs:
        docs_by_folder.setdefault(d.folder_id, []).append(d)

    def _folder_ok(f: DocFolder) -> bool:
        return guest_rank >= _MIN_GRADE_RANK.get(f.min_grade.value, 0)

    def _space_ok(s: DocSpace) -> bool:
        return guest_rank >= _MIN_GRADE_RANK.get(s.min_grade.value, 0)

    def _folder_opened(f: DocFolder) -> bool:
        space = spaces_by_id.get(f.space_id)
        return bool(f.visible_to_guests or (space and space.visible_to_guests))

    def _doc_visible(d: Document, f: DocFolder) -> bool:
        return bool(d.visible_to_guests or _folder_opened(f))

    folder_nodes: dict = {}

    def _build_folder(f: DocFolder, parent_node):
        if not _folder_ok(f):
            return None
        own_docs = [d for d in docs_by_folder.get(f.id, []) if _doc_visible(d, f)]
        node = {"folder": f, "docs": own_docs, "children": [], "parent": parent_node}
        for cf in children_by_parent.get(f.id, []):
            child_node = _build_folder(cf, node)
            if child_node:
                node["children"].append(child_node)
        if not own_docs and not node["children"]:
            return None
        folder_nodes[f.id] = node
        return node

    space_list = []
    for s in spaces:
        if not _space_ok(s):
            continue
        root_folders = [
            n for f in children_by_parent.get(None, []) if f.space_id == s.id
            for n in [_build_folder(f, None)] if n
        ]
        if root_folders:
            space_list.append({"space": s, "folders": root_folders})

    return {"spaces": space_list, "folder_nodes": folder_nodes}


@router.get("/documents", response_class=HTMLResponse)
async def visiteur_documents(
    request: Request,
    guest: Annotated[GuestAccount, Depends(require_guest_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tree = await _build_guest_doc_tree(db, guest)
    return templates.TemplateResponse(request, "pages/visiteur/documents.html", {
        "guest": guest,
        "spaces": tree["spaces"],
    })


@router.get("/documents/dossier/{folder_id}", response_class=HTMLResponse)
async def visiteur_documents_folder(
    folder_id: int,
    request: Request,
    guest: Annotated[GuestAccount, Depends(require_guest_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tree = await _build_guest_doc_tree(db, guest)
    node = tree["folder_nodes"].get(folder_id)
    if not node:
        raise HTTPException(status_code=404)

    breadcrumb = []
    n = node
    while n:
        breadcrumb.append(n["folder"])
        n = n["parent"]
    breadcrumb.reverse()

    space = await db.get(DocSpace, node["folder"].space_id)

    return templates.TemplateResponse(request, "pages/visiteur/documents_folder.html", {
        "guest": guest,
        "node": node,
        "breadcrumb": breadcrumb,
        "space": space,
    })


async def _get_guest_doc_or_404(db: AsyncSession, doc_id: int, guest: GuestAccount) -> Document:
    r = await db.execute(
        select(Document)
        .options(selectinload(Document.folder).selectinload(DocFolder.space))
        .where(Document.id == doc_id)
    )
    doc = r.scalar_one_or_none()
    if (
        not doc
        or doc.status != DocStatus.PUBLISHED
        or doc.deleted_at is not None
        or not _doc_guest_visible(doc, _guest_rank(guest))
    ):
        raise HTTPException(status_code=404)
    return doc


@router.get("/documents/{doc_id}/view", response_class=HTMLResponse)
async def visiteur_document_view(
    doc_id: int,
    request: Request,
    guest: Annotated[GuestAccount, Depends(require_guest_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    doc = await _get_guest_doc_or_404(db, doc_id, guest)
    is_previewable = bool(
        doc.storage_path and doc.mime_type
        and (doc.mime_type.startswith("image/") or doc.mime_type == "application/pdf")
    )
    return templates.TemplateResponse(request, "pages/visiteur/document_view.html", {
        "guest": guest,
        "doc": doc,
        "folder": doc.folder,
        "space": doc.folder.space,
        "is_previewable": is_previewable,
    })


@router.get("/documents/{doc_id}/preview")
async def visiteur_document_preview(
    doc_id: int,
    guest: Annotated[GuestAccount, Depends(require_guest_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Flux inline (Content-Disposition: inline) pour l'aperçu PDF/image dans
    document_view.html — pas de compteur de téléchargement, comme côté
    membre (documents_preview dans app/routers/documents.py)."""
    doc = await _get_guest_doc_or_404(db, doc_id, guest)
    if not doc.storage_path:
        raise HTTPException(status_code=404, detail="Pas de fichier à prévisualiser")
    path = Path(doc.storage_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Fichier introuvable sur le serveur")
    return FileResponse(
        path=path,
        media_type=doc.mime_type or "application/octet-stream",
        headers={"Content-Disposition": "inline"},
    )


@router.get("/documents/{doc_id}/download")
async def visiteur_document_download(
    doc_id: int,
    guest: Annotated[GuestAccount, Depends(require_guest_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    doc = await _get_guest_doc_or_404(db, doc_id, guest)

    if doc.link_url:
        return RedirectResponse(url=doc.link_url, status_code=302)

    if not doc.storage_path:
        raise HTTPException(status_code=404, detail="Fichier introuvable")
    path = Path(doc.storage_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Fichier introuvable sur le serveur")

    doc.download_count = (doc.download_count or 0) + 1
    await db.commit()
    return FileResponse(
        path=path,
        filename=doc.original_filename,
        media_type=doc.mime_type or "application/octet-stream",
    )


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
