"""Personnalisation des libellés d'enums (LabelOverride).

API :
  - `await refresh_cache()` : recharge depuis la DB
  - `get_label(enum_value)` : retourne le libellé personnalisé ou la valeur par défaut
  - filtre Jinja `| label` : utilisable dans les templates

Cache mémoire : rechargé à chaque modif via `set_label()`.
"""
import time
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.system import LabelOverride


# Cache global { "MasonicGrade.APPRENTI": "Apprenti·e", ... }
_CACHE: dict[str, str] = {}
_CACHE_LOADED_AT: float = 0.0
_TTL = 60.0


def _key(enum_class: str, enum_key: str) -> str:
    return f"{enum_class}.{enum_key}"


async def _load_all() -> None:
    """Recharge l'intégralité du cache depuis la DB."""
    global _CACHE, _CACHE_LOADED_AT
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(select(LabelOverride))).scalars().all()
        _CACHE = {_key(r.enum_class, r.enum_key): r.label for r in rows}
        _CACHE_LOADED_AT = time.time()


async def _ensure_loaded() -> None:
    if time.time() - _CACHE_LOADED_AT > _TTL or not _CACHE_LOADED_AT:
        try:
            await _load_all()
        except Exception:
            pass  # garde l'ancien cache


def get_label(value: Any, default: Optional[str] = None) -> str:
    """Retourne le libellé personnalisé d'une valeur d'enum, sinon sa valeur.

    Acceptable inputs : une instance d'enum, ou une str "Class.KEY".
    Synchrone — utilise uniquement le cache.
    """
    if value is None:
        return default or ""
    # Enum instance
    if hasattr(value, "__class__") and hasattr(value, "name"):
        cls_name = value.__class__.__name__
        key_name = value.name
        cache_key = _key(cls_name, key_name)
        if cache_key in _CACHE:
            return _CACHE[cache_key]
        return getattr(value, "value", str(value))
    # Tuple-like (class, key)
    if isinstance(value, tuple) and len(value) == 2:
        cls_name, key_name = value
        return _CACHE.get(_key(cls_name, key_name), default or str(key_name))
    # str "Class.KEY"
    if isinstance(value, str) and "." in value:
        return _CACHE.get(value, default or value.split(".", 1)[1])
    return default or str(value)


async def set_label(
    db: AsyncSession, enum_class: str, enum_key: str,
    label: Optional[str], actor_id: Optional[int] = None,
) -> None:
    """Crée/met à jour/supprime un override (label vide = suppression).
    Recharge le cache après commit."""
    r = await db.execute(
        select(LabelOverride).where(
            LabelOverride.enum_class == enum_class,
            LabelOverride.enum_key == enum_key,
        )
    )
    row = r.scalar_one_or_none()
    if label and label.strip():
        if row:
            row.label = label.strip()[:200]
            row.updated_by_id = actor_id
        else:
            db.add(LabelOverride(
                enum_class=enum_class, enum_key=enum_key,
                label=label.strip()[:200], updated_by_id=actor_id,
            ))
    else:
        if row:
            await db.delete(row)
    await db.commit()
    # Recharger en mémoire
    try:
        await _load_all()
    except Exception:
        pass


def register_jinja(env) -> None:
    """Enregistre le filtre `label` sur un Environment Jinja."""
    env.filters["label"] = get_label
