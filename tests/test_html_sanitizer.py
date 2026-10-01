"""Nettoyage du HTML des actualités (app/utils/html_sanitizer.py)."""
from app.utils.html_sanitizer import html_to_editable, sanitize_html, text_to_html


def test_script_and_handlers_removed():
    out = sanitize_html('<p onclick="x()">Salut<script>alert(1)</script></p><img src=x onerror=alert(1)>')
    assert out == "<p>Salut</p>"


def test_javascript_link_neutralised():
    out = sanitize_html('<a href="javascript:alert(1)">clic</a>')
    assert "javascript" not in out and "clic" in out


def test_safe_link_kept_and_opens_in_new_tab():
    out = sanitize_html('<a href="https://staging.amisdesocrate.fr/statistiques/">Stats</a>')
    assert out == ('<a href="https://staging.amisdesocrate.fr/statistiques/" '
                   'target="_blank" rel="noopener noreferrer">Stats</a>')


def test_text_is_escaped():
    assert sanitize_html("a < b & c") == "a &lt; b &amp; c"


def test_unclosed_tags_are_closed():
    assert sanitize_html("<b>gras <i>italique") == "<b>gras <i>italique</i></b>"


def test_line_breaks_kept_with_inline_link():
    out = text_to_html('Ligne 1\nVoir <a href="https://a.fr/">ici</a>')
    assert out.startswith("Ligne 1<br>Voir <a ")


def test_paragraphs_left_to_author():
    out = text_to_html("<p>Un</p>\n<p>Deux</p>")
    assert "<br>" not in out and out.count("<p>") == 2


def test_edit_round_trip_keeps_links():
    for raw in ['Ligne 1\nVoir <a href="https://a.fr/">ici</a>', "<p>Un <b>gras</b></p>\n<p>Deux</p>"]:
        saved = text_to_html(raw)
        assert text_to_html(html_to_editable(saved)) == saved


def test_bare_url_becomes_link():
    out = sanitize_html("<p>C'est ici : https://staging.amisdesocrate.fr/statistiques/.</p>")
    assert ('<a href="https://staging.amisdesocrate.fr/statistiques/" target="_blank" '
            'rel="noopener noreferrer">https://staging.amisdesocrate.fr/statistiques/</a>.') in out


def test_quill_markup_kept():
    html = ('<h2>Titre</h2><p><strong>Gras</strong> <em>italique</em> <u>souligné</u></p>'
            '<ul><li>un</li><li>deux</li></ul>'
            '<p><a href="https://a.fr/" rel="noopener noreferrer" target="_blank">lien</a></p>')
    assert sanitize_html(html) == html.replace(' rel="noopener noreferrer" target="_blank"',
                                               ' target="_blank" rel="noopener noreferrer"')
