"""Réglages confidentialité — tous activables/désactivables depuis /admin/confidentiality.

Chaque protection est OPT-IN : aucune n'est active par défaut, pour ne rien
casser sur les déploiements existants. L'admin choisit ce qu'il veut activer.
"""
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.settings_store import get_setting, set_setting

KEY = "confidentiality"

DEFAULTS = {
    # A. Whitelist des dossiers GED dont les documents peuvent être joints en
    # pièce jointe de mailing. Liste vide => tous autorisés (= comportement
    # actuel, pas de restriction).
    "pj_whitelist_enabled":  False,
    "pj_allowed_folder_ids": [],

    # C. Logger les consultations de documents sensibles (tracés, planches,
    # fiches membres) dans l'AuditLog. Plus de visibilité, mais le journal
    # grossit vite.
    "audit_sensitive_views": False,

    # D. Bannière "CONFIDENTIEL" sur les pages sensibles (tracé, planche,
    # fiche membre). Purement dissuasive.
    "show_confidentiality_banner": False,
}


async def get_config(db: Optional[AsyncSession] = None) -> dict:
    """Retourne le dict de config, fusionné avec les defaults."""
    stored = await get_setting(KEY, db=db) or {}
    out = dict(DEFAULTS)
    if isinstance(stored, dict):
        out.update(stored)
    # Garantie sur les types
    out["pj_allowed_folder_ids"] = [int(x) for x in (out.get("pj_allowed_folder_ids") or []) if str(x).isdigit() or isinstance(x, int)]
    return out


async def maybe_audit_view(
    db: AsyncSession,
    *,
    actor_id: Optional[int],
    resource_type: str,
    resource_id: Optional[int],
    target_label: Optional[str],
    request=None,
) -> None:
    """Loggue une consultation dans l'AuditLog si l'option est activée.
    Ne lève jamais (best-effort)."""
    try:
        cfg = await get_config(db=db)
        if not cfg.get("audit_sensitive_views"):
            return
        from app.services.audit import log_audit
        await log_audit(
            db, actor_id=actor_id,
            action="VIEW_SENSITIVE",
            target_type=resource_type,
            target_id=resource_id,
            target_label=target_label,
            request=request, commit=True,
        )
    except Exception:
        pass


async def save_config(db: AsyncSession, *,
                      pj_whitelist_enabled: bool,
                      pj_allowed_folder_ids: list[int],
                      audit_sensitive_views: bool,
                      show_confidentiality_banner: bool,
                      actor_id: Optional[int] = None) -> None:
    payload = {
        "pj_whitelist_enabled": bool(pj_whitelist_enabled),
        "pj_allowed_folder_ids": [int(x) for x in pj_allowed_folder_ids],
        "audit_sensitive_views": bool(audit_sensitive_views),
        "show_confidentiality_banner": bool(show_confidentiality_banner),
    }
    await set_setting(db, KEY, payload, actor_id=actor_id)
