"""Lecture/écriture des paramètres système clé/valeur.

Cache en mémoire (TTL court) pour éviter de toucher la DB à chaque vue.
"""
import time
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.system import SystemSetting

_CACHE: dict[str, tuple[float, Any]] = {}
_TTL = 30.0  # secondes


async def get_setting(key: str, default: Any = None, db: Optional[AsyncSession] = None) -> Any:
    """Retourne la valeur (JSON déjà désérialisé) ou default si absent."""
    now = time.time()
    cached = _CACHE.get(key)
    if cached and cached[0] > now:
        return cached[1]

    async def _fetch(s: AsyncSession) -> Any:
        r = await s.execute(select(SystemSetting).where(SystemSetting.key == key))
        row = r.scalar_one_or_none()
        return row.value if row else None

    if db is not None:
        val = await _fetch(db)
    else:
        async with AsyncSessionLocal() as s:
            val = await _fetch(s)

    if val is None:
        val = default
    _CACHE[key] = (now + _TTL, val)
    return val


async def set_setting(
    db: AsyncSession, key: str, value: Any, actor_id: Optional[int] = None
) -> None:
    """Upsert. Invalide le cache local."""
    r = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    row = r.scalar_one_or_none()
    if row:
        row.value = value
        row.updated_by_id = actor_id
    else:
        db.add(SystemSetting(key=key, value=value, updated_by_id=actor_id))
    await db.commit()
    _CACHE.pop(key, None)


def invalidate(key: str | None = None):
    if key is None:
        _CACHE.clear()
    else:
        _CACHE.pop(key, None)
