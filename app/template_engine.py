"""Instance Jinja2 partagée avec tous les filtres enregistrés.
Tous les routers importent : from app.template_engine import templates
"""
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")

# ── Cache-busting des fichiers statiques (?v=mtime) ─────────────────────────
# Sans ça, un fichier CSS/JS modifié (ex. app.css recompilé après un ajout de
# classes Tailwind) reste servi depuis le cache du navigateur après un
# déploiement, tant que l'utilisateur ne force pas un rechargement complet —
# déjà rencontré sur formation.css/js (cache-busting ajouté localement à ce
# module-là) ; ici en global Jinja pour que n'importe quel template puisse
# s'en servir, à commencer par app.css chargé par toutes les pages via
# base.html.
import pathlib as _pathlib

_STATIC_DIR = _pathlib.Path(__file__).resolve().parent / "static"


def _asset_version(rel_path: str) -> int:
    try:
        return int((_STATIC_DIR / rel_path).stat().st_mtime)
    except OSError:
        return 0


templates.env.globals["asset_version"] = _asset_version

# ── Filtre dates en français ─────────────────────────────────────────────────
_MOIS = {
    "January":"janvier","February":"février","March":"mars","April":"avril",
    "May":"mai","June":"juin","July":"juillet","August":"août",
    "September":"septembre","October":"octobre","November":"novembre","December":"décembre",
    "Jan":"jan","Feb":"fév","Mar":"mars","Apr":"avr",
    "Aug":"août","Sep":"sep","Oct":"oct","Nov":"nov","Dec":"déc",
    "Jun":"juin","Jul":"juil",
}
_JOURS = {
    "Monday":"lundi","Tuesday":"mardi","Wednesday":"mercredi","Thursday":"jeudi",
    "Friday":"vendredi","Saturday":"samedi","Sunday":"dimanche",
    "Mon":"lun","Tue":"mar","Wed":"mer","Thu":"jeu","Fri":"ven","Sat":"sam","Sun":"dim",
}

def _datefr(value, fmt="%d %B %Y"):
    if value is None:
        return ""
    import datetime as _dt
    if isinstance(value, (_dt.datetime, _dt.date)):
        s = value.strftime(fmt)
        for en, fr in {**_MOIS, **_JOURS}.items():
            s = s.replace(en, fr)
        return s
    return str(value)

templates.env.filters["datefr"] = _datefr


# ── Filtre conversion UTC → heure locale (Europe/Paris) ────────────────────
from app.utils.tz import to_paris as _to_paris

def _localdt(value, fmt="%d/%m/%Y %H:%M:%S"):
    """Convertit un datetime naïf stocké en UTC (ex : created_at des logs
    d'audit, server_default=func.now() sur SQLite) vers l'heure de Paris
    avant affichage. Sans ça les horodatages « s'est produit à » affichent
    l'heure UTC brute, décalée de 1h ou 2h selon la saison."""
    if value is None:
        return ""
    import datetime as _dt
    if isinstance(value, _dt.datetime):
        return _to_paris(value).strftime(fmt)
    return str(value)

templates.env.filters["localdt"] = _localdt


# ── Filtre anonymisation noms de famille ──────────────────────────────────────
# Règle : consonnes seulement (si < 2 consonnes → initiale + …), via
# masonic_write (app/utils/text.py) — la même "écriture maçonnique" que celle
# utilisée dans les tracés.
# Visible en clair uniquement pour : admin, VM, Secrétaire, Trésorier

from app.utils.text import masonic_write as _masonic_write

_ROLES_FULL = {"VM", "SECRETAIRE", "TRESORIER"}


def _anon_nom_fn(name: str, can_see: bool) -> str:
    """Retourne le nom complet ou sa version anonymisée (consonnes)."""
    if can_see or not name:
        return name
    return _masonic_write(name)


import jinja2 as _jinja2

@_jinja2.pass_context
def _anon_nom(ctx, name: str) -> str:
    """Filtre Jinja2 : {{ member.last_name | anon_nom }}
    Affiche le nom complet pour admin/VM/Sec/Trésorier, consonnes pour les autres.
    """
    user   = ctx.get("current_user")
    member = ctx.get("current_member")

    can_see = False
    if user and getattr(user, "is_admin", False):
        can_see = True
    elif member and getattr(member, "lodge_function", None):
        fn = member.lodge_function.value if hasattr(member.lodge_function, "value") else str(member.lodge_function)
        can_see = fn in _ROLES_FULL

    return _anon_nom_fn(name or "", can_see)


templates.env.filters["anon_nom"] = _anon_nom
templates.env.filters["masonic_write"] = _masonic_write

# HTML saisi par les rédacteurs (actualités) : nettoyé à l'affichage aussi,
# pour couvrir les contenus enregistrés avant l'ajout du nettoyage.
from markupsafe import Markup as _Markup
from app.utils.html_sanitizer import sanitize_html as _sanitize_html, html_to_editable as _html_to_editable
templates.env.filters["sanitize_html"] = lambda v: _Markup(_sanitize_html(v))
templates.env.filters["html_to_editable"] = _html_to_editable
