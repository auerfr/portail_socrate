"""app.services.email_tracking.inject_tracking — pixel d'ouverture + liens de
clic ajoutés automatiquement à TOUT email journalisé, pas seulement l'email
d'accès (régression : le tableau de bord d'engagement affichait ~0%
d'ouverture sur la quasi-totalité des emails envoyés, faute de pixel sur ces
emails-là — cf. app/routers/engagement.py)."""
from app.services.email_tracking import inject_tracking, _rewrite_links
from app.services.member_access import verify_access_track_token


def test_pixel_inserted_before_closing_body():
    html = "<html><body><p>Bonjour</p></body></html>"
    out = inject_tracking(html, log_id=42, base_url="https://portail.test")
    assert "/access/open/" in out
    assert out.index("/access/open/") < out.index("</body>")


def test_pixel_appended_when_no_body_tag():
    html = "<p>Bonjour</p>"
    out = inject_tracking(html, log_id=42, base_url="https://portail.test")
    assert out.startswith("<p>Bonjour</p>")
    assert "/access/open/" in out


def test_open_pixel_token_resolves_to_correct_log_id():
    html = "<p>Bonjour</p>"
    out = inject_tracking(html, log_id=99, base_url="https://portail.test")
    # Extrait le token depuis l'URL du pixel et vérifie qu'il pointe bien sur log_id=99
    url = out.split('src="')[1].split('"')[0]
    token = url.rsplit("/", 1)[-1]
    parsed = verify_access_track_token(token)
    assert parsed == (99, "o")


def test_http_link_rewritten_to_click_tracker():
    html = '<a href="https://example.com/page">Cliquez ici</a>'
    out = _rewrite_links(html, "https://portail.test", log_id=7)
    assert "https://portail.test/access/click/" in out
    assert "url=https%3A%2F%2Fexample.com%2Fpage" in out
    # L'URL d'origine reste récupérable en paramètre, jamais perdue
    assert "example.com" in out


def test_other_attributes_preserved_when_rewriting_link():
    html = '<a href="https://example.com" class="btn" target="_blank">Go</a>'
    out = _rewrite_links(html, "https://portail.test", log_id=7)
    assert 'class="btn"' in out
    assert 'target="_blank"' in out


def test_mailto_and_tel_links_untouched():
    html = '<a href="mailto:test@example.com">Email</a> <a href="tel:+33123456789">Tel</a>'
    out = _rewrite_links(html, "https://portail.test", log_id=7)
    assert out == html


def test_already_tracked_mailing_campaign_link_not_double_wrapped():
    html = '<a href="https://portail.test/mailing/track/click/abc.c.sig?url=https%3A%2F%2Fexample.com">Lien</a>'
    out = _rewrite_links(html, "https://portail.test", log_id=7)
    assert out == html


def test_already_tracked_access_link_not_double_wrapped():
    html = '<a href="https://portail.test/access/click/1.c.sig?url=https%3A%2F%2Fexample.com">Lien</a>'
    out = _rewrite_links(html, "https://portail.test", log_id=7)
    assert out == html


def test_relative_and_cid_links_untouched():
    html = '<a href="/faq">FAQ</a> <img src="cid:qr1">'
    out = _rewrite_links(html, "https://portail.test", log_id=7)
    assert out == html


def test_inject_tracking_never_raises_on_missing_args():
    assert inject_tracking("", log_id=1, base_url="https://x") == ""
    assert inject_tracking("<p>x</p>", log_id=None, base_url="https://x") == "<p>x</p>"
    assert inject_tracking("<p>x</p>", log_id=1, base_url="") == "<p>x</p>"
