"""Présence en ligne — statut calculé à partir de Member.last_activity_at
(mis à jour par battement, cf. app/dependencies.py::get_current_user et
app/routers/presence.py). Pas de temps réel WebSocket : seuils tolérants."""
from datetime import datetime, timedelta
from typing import Optional

ONLINE_THRESHOLD = timedelta(minutes=3)
AWAY_THRESHOLD = timedelta(minutes=30)


def presence_status(member) -> str:
    """Retourne 'online', 'away' ou 'offline'."""
    last = getattr(member, "last_activity_at", None) if member else None
    if not last:
        return "offline"
    delta = datetime.utcnow() - last
    if delta <= ONLINE_THRESHOLD:
        return "online"
    if delta <= AWAY_THRESHOLD:
        return "away"
    return "offline"


def _format_ago(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "à l'instant"
    if minutes < 60:
        return f"il y a {minutes} min"
    hours = minutes // 60
    if hours < 24:
        return f"il y a {hours} h"
    days = hours // 24
    return f"il y a {days} j"


def presence_label(member) -> str:
    """Libellé pour l'infobulle du badge de présence."""
    last: Optional[datetime] = getattr(member, "last_activity_at", None) if member else None
    if not last:
        return "Jamais connecté"
    status = presence_status(member)
    if status == "online":
        return "En ligne"
    ago = _format_ago(datetime.utcnow() - last)
    if status == "away":
        return f"Inactif — vu {ago}"
    return f"Hors ligne — vu {ago}"


def register_jinja(env) -> None:
    """Enregistre les filtres `presence_status` / `presence_label`."""
    env.filters["presence_status"] = presence_status
    env.filters["presence_label"] = presence_label
