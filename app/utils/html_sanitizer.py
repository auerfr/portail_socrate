"""Nettoyage du HTML saisi par les rédacteurs (actualités).

Liste blanche : seules quelques balises de mise en forme et les liens sont
conservés ; tout le reste est retiré (balise supprimée, texte conservé), et
le contenu de <script>/<style> est supprimé entièrement. Implémenté avec la
bibliothèque standard pour ne pas ajouter de dépendance à l'hébergement.
"""
import re
from html import escape
from html.parser import HTMLParser

ALLOWED_TAGS = {"p", "br", "strong", "b", "em", "i", "u", "a", "ul", "ol", "li", "h2", "h3", "h4", "blockquote"}
VOID_TAGS = {"br"}
DROP_CONTENT_TAGS = {"script", "style", "iframe", "object", "embed", "template", "noscript"}
# Balises de bloc : si le texte en contient, l'auteur gère lui-même ses paragraphes
BLOCK_TAGS_RE = re.compile(r"<\s*/?\s*(p|br|ul|ol|li|h2|h3|h4|blockquote)\b", re.I)
URL_RE = re.compile(r"https?://[^\s<>\"]+")
SAFE_HREF_RE = re.compile(r"^(https?://|mailto:|/(?!/)|#)", re.I)


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.open: list[str] = []
        self.dropping = 0

    def handle_starttag(self, tag, attrs):
        if tag in DROP_CONTENT_TAGS:
            self.dropping += 1
            return
        if self.dropping or tag not in ALLOWED_TAGS:
            return
        if tag == "a":
            href = (dict(attrs).get("href") or "").strip()
            if not SAFE_HREF_RE.match(href):
                self.out.append("<a>")
            else:
                self.out.append(f'<a href="{escape(href, quote=True)}" target="_blank" rel="noopener noreferrer">')
        else:
            self.out.append(f"<{tag}>")
        if tag not in VOID_TAGS:
            self.open.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS and self.open and self.open[-1] == tag:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in DROP_CONTENT_TAGS:
            self.dropping = max(0, self.dropping - 1)
            return
        if self.dropping or tag not in self.open:
            return
        # Referme proprement les balises restées ouvertes à l'intérieur
        while self.open:
            t = self.open.pop()
            self.out.append(f"</{t}>")
            if t == tag:
                break

    def handle_data(self, data):
        if self.dropping:
            return
        if "a" in self.open:
            self.out.append(escape(data, quote=False))
            return
        # Adresse collée telle quelle : rendue cliquable (sans la ponctuation finale)
        last = 0
        for m in URL_RE.finditer(data):
            url = m.group(0).rstrip(".,;:!?)»'\"")
            self.out.append(escape(data[last:m.start()], quote=False))
            eu = escape(url, quote=True)
            self.out.append(f'<a href="{eu}" target="_blank" rel="noopener noreferrer">{eu}</a>')
            last = m.start() + len(url)
        self.out.append(escape(data[last:], quote=False))

    def result(self) -> str:
        self.close()
        while self.open:
            self.out.append(f"</{self.open.pop()}>")
        return "".join(self.out)


def sanitize_html(raw: str | None) -> str:
    """HTML nettoyé selon la liste blanche (chaîne vide si rien)."""
    if not raw:
        return ""
    parser = _Sanitizer()
    parser.feed(raw)
    return parser.result()


def text_to_html(raw: str | None) -> str:
    """Saisie d'un rédacteur → HTML nettoyé. Les sauts de ligne deviennent des
    <br>, sauf si l'auteur structure lui-même son texte avec des balises de bloc."""
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not BLOCK_TAGS_RE.search(text):
        text = text.replace("\n", "<br>")
    return sanitize_html(text)


def html_to_editable(html: str | None) -> str:
    """Inverse de text_to_html pour le formulaire de modification : on rend les
    <br> sous forme de sauts de ligne quand c'est eux qui portaient la mise en
    page, et on conserve toutes les autres balises (liens, gras…)."""
    html = html or ""
    if re.search(r"<\s*/?\s*(p|ul|ol|li|h2|h3|h4|blockquote)\b", html, re.I):
        return html
    return re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
