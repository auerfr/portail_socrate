"""Router — Bibliothèque documentaire (GED)"""
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import require_admin, require_auth
from app.models.documents import DocFolder, DocSpace, DocStatus, Document, DocumentVersion, MinGrade
from app.models.groups import LodgeGroup
from app.models.identity import MasonicGrade, Member
from app.routers.groups import resolve_group_member_ids

router = APIRouter(prefix="/documents", tags=["documents"])
templates = Jinja2Templates(directory="app/templates")

UPLOAD_DIR = Path("uploads/documents")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 Mo
ALLOWED_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".odt",
    ".xls", ".xlsx", ".ods",
    ".ppt", ".pptx", ".odp",
    ".jpg", ".jpeg", ".png", ".gif", ".webp",
    ".txt", ".csv", ".rtf",
    ".zip", ".7z",
}

_GRADE_ORDER = {
    MasonicGrade.APPRENTI:  1,
    MasonicGrade.COMPAGNON: 2,
    MasonicGrade.MAITRE:    3,
}
_MIN_GRADE_ORDER = {
    MinGrade.ALL:       0,
    MinGrade.APPRENTI:  1,
    MinGrade.COMPAGNON: 2,
    MinGrade.MAITRE:    3,
}


async def _can_access(
    member: Member,
    user,
    min_grade: MinGrade,
    group_id: Optional[int],
    db: AsyncSession,
) -> bool:
    """
    Règle d'accès :
    - Admin → toujours True
    - Si group_id défini → le membre doit appartenir à ce groupe
    - Sinon → vérification par grade minimum
    """
    if user.is_admin:
        return True

    if group_id:
        group = await db.get(LodgeGroup, group_id)
        if not group:
            return False
        member_ids = await resolve_group_member_ids(db, group)
        return member.id in member_ids

    member_lvl = _GRADE_ORDER.get(member.masonic_grade, 0)
    required   = _MIN_GRADE_ORDER.get(min_grade, 0)
    return member_lvl >= required


async def _load_groups(db: AsyncSession) -> list[LodgeGroup]:
    """Charge tous les groupes pour les sélecteurs."""
    r = await db.execute(select(LodgeGroup).order_by(LodgeGroup.name))
    return r.scalars().all()


# ── Page racine — liste des espaces ────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def documents_home(
    request: Request,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    spaces_r = await db.execute(
        select(DocSpace).order_by(DocSpace.order_position, DocSpace.name)
    )
    all_spaces = spaces_r.scalars().all()

    # Filtrer selon droits (grade ou groupe)
    spaces = []
    for s in all_spaces:
        if await _can_access(member, user, s.min_grade, s.group_id, db):
            spaces.append(s)

    # Charger les groupes associés aux espaces visibles (pour affichage badge)
    group_ids = {s.group_id for s in spaces if s.group_id}
    groups_map: dict[int, LodgeGroup] = {}
    if group_ids:
        gr = await db.execute(select(LodgeGroup).where(LodgeGroup.id.in_(group_ids)))
        groups_map = {g.id: g for g in gr.scalars().all()}

    all_groups = await _load_groups(db) if user.is_admin else []

    return templates.TemplateResponse(request, "pages/documents/index.html", {
        "current_member": member,
        "current_user": user,
        "spaces": spaces,
        "groups_map": groups_map,
        "all_groups": all_groups,
        "is_admin": user.is_admin,
        "saved": request.query_params.get("saved"),
    })


# ── Espace ──────────────────────────────────────────────────────────────────

@router.get("/space/{space_id}", response_class=HTMLResponse)
async def documents_space(
    request: Request,
    space_id: int,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    space = await db.get(DocSpace, space_id)
    if not space or not await _can_access(member, user, space.min_grade, space.group_id, db):
        raise HTTPException(status_code=404)

    folders_r = await db.execute(
        select(DocFolder)
        .where(DocFolder.space_id == space_id, DocFolder.parent_id == None)
        .order_by(DocFolder.order_position, DocFolder.name)
    )
    folders = []
    for f in folders_r.scalars().all():
        if await _can_access(member, user, f.min_grade, f.group_id, db):
            folders.append(f)

    # Groupes pour badges et modal admin
    group_ids = {f.group_id for f in folders if f.group_id}
    if space.group_id:
        group_ids.add(space.group_id)
    groups_map: dict[int, LodgeGroup] = {}
    if group_ids:
        gr = await db.execute(select(LodgeGroup).where(LodgeGroup.id.in_(group_ids)))
        groups_map = {g.id: g for g in gr.scalars().all()}

    all_groups = await _load_groups(db) if user.is_admin else []

    return templates.TemplateResponse(request, "pages/documents/space.html", {
        "current_member": member,
        "current_user": user,
        "space": space,
        "folders": folders,
        "groups_map": groups_map,
        "all_groups": all_groups,
        "min_grades": list(MinGrade),
        "is_admin": user.is_admin,
        "saved": request.query_params.get("saved"),
        "breadcrumb": [{"label": "Bibliothèque", "url": "/documents/"},
                       {"label": space.name, "url": None}],
    })


# ── Dossier ──────────────────────────────────────────────────────────────────

@router.get("/folder/{folder_id}", response_class=HTMLResponse)
async def documents_folder(
    request: Request,
    folder_id: int,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    sort: str = "name",
    dir: str = "asc",
):
    from sqlalchemy import asc as sa_asc, desc as sa_desc

    user, member = ctx

    folder = await db.get(DocFolder, folder_id)
    if not folder or not await _can_access(member, user, folder.min_grade, folder.group_id, db):
        raise HTTPException(status_code=404)

    space = await db.get(DocSpace, folder.space_id)
    if not space or not await _can_access(member, user, space.min_grade, space.group_id, db):
        raise HTTPException(status_code=404)

    # Sous-dossiers
    sub_r = await db.execute(
        select(DocFolder)
        .where(DocFolder.parent_id == folder_id)
        .order_by(DocFolder.order_position, DocFolder.name)
    )
    subfolders = []
    for f in sub_r.scalars().all():
        if await _can_access(member, user, f.min_grade, f.group_id, db):
            subfolders.append(f)

    # Tri des documents
    _sort_cols = {
        "name": Document.name,
        "date": Document.created_at,
        "size": Document.file_size,
    }
    sort_col = _sort_cols.get(sort, Document.name)
    sort_fn  = sa_desc if dir == "desc" else sa_asc

    docs_r = await db.execute(
        select(Document)
        .options(selectinload(Document.folder), selectinload(Document.versions))
        .where(
            Document.folder_id == folder_id,
            Document.status == DocStatus.PUBLISHED,
            Document.deleted_at == None,
        )
        .order_by(sort_fn(sort_col))
    )
    documents = docs_r.scalars().all()
    versions_by_doc = {doc.id: sorted(doc.versions, key=lambda v: v.version_number) for doc in documents}

    # Groupes pour badges
    group_ids = {f.group_id for f in subfolders if f.group_id}
    if folder.group_id:
        group_ids.add(folder.group_id)
    groups_map: dict[int, LodgeGroup] = {}
    if group_ids:
        gr = await db.execute(select(LodgeGroup).where(LodgeGroup.id.in_(group_ids)))
        groups_map = {g.id: g for g in gr.scalars().all()}

    all_groups = await _load_groups(db) if user.is_admin else []

    # Fil d'ariane
    breadcrumb = [{"label": "Bibliothèque", "url": "/documents/"},
                  {"label": space.name,     "url": f"/documents/space/{space.id}"}]
    ancestors = []
    cur_id = folder.parent_id
    while cur_id:
        anc = await db.get(DocFolder, cur_id)
        if not anc:
            break
        ancestors.insert(0, {"label": anc.name, "url": f"/documents/folder/{anc.id}"})
        cur_id = anc.parent_id
    breadcrumb += ancestors
    breadcrumb.append({"label": folder.name, "url": None})

    # Infos plateforme pour les liens externes
    platforms = {doc.id: _detect_platform(doc.link_url) for doc in documents if doc.link_url}

    # Arbre complet des dossiers pour la modale "Déplacer"
    all_spaces_r = await db.execute(select(DocSpace).order_by(DocSpace.order_position, DocSpace.name))
    all_folders_r = await db.execute(select(DocFolder).order_by(DocFolder.space_id, DocFolder.order_position, DocFolder.name))
    all_folders_flat = [
        {"id": f.id, "name": f.name, "parent_id": f.parent_id, "space_id": f.space_id}
        for f in all_folders_r.scalars().all()
    ]

    return templates.TemplateResponse(request, "pages/documents/folder.html", {
        "current_member": member,
        "current_user": user,
        "space": space,
        "folder": folder,
        "subfolders": subfolders,
        "documents": documents,
        "platforms": platforms,
        "groups_map": groups_map,
        "all_groups": all_groups,
        "breadcrumb": breadcrumb,
        "is_admin": user.is_admin,
        "saved": request.query_params.get("saved"),
        "error": request.query_params.get("error"),
        "sort": sort,
        "dir": dir,
        "all_spaces": all_spaces_r.scalars().all(),
        "all_folders_flat": all_folders_flat,
        "current_folder_id": folder_id,
        "versions_by_doc": versions_by_doc,
    })


# ── Upload ────────────────────────────────────────────────────────────────────

@router.post("/folder/{folder_id}/upload")
async def documents_upload(
    request: Request,
    folder_id: int,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    files: List[UploadFile] = File(...),
    doc_name: str = Form(""),
):
    user, member = ctx

    folder = await db.get(DocFolder, folder_id)
    if not folder or not await _can_access(member, user, folder.min_grade, folder.group_id, db):
        raise HTTPException(status_code=403)

    errors = []
    for f in files:
        if not f.filename:
            continue
        ext = Path(f.filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            errors.append(f"{f.filename} : extension non autorisée")
            continue
        content = await f.read()
        if len(content) > MAX_FILE_SIZE:
            errors.append(f"{f.filename} : fichier trop volumineux (max 50 Mo)")
            continue

        stored_name = f"{uuid.uuid4().hex}{ext}"
        dest = UPLOAD_DIR / stored_name
        dest.write_bytes(content)

        display_name = doc_name.strip() if (doc_name.strip() and len(files) == 1) else f.filename

        doc = Document(
            folder_id=folder_id,
            name=display_name,
            original_filename=f.filename,
            mime_type=f.content_type,
            file_size=len(content),
            storage_path=str(dest),
            status=DocStatus.PUBLISHED,
            author_id=member.id,
        )
        db.add(doc)

    await db.commit()

    if errors:
        from urllib.parse import quote
        err_msg = quote("; ".join(errors)[:300])
        return RedirectResponse(url=f"/documents/folder/{folder_id}?error={err_msg}", status_code=303)
    return RedirectResponse(url=f"/documents/folder/{folder_id}?saved=1", status_code=303)


# ── Détection de plateforme musicale ─────────────────────────────────────────

def _detect_platform(url: str) -> dict:
    """Identifie la plateforme depuis l'URL : label, couleur, icône SVG et URL d'embed."""
    from urllib.parse import urlparse, parse_qs, quote as urlquote
    url_lower = url.lower()
    parsed   = urlparse(url)

    if "spotify.com" in url_lower:
        embed_url = f"https://open.spotify.com/embed{parsed.path}"
        return {"name": "Spotify", "color": "#1db954", "bg": "#f0fdf4",
                "embed_url": embed_url,
                "icon": '<path d="M12 2C6.477 2 2 6.477 2 12s4.477 10 10 10 10-4.477 10-10S17.523 2 12 2zm4.586 14.424a.622.622 0 01-.857.207c-2.348-1.435-5.304-1.76-8.785-.964a.622.622 0 01-.277-1.215c3.809-.87 7.076-.496 9.712 1.115a.622.622 0 01.207.857zm1.223-2.722a.779.779 0 01-1.072.257c-2.687-1.652-6.786-2.131-9.965-1.166a.779.779 0 01-.457-1.489c3.633-1.118 8.147-.576 11.238 1.327a.779.779 0 01.256 1.071zm.105-2.835C14.692 8.95 9.375 8.775 6.297 9.71a.935.935 0 11-.543-1.79c3.532-1.072 9.404-.865 13.115 1.338a.935.935 0 01-.954 1.609z"/>'}

    if "music.apple.com" in url_lower or "itunes.apple.com" in url_lower:
        embed_url = url.replace("music.apple.com", "embed.music.apple.com") \
                       .replace("itunes.apple.com", "embed.music.apple.com")
        return {"name": "Apple Music", "color": "#fc3c44", "bg": "#fff1f2",
                "embed_url": embed_url,
                "icon": '<path d="M23 7.286V16.5c0 2.485-2.015 4.5-4.5 4.5S14 18.985 14 16.5s2.015-4.5 4.5-4.5c.537 0 1.053.094 1.5.267V9.686l-9 2.25V19.5c0 2.485-2.015 4.5-4.5 4.5S2 21.985 2 19.5 4.015 15 6.5 15c.537 0 1.053.094 1.5.267V7.5L23 4.286v3z"/>'}

    if "deezer.com" in url_lower:
        parts = [p for p in parsed.path.strip("/").split("/") if p]
        embed_url = None
        for i, p in enumerate(parts):
            if p in ("playlist", "album", "track", "artist") and i + 1 < len(parts):
                embed_url = f"https://widget.deezer.com/widget/dark/{p}/{parts[i + 1]}"
                break
        return {"name": "Deezer", "color": "#a238ff", "bg": "#faf5ff",
                "embed_url": embed_url,
                "icon": '<path d="M18.944 20.79v1.29H24v-1.29h-5.056zM12.012 20.79v1.29h5.057v-1.29h-5.057zM5.057 20.79v1.29h5.055v-1.29H5.057zM0 20.79v1.29h4.334v-1.29H0zm18.944-2.79v1.29H24V18h-5.056zm-6.932 0v1.29h5.057V18h-5.057zm-6.955 0v1.29h5.055V18H5.057zm13.887-2.79v1.29H24v-1.29h-5.056zm-6.932 0v1.29h5.057v-1.29h-5.057zM18.944 12.42v1.29H24v-1.29h-5.056zm-6.932 0v1.29h5.057v-1.29h-5.057zM18.944 9.63V10.92H24V9.63h-5.056zm-6.932 0V10.92h5.057V9.63h-5.057zM18.944 6.84V8.13H24V6.84h-5.056zM18.944 4.05V5.34H24V4.05h-5.056zM18.944 1.26V2.55H24V1.26h-5.056z"/>'}

    if "music.youtube.com" in url_lower or ("youtube.com" in url_lower and ("watch" in url_lower or "playlist" in url_lower)):
        qs = parse_qs(parsed.query)
        embed_url = None
        if "v" in qs:
            embed_url = f"https://www.youtube.com/embed/{qs['v'][0]}"
        elif "list" in qs:
            embed_url = f"https://www.youtube.com/embed/videoseries?list={qs['list'][0]}"
        return {"name": "YouTube Music", "color": "#ff0000", "bg": "#fff1f2",
                "embed_url": embed_url,
                "icon": '<path d="M21.582 6.186a2.506 2.506 0 00-1.768-1.768C18.254 4 12 4 12 4s-6.254 0-7.814.418c-.86.23-1.538.908-1.768 1.768C2 7.746 2 12 2 12s0 4.254.418 5.814c.23.86.908 1.538 1.768 1.768C5.746 20 12 20 12 20s6.254 0 7.814-.418a2.506 2.506 0 001.768-1.768C22 16.254 22 12 22 12s0-4.254-.418-5.814zM10 15.464V8.536L15.818 12 10 15.464z"/>'}

    if "soundcloud.com" in url_lower:
        encoded   = urlquote(url, safe="")
        embed_url = (
            f"https://w.soundcloud.com/player/?url={encoded}"
            "&color=%23ff5500&auto_play=false&hide_related=false"
            "&show_comments=false&show_user=true&show_reposts=false&visual=true"
        )
        return {"name": "SoundCloud", "color": "#ff5500", "bg": "#fff7ed",
                "embed_url": embed_url,
                "icon": '<path d="M1.175 12.225c-.066 0-.12.044-.13.11l-.245 2.154.245 2.105c.01.067.064.11.13.11.065 0 .12-.043.13-.11l.278-2.105-.278-2.154c-.01-.066-.065-.11-.13-.11zm.97-.403c-.08 0-.145.056-.155.132l-.215 2.557.215 2.476c.01.076.075.132.155.132.08 0 .146-.056.157-.132l.24-2.476-.24-2.557c-.011-.076-.076-.132-.157-.132zm.99-.243c-.095 0-.172.067-.182.16l-.185 2.8.185 2.685c.01.093.087.16.182.16.094 0 .172-.067.183-.16l.21-2.685-.21-2.8c-.011-.093-.089-.16-.183-.16zm1-.105c-.11 0-.2.08-.21.185l-.157 2.905.157 2.81c.01.105.1.186.21.186.11 0 .2-.08.21-.185l.18-2.81-.18-2.905c-.01-.105-.1-.186-.21-.186zm.99.04c-.12 0-.22.09-.23.208l-.13 2.865.13 2.77c.01.118.11.21.23.21.12 0 .22-.092.23-.21l.145-2.77-.145-2.865c-.01-.118-.11-.21-.23-.21zm1.01-.19c-.136 0-.247.102-.258.234l-.103 2.824.103 2.726c.011.133.122.234.258.234.136 0 .246-.1.258-.234l.117-2.726-.117-2.824c-.012-.132-.122-.234-.258-.234zm1.01-.07c-.15 0-.272.113-.28.26l-.078 2.762.078 2.64c.008.147.13.26.28.26.15 0 .27-.113.28-.26l.088-2.64-.088-2.762c-.01-.147-.13-.26-.28-.26zm1.01.04c-.164 0-.297.124-.308.285l-.05 2.682.05 2.55c.01.16.144.285.308.285.163 0 .296-.124.308-.285l.056-2.55-.056-2.682c-.012-.16-.145-.285-.308-.285zm3.96-2.22c-.08-.027-.163-.04-.247-.04-.415 0-.777.234-.964.578C11.67 9.28 11.58 9.7 11.58 10.14v5.77c0 .177.146.32.324.322h3.674c.476 0 .862-.386.862-.862V11.94c0-.476-.386-.862-.862-.862-.144 0-.28.036-.4.1-.093-.67-.668-1.186-1.36-1.186-.232 0-.45.062-.64.17z"/>'}

    if "tidal.com" in url_lower:
        return {"name": "Tidal", "color": "#000000", "bg": "#f9fafb",
                "embed_url": None,
                "icon": '<path d="M12.012 3.992L8.008 7.996 4.004 3.992 0 7.996l4.004 4.004 4.004-4.004 4.004 4.004 4.004-4.004zM8.008 12l-4.004 4.004 4.004 4.004 4.004-4.004z"/>'}

    # Lien générique
    return {"name": "Lien", "color": "#6366f1", "bg": "#f0f0ff",
            "embed_url": None,
            "icon": '<path stroke-linecap="round" stroke-linejoin="round" d="M13.19 8.688a4.5 4.5 0 011.242 7.244l-4.5 4.5a4.5 4.5 0 01-6.364-6.364l1.757-1.757m13.35-.622l1.757-1.757a4.5 4.5 0 00-6.364-6.364l-4.5 4.5a4.5 4.5 0 001.242 7.244"/>'}


# ── Ajout de lien externe dans un dossier ─────────────────────────────────────

@router.post("/folder/{folder_id}/add-link")
async def documents_add_link(
    request: Request,
    folder_id: int,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    name: str = Form(...),
    url: str = Form(...),
    description: str = Form(""),
):
    user, member = ctx

    folder = await db.get(DocFolder, folder_id)
    if not folder or not await _can_access(member, user, folder.min_grade, folder.group_id, db):
        raise HTTPException(status_code=403)

    link_url = url.strip()
    if not link_url.startswith(("http://", "https://")):
        link_url = "https://" + link_url

    doc = Document(
        folder_id=folder_id,
        name=name.strip() or link_url,
        description=description.strip() or None,
        original_filename="",
        mime_type="text/uri-list",
        link_url=link_url,
        storage_path=None,
        status=DocStatus.PUBLISHED,
        author_id=member.id,
    )
    db.add(doc)
    await db.commit()

    return RedirectResponse(url=f"/documents/folder/{folder_id}?saved=1", status_code=303)


# ── Helpers communs download/preview ─────────────────────────────────────────

async def _get_authorized_doc(doc_id: int, user, member, db: AsyncSession):
    """Récupère un document publié et vérifie les droits d'accès."""
    doc = await db.get(Document, doc_id, options=[selectinload(Document.folder)])
    if not doc or doc.status != DocStatus.PUBLISHED:
        raise HTTPException(status_code=404)
    folder = doc.folder
    space  = await db.get(DocSpace, folder.space_id)
    if (not await _can_access(member, user, folder.min_grade, folder.group_id, db)
            or not await _can_access(member, user, space.min_grade, space.group_id, db)):
        raise HTTPException(status_code=403)
    return doc


# ── Téléchargement (Content-Disposition: attachment) ─────────────────────────

@router.get("/file/{doc_id}/download")
async def documents_download(
    doc_id: int,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    doc = await _get_authorized_doc(doc_id, user, member, db)

    if doc.link_url:
        doc.download_count = (doc.download_count or 0) + 1
        await db.commit()
        return RedirectResponse(url=doc.link_url, status_code=302)

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


# ── Aperçu inline (Content-Disposition: inline — pour PDF et images) ─────────

@router.get("/file/{doc_id}/preview")
async def documents_preview(
    doc_id: int,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from fastapi.responses import Response as _Response
    user, member = ctx
    doc = await _get_authorized_doc(doc_id, user, member, db)

    if not doc.storage_path:
        raise HTTPException(status_code=400, detail="Pas de fichier à prévisualiser")

    path = Path(doc.storage_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Fichier introuvable sur le serveur")

    mime = doc.mime_type or "application/octet-stream"
    content = path.read_bytes()

    return _Response(
        content=content,
        media_type=mime,
        headers={
            "Content-Disposition": f"inline; filename=\"{doc.original_filename or 'fichier'}\"",
            "Cache-Control": "private, max-age=3600",
        },
    )


# ── Page de visualisation/détail d'un document ───────────────────────────────

@router.get("/file/{doc_id}/view", response_class=HTMLResponse)
async def document_view(
    doc_id: int,
    request: Request,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    doc = await _get_authorized_doc(doc_id, user, member, db)
    folder = doc.folder
    space = await db.get(DocSpace, folder.space_id)
    is_previewable = doc.storage_path and doc.mime_type and (
        doc.mime_type.startswith("image/") or doc.mime_type == "application/pdf"
    )
    return templates.TemplateResponse(request, "pages/documents/file_view.html", {
        "current_member": member,
        "current_user": user,
        "doc": doc,
        "folder": folder,
        "space": space,
        "is_admin": user.is_admin,
        "is_previewable": is_previewable,
    })


# ── Admin — créer espace ──────────────────────────────────────────────────────

@router.post("/admin/space")
async def admin_create_space(
    request: Request,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    name: str = Form(...),
    description: str = Form(""),
    min_grade: str = Form("ALL"),
    group_id: str = Form(""),
    order_position: int = Form(0),
):
    user, member = ctx
    space = DocSpace(
        name=name.strip(),
        description=description.strip() or None,
        min_grade=MinGrade(min_grade),
        group_id=int(group_id) if group_id.strip().isdigit() else None,
        order_position=order_position,
        created_by_id=member.id,
    )
    db.add(space)
    await db.commit()
    return RedirectResponse(url="/documents/?saved=1", status_code=303)


# ── Admin — éditer espace ────────────────────────────────────────────────────

@router.post("/admin/space/{space_id}/edit")
async def admin_edit_space(
    space_id: int,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    name: str = Form(...),
    description: str = Form(""),
    min_grade: str = Form("ALL"),
    group_id: str = Form(""),
    order_position: int = Form(0),
):
    space = await db.get(DocSpace, space_id)
    if not space:
        raise HTTPException(status_code=404)
    space.name = name.strip()
    space.description = description.strip() or None
    space.min_grade = MinGrade(min_grade)
    space.group_id = int(group_id) if group_id.strip().isdigit() else None
    space.order_position = order_position
    await db.commit()
    return RedirectResponse(url=f"/documents/space/{space_id}?saved=1", status_code=303)


# ── Admin — créer dossier ─────────────────────────────────────────────────────

@router.post("/admin/folder")
async def admin_create_folder(
    request: Request,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    name: str = Form(...),
    space_id: int = Form(...),
    parent_id: Optional[int] = Form(None),
    min_grade: str = Form("ALL"),
    group_id: str = Form(""),
    order_position: int = Form(0),
):
    user, member = ctx
    folder = DocFolder(
        name=name.strip(),
        space_id=space_id,
        parent_id=parent_id or None,
        min_grade=MinGrade(min_grade),
        group_id=int(group_id) if group_id.strip().isdigit() else None,
        order_position=order_position,
        created_by_id=member.id,
    )
    db.add(folder)
    await db.commit()
    if parent_id:
        return RedirectResponse(url=f"/documents/folder/{parent_id}?saved=1", status_code=303)
    return RedirectResponse(url=f"/documents/space/{space_id}?saved=1", status_code=303)


# ── Admin — supprimer document ────────────────────────────────────────────────

@router.post("/admin/file/{doc_id}/delete")
async def admin_delete_file(
    doc_id: int,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    doc = await db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404)
    folder_id = doc.folder_id
    try:
        Path(doc.storage_path).unlink(missing_ok=True)
    except Exception:
        pass
    await db.delete(doc)
    await db.commit()
    return RedirectResponse(url=f"/documents/folder/{folder_id}?saved=1", status_code=303)


# ── Admin — supprimer dossier ─────────────────────────────────────────────────

@router.post("/admin/folder/{folder_id}/delete")
async def admin_delete_folder(
    folder_id: int,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    folder = await db.get(DocFolder, folder_id)
    if not folder:
        raise HTTPException(status_code=404)
    parent_id = folder.parent_id
    space_id = folder.space_id
    await db.delete(folder)
    await db.commit()
    if parent_id:
        return RedirectResponse(url=f"/documents/folder/{parent_id}?saved=1", status_code=303)
    return RedirectResponse(url=f"/documents/space/{space_id}?saved=1", status_code=303)


# ── Admin — supprimer espace ──────────────────────────────────────────────────

@router.post("/admin/space/{space_id}/delete")
async def admin_delete_space(
    space_id: int,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    space = await db.get(DocSpace, space_id)
    if not space:
        raise HTTPException(status_code=404)
    await db.delete(space)
    await db.commit()
    return RedirectResponse(url="/documents/?saved=1", status_code=303)


# ── Éditer dossier ───────────────────────────────────────────────────────────

@router.get("/folder/{folder_id}/edit", response_class=HTMLResponse)
async def folder_edit_form(
    request: Request,
    folder_id: int,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    folder = await db.get(DocFolder, folder_id)
    if not folder:
        raise HTTPException(status_code=404)
    groups_r = await db.execute(select(LodgeGroup).order_by(LodgeGroup.name))
    groups = groups_r.scalars().all()
    all_spaces_r = await db.execute(select(DocSpace).order_by(DocSpace.order_position, DocSpace.name))
    all_folders_r = await db.execute(select(DocFolder).order_by(DocFolder.space_id, DocFolder.order_position, DocFolder.name))
    all_folders_flat = [
        {"id": f.id, "name": f.name, "parent_id": f.parent_id, "space_id": f.space_id}
        for f in all_folders_r.scalars().all()
    ]
    return templates.TemplateResponse(request, "pages/documents/folder_edit.html", {
        "current_member": member,
        "folder": folder,
        "groups": groups,
        "min_grades": list(MinGrade),
        "saved": request.query_params.get("saved"),
        "all_spaces": all_spaces_r.scalars().all(),
        "all_folders_flat": all_folders_flat,
    })


@router.post("/folder/{folder_id}/edit")
async def folder_edit_save(
    folder_id: int,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    name: str = Form(...),
    description: str = Form(""),
    min_grade: str = Form("ALL"),
    group_id: str = Form(""),
    order_position: int = Form(0),
    move_to: Optional[str] = Form(None),
):
    folder = await db.get(DocFolder, folder_id)
    if not folder:
        raise HTTPException(status_code=404)
    folder.name = name.strip()
    folder.description = description.strip() or None
    folder.min_grade = MinGrade(min_grade)
    folder.group_id = int(group_id) if group_id.strip().isdigit() else None
    folder.order_position = order_position
    # Déplacement optionnel via le sélecteur "move_to" (format "folder:42" ou "space:3")
    if move_to and ":" in move_to:
        target_type, target_id_str = move_to.split(":", 1)
        if target_id_str.isdigit():
            target_id = int(target_id_str)
            if target_type == "folder":
                target = await db.get(DocFolder, target_id)
                if target:
                    folder.parent_id = target.id
                    folder.space_id = target.space_id
            elif target_type == "space":
                space = await db.get(DocSpace, target_id)
                if space:
                    folder.parent_id = None
                    folder.space_id = target_id
    await db.commit()
    back = f"/documents/folder/{folder.parent_id}" if folder.parent_id else f"/documents/space/{folder.space_id}"
    return RedirectResponse(url=f"{back}?saved=1", status_code=303)


# ── Déplacer dossier ─────────────────────────────────────────────────────────

@router.post("/folder/{folder_id}/move")
async def folder_move(
    folder_id: int,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    target_type: str = Form(...),       # "folder" or "space"
    target_id: int = Form(...),
):
    folder = await db.get(DocFolder, folder_id)
    if not folder:
        raise HTTPException(status_code=404)
    old_parent_id = folder.parent_id
    old_space_id  = folder.space_id
    if target_type == "folder":
        target = await db.get(DocFolder, target_id)
        if not target:
            raise HTTPException(status_code=404)
        folder.parent_id = target.id
        folder.space_id  = target.space_id
    else:  # space root
        space = await db.get(DocSpace, target_id)
        if not space:
            raise HTTPException(status_code=404)
        folder.parent_id = None
        folder.space_id  = target_id
    await db.commit()
    if old_parent_id:
        return RedirectResponse(url=f"/documents/folder/{old_parent_id}?saved=1", status_code=303)
    return RedirectResponse(url=f"/documents/space/{old_space_id}?saved=1", status_code=303)


# ── Corbeille ─────────────────────────────────────────────────────────────────

@router.get("/trash", response_class=HTMLResponse)
async def documents_trash(
    request: Request,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx
    docs_r = await db.execute(
        select(Document)
        .options(selectinload(Document.folder))
        .where(Document.deleted_at != None)
        .order_by(Document.deleted_at.desc())
    )
    trashed = docs_r.scalars().all()
    return templates.TemplateResponse(request, "pages/documents/trash.html", {
        "current_member": member,
        "current_user": user,
        "documents": trashed,
        "is_admin": True,
    })


@router.post("/file/{doc_id}/trash")
async def document_trash(
    doc_id: int,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from datetime import datetime
    doc = await db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404)
    folder_id = doc.folder_id
    doc.deleted_at = datetime.now()
    await db.commit()
    return RedirectResponse(url=f"/documents/folder/{folder_id}?saved=1", status_code=303)


@router.post("/file/{doc_id}/restore")
async def document_restore(
    doc_id: int,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    doc = await db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404)
    doc.deleted_at = None
    await db.commit()
    return RedirectResponse(url="/documents/trash?saved=1", status_code=303)


@router.post("/file/{doc_id}/destroy")
async def document_destroy(
    doc_id: int,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    doc = await db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404)
    if doc.storage_path:
        try:
            Path(doc.storage_path).unlink(missing_ok=True)
        except Exception:
            pass
    await db.delete(doc)
    await db.commit()
    return RedirectResponse(url="/documents/trash?saved=1", status_code=303)


@router.post("/file/{doc_id}/move")
async def document_move(
    doc_id: int,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    target_folder_id: Annotated[int, Form()],
):
    doc = await db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404)
    target = await db.get(DocFolder, target_folder_id)
    if not target:
        raise HTTPException(status_code=404, detail="Dossier cible introuvable")
    old_folder_id = doc.folder_id
    doc.folder_id = target_folder_id
    await db.commit()
    return RedirectResponse(url=f"/documents/folder/{old_folder_id}?saved=1", status_code=303)


# ── Feature 1 — Renommage de fichier ─────────────────────────────────────────

@router.post("/file/{doc_id}/rename")
async def document_rename(
    doc_id: int,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    name: str = Form(...),
):
    doc = await db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404)
    doc.name = name.strip()
    await db.commit()
    return RedirectResponse(url=f"/documents/folder/{doc.folder_id}?saved=1", status_code=303)


# ── Feature 2 — Recherche ─────────────────────────────────────────────────────

@router.get("/api/tree")
async def documents_tree(
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Arborescence accessible : espaces → dossiers → sous-dossiers (sans docs)."""
    user, member = ctx
    spaces_r = await db.execute(
        select(DocSpace).order_by(DocSpace.order_position, DocSpace.name)
    )
    result = []
    for space in spaces_r.scalars().all():
        if not await _can_access(member, user, space.min_grade, space.group_id, db):
            continue
        folders_r = await db.execute(
            select(DocFolder)
            .where(DocFolder.space_id == space.id, DocFolder.parent_id == None)
            .order_by(DocFolder.order_position, DocFolder.name)
        )
        space_data = {"id": space.id, "name": space.name, "folders": []}
        for folder in folders_r.scalars().all():
            if not await _can_access(member, user, folder.min_grade, folder.group_id, db):
                continue
            sf_r = await db.execute(
                select(DocFolder)
                .where(DocFolder.parent_id == folder.id)
                .order_by(DocFolder.order_position, DocFolder.name)
            )
            subfolders = []
            for sf in sf_r.scalars().all():
                if not await _can_access(member, user, sf.min_grade, sf.group_id, db):
                    continue
                subfolders.append({"id": sf.id, "name": sf.name})
            space_data["folders"].append({
                "id": folder.id,
                "name": folder.name,
                "subfolders": subfolders,
            })
        result.append(space_data)
    return result


@router.get("/api/picker")
async def documents_picker(
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    q: str = "",
    folder_id: int = 0,
):
    """Retourne les documents accessibles (JSON) pour le sélecteur de bibliothèque."""
    user, member = ctx
    pattern = f"%{q.strip()}%" if q.strip() else "%"
    docs_r = await db.execute(
        select(Document)
        .options(selectinload(Document.folder))
        .where(
            Document.deleted_at == None,
            Document.status == DocStatus.PUBLISHED,
            or_(Document.name.ilike(pattern), Document.description.ilike(pattern)),
            *([Document.folder_id == folder_id] if folder_id else []),
        )
        .order_by(Document.name)
        .limit(50)
    )
    results = []
    for doc in docs_r.scalars().all():
        folder = doc.folder
        if not folder:
            continue
        space = await db.get(DocSpace, folder.space_id)
        if not space:
            continue
        if (await _can_access(member, user, folder.min_grade, folder.group_id, db)
                and await _can_access(member, user, space.min_grade, space.group_id, db)):
            results.append({
                "id": doc.id,
                "name": doc.name,
                "folder": folder.name,
                "space": space.name,
                "mime_type": doc.mime_type or "",
                "download_url": f"/documents/file/{doc.id}/download",
                "view_url": f"/documents/file/{doc.id}/view",
            })
    return results


@router.get("/search", response_class=HTMLResponse)
async def documents_search(
    request: Request,
    ctx: Annotated[object, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    q: str = "",
):
    user, member = ctx

    results = []
    if q.strip():
        pattern = f"%{q.strip()}%"
        docs_r = await db.execute(
            select(Document)
            .options(selectinload(Document.folder))
            .where(
                Document.deleted_at == None,
                Document.status == DocStatus.PUBLISHED,
                or_(Document.name.ilike(pattern), Document.description.ilike(pattern)),
            )
        )
        all_docs = docs_r.scalars().all()

        for doc in all_docs:
            folder = doc.folder
            if not folder:
                continue
            space = await db.get(DocSpace, folder.space_id)
            if not space:
                continue
            if (await _can_access(member, user, folder.min_grade, folder.group_id, db)
                    and await _can_access(member, user, space.min_grade, space.group_id, db)):
                results.append(doc)

    return templates.TemplateResponse(request, "pages/documents/search.html", {
        "current_member": member,
        "current_user": user,
        "documents": results,
        "q": q,
        "is_admin": user.is_admin,
    })


# ── Feature 3 — Versioning ────────────────────────────────────────────────────

@router.post("/file/{doc_id}/new-version")
async def document_new_version(
    doc_id: int,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...),
    change_notes: str = Form(""),
):
    user, member = ctx

    doc = await db.get(Document, doc_id, options=[selectinload(Document.versions)])
    if not doc:
        raise HTTPException(status_code=404)

    ext = Path(file.filename).suffix.lower() if file.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Extension non autorisée")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="Fichier trop volumineux (max 50 Mo)")

    # Archiver la version courante
    version_number = len(doc.versions) + 1
    version = DocumentVersion(
        document_id=doc.id,
        version_number=version_number,
        storage_path=doc.storage_path or "",
        file_size=doc.file_size,
        change_notes=change_notes.strip() or None,
        created_by_id=member.id,
    )
    db.add(version)

    # Sauvegarder le nouveau fichier
    stored_name = f"{uuid.uuid4().hex}{ext}"
    dest = UPLOAD_DIR / stored_name
    dest.write_bytes(content)

    # Mettre à jour le document
    doc.storage_path = str(dest)
    doc.file_size = len(content)
    doc.original_filename = file.filename
    doc.mime_type = file.content_type
    doc.updated_at = datetime.now()

    await db.commit()
    return RedirectResponse(url=f"/documents/folder/{doc.folder_id}?saved=1", status_code=303)


@router.post("/file/{doc_id}/version/{version_id}/restore")
async def document_version_restore(
    doc_id: int,
    version_id: int,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, member = ctx

    doc = await db.get(Document, doc_id, options=[selectinload(Document.versions)])
    if not doc:
        raise HTTPException(status_code=404)

    target_version = await db.get(DocumentVersion, version_id)
    if not target_version or target_version.document_id != doc_id:
        raise HTTPException(status_code=404)

    # Archiver la version courante avant restauration
    current_version_number = len(doc.versions) + 1
    current_snap = DocumentVersion(
        document_id=doc.id,
        version_number=current_version_number,
        storage_path=doc.storage_path or "",
        file_size=doc.file_size,
        change_notes=f"Snapshot avant restauration de la v{target_version.version_number}",
        created_by_id=member.id,
    )
    db.add(current_snap)

    # Restaurer les champs depuis la version cible
    doc.storage_path = target_version.storage_path
    doc.file_size = target_version.file_size
    doc.updated_at = datetime.now()

    await db.commit()
    return RedirectResponse(url=f"/documents/folder/{doc.folder_id}?saved=1", status_code=303)


@router.post("/bulk")
async def documents_bulk(
    request: Request,
    ctx: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from datetime import datetime
    user, member = ctx
    form = await request.form()
    action = form.get("action", "")
    doc_ids = [int(x) for x in form.getlist("doc_ids") if str(x).isdigit()]
    target_folder_id = form.get("target_folder_id", "")
    back_folder = form.get("back_folder_id", "")

    if not doc_ids:
        return RedirectResponse(url=f"/documents/folder/{back_folder}?error=nosel", status_code=303)

    docs_r = await db.execute(select(Document).where(Document.id.in_(doc_ids)))
    docs = docs_r.scalars().all()

    if action == "trash":
        now = datetime.now()
        for doc in docs:
            doc.deleted_at = now
        await db.commit()

    elif action == "restore":
        for doc in docs:
            doc.deleted_at = None
        await db.commit()
        return RedirectResponse(url="/documents/trash?saved=1", status_code=303)

    elif action == "destroy":
        for doc in docs:
            if doc.storage_path:
                try:
                    Path(doc.storage_path).unlink(missing_ok=True)
                except Exception:
                    pass
            await db.delete(doc)
        await db.commit()
        return RedirectResponse(url="/documents/trash?saved=1", status_code=303)

    elif action == "move" and str(target_folder_id).isdigit():
        tid = int(target_folder_id)
        target = await db.get(DocFolder, tid)
        if not target:
            return RedirectResponse(url=f"/documents/folder/{back_folder}?error=notarget", status_code=303)
        for doc in docs:
            doc.folder_id = tid
        await db.commit()
        return RedirectResponse(url=f"/documents/folder/{tid}?saved=1", status_code=303)

    redirect = f"/documents/folder/{back_folder}" if back_folder else "/documents/trash"
    return RedirectResponse(url=f"{redirect}?saved=1", status_code=303)
