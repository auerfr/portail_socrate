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
