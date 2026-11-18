"""Injection d'un pixel de suivi d'ouverture + réécriture des liens pour le
suivi de clic, dans le corps HTML d'un email — appelé automatiquement par
app.services.email._send_raw pour TOUT email journalisé dans EmailLog (pas
seulement l'email d'accès, qui était jusqu'ici le seul à en bénéficier).

Réutilise le même mécanisme de token que app.services.member_access
(make_access_track_token / /access/open/ /access/click/) : ces fonctions et
routes sont déjà génériques (elles opèrent sur un EmailLog.id arbitraire),
seul leur nom ("access") date de l'époque où un seul type d'email en
profitait.
"""
import re
from html import escape
from urllib.parse import quote

from app.services.member_access import make_access_track_token

# Schémas de lien qu'on ne réécrit jamais : mailto:/tel: n'ont pas de "clic
# suivi" qui fasse sens, et un lien déjà réécrit (mailing.py a son propre
# suivi de clic pour les campagnes de diffusion) ne doit pas l'être deux fois.
_SKIP_HREF_RE = re.compile(r"^(mailto:|tel:)", re.I)
_ALREADY_TRACKED_RE = re.compile(r"/access/click/|/mailing/track/click/", re.I)

# Cible uniquement l'attribut href d'une balise <a> — une regex ciblée plutôt
# qu'une reconstruction complète du HTML : plus sûr pour un corps d'email
# (tables, styles inline, markup riche) où une re-sérialisation risquerait de
# perdre ou déformer du balisage.
_HREF_ATTR_RE = re.compile(r'(<a\b[^>]*?\bhref\s*=\s*)(["\'])(.*?)\2', re.I | re.S)


def _rewrite_links(html: str, base_url: str, log_id: int) -> str:
    """Réécrit les href http(s) des balises <a> pour le suivi de clic."""

    def _sub(m: re.Match) -> str:
        prefix, quote_char, href = m.group(1), m.group(2), m.group(3)
        if not href.startswith(("http://", "https://")):
            return m.group(0)
        if _SKIP_HREF_RE.match(href) or _ALREADY_TRACKED_RE.search(href):
            return m.group(0)
        token = make_access_track_token(log_id, "c")
        new_href = f"{base_url}/access/click/{token}?url={quote(href, safe='')}"
        return f"{prefix}{quote_char}{escape(new_href, quote=True)}{quote_char}"

    return _HREF_ATTR_RE.sub(_sub, html)


def inject_tracking(html: str, log_id: int, base_url: str) -> str:
    """Point d'entrée : réécrit les liens pour le suivi de clic, puis ajoute
    le pixel 1×1 de suivi d'ouverture. Ne lève jamais — un échec de suivi ne
    doit pas empêcher l'envoi de l'email."""
    if not html or not base_url or not log_id:
        return html
    try:
        html = _rewrite_links(html, base_url, log_id)
        open_token = make_access_track_token(log_id, "o")
        pixel = f'<img src="{base_url}/access/open/{open_token}" width="1" height="1" alt="" style="display:none">'
        if re.search(r"</body\s*>", html, re.I):
            html = re.sub(r"</body\s*>", pixel + "</body>", html, count=1, flags=re.I)
        else:
            html = html + pixel
        return html
    except Exception:
        return html
