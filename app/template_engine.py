"""Instance Jinja2 partagée avec tous les filtres enregistrés.
Tous les routers importent : from app.template_engine import templates
"""
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")

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


# ── Filtre anonymisation noms de famille ──────────────────────────────────────
# Règle : consonnes seulement (si < 2 consonnes → initiale + …)
# Visible en clair uniquement pour : admin, VM, Secrétaire, Trésorier

_VOYELLES = set("AEIOUÀÂÄÆÈÉÊËÎÏŒÔÖÙÛÜ")
_ROLES_FULL = {"VM", "SECRETAIRE", "TRESORIER"}


def _anon_nom_fn(name: str, can_see: bool) -> str:
    """Retourne le nom complet ou sa version anonymisée (consonnes)."""
    if can_see or not name:
        return name
    upper = name.upper()
    consonnes = [c for c in upper if c.isalpha() and c not in _VOYELLES]
    if len(consonnes) < 2:
        return (upper[0] + "…") if upper else "…"
    return "".join(consonnes)


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
