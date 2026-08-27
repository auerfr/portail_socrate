"""Conversion des horodatages UTC (stockage) vers l'heure de Paris (affichage).

Les colonnes created_at/sent_at/read_at/updated_at sont stockées en UTC
(CURRENT_TIMESTAMP SQLite / datetime.utcnow()) mais doivent être affichées
en heure de Paris — que ce soit côté template Jinja (voir localdt dans
template_engine.py) ou côté route quand du texte est formaté en Python
avant d'être renvoyé en JSON ou inséré dans un PDF."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

PARIS_TZ = ZoneInfo("Europe/Paris")


def to_paris(value: datetime) -> datetime:
    """Convertit un datetime (naïf = UTC, ou déjà aware) vers Europe/Paris."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(PARIS_TZ)
