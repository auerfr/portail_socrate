"""Rendu HTML → PDF partagé (WeasyPrint), utilisé par les tracés et les planches."""
import os


def render_html_to_pdf(html: str) -> bytes:
    """Rend un gabarit HTML en PDF via WeasyPrint."""
    import weasyprint

    def _static_url_fetcher(url: str):
        # Les références d'images du gabarit sont en chemin absolu
        # (ex: /static/img/sceau-socrate-transparent.png) — pensées pour
        # être servies par Starlette, pas résolues comme chemin fichier.
        # Une fois converties en file:// par WeasyPrint via base_url, la
        # résolution RFC 3986 d'un chemin absolu ignore le path de la
        # base et repart de la racine du filesystem : on les redirige
        # donc explicitement vers app/static/.
        if url.startswith("file:///static/"):
            rel = url[len("file:///static/"):]
            url = f"file://{os.getcwd()}/app/static/{rel}"
        return weasyprint.default_url_fetcher(url)

    base_url = f"file://{os.getcwd()}/"
    return weasyprint.HTML(
        string=html, base_url=base_url, url_fetcher=_static_url_fetcher
    ).write_pdf()
