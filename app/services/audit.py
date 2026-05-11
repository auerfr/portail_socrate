"""Helper centralisé pour écrire des entrées dans AuditLog."""
from typing import Optional
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system import AuditLog


def _client_ip(request: Optional[Request]) -> Optional[str]:
    if not request:
        return None
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


async def log_audit(
    db: AsyncSession,
    *,
    actor_id: Optional[int],
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[int] = None,
    target_label: Optional[str] = None,
    details: Optional[dict | str] = None,
    request: Optional[Request] = None,
    commit: bool = False,
) -> None:
    """Ajoute une entrée d'audit. Par défaut ne commit pas — laisse l'appelant
    le faire dans sa transaction."""
    # `details` peut être dict (stocké en JSON) ou str (sera wrappé)
    if isinstance(details, str):
        details_json = {"text": details}
    else:
        details_json = details

    entry = AuditLog(
        actor_id=actor_id,
        action=action,
        resource_type=target_type,
        resource_id=target_id,
        target_label=(target_label[:300] if target_label else None),
        details=details_json,
        ip_address=_client_ip(request),
        user_agent=(request.headers.get("user-agent", "")[:300]
                    if request else None),
    )
    db.add(entry)
    if commit:
        await db.commit()
