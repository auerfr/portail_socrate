"""Script one-shot : publie directement en base les deux actualités rédigées
avec l'administrateur — évite le collage de HTML brut dans l'éditeur Quill
(qui l'insère comme texte visible au lieu de l'interpréter).

Lancer une seule fois depuis la console PythonAnywhere :
  cd ~/portail-socrate && ~/.virtualenvs/socrate-env/bin/python scripts/publish_news_articles.py [--dry-run]
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# NewsArticle.target_group_id référence lodge_groups : sans cet import, la
# configuration des mappers SQLAlchemy échoue au premier commit() (la classe
# n'est chargée dans aucun autre module importé par ce script autonome).
import app.models.groups  # noqa: F401

from sqlalchemy import select

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models.content import NewsArticle

# Un lien relatif ("/faq#...") ne se résout correctement que lu depuis une
# page du portail. Dans une actualité, il peut être recopié/ouvert hors de ce
# contexte (email, aperçu de notification, copier-coller) — d'où l'URL
# absolue, construite à partir du portal_url réellement configuré (ex :
# https://staging.amisdesocrate.fr), jamais codée en dur.
_settings = get_settings()
BASE_URL = (_settings.portal_url or f"https://{_settings.lodge_domain}").rstrip("/")

ARTICLES = [
    {
        "title": "Nouveau : invitez vous-même un visiteur à une tenue",
        "content_html": f"""
<p>Vous recevez prochainement un F∴/S∴ passant, ou une loge amie souhaite envoyer un représentant ? Vous pouvez désormais l'inviter directement, sans passer par le Secrétariat.</p>
<p>Sur la fiche de chaque tenue (menu <strong>Tenues</strong>), un bloc <strong>« Lien d'inscription »</strong> est maintenant visible par tous. Copiez-le et transmettez-le par SMS, email ou WhatsApp à la personne que vous invitez : elle s'inscrit elle-même en quelques clics, agapes comprises.</p>
<p>Si elle participe aux agapes, inscrivez-la <strong>le plus tôt possible</strong> : notre Maître des Banquets est désormais averti automatiquement à chaque inscription, mais il a des délais de commande à respecter — une inscription de dernière minute peut arriver trop tard.</p>
<p>Détails dans la <a href="{BASE_URL}/faq#visiteur-inscription">FAQ</a>.</p>
""".strip(),
    },
    {
        "title": "Activez les notifications sur votre téléphone",
        "content_html": f"""
<p>Le portail peut vous prévenir directement sur votre téléphone — rappels de tenue, anniversaires, messages importants — même quand le navigateur est fermé.</p>
<p>Rendez-vous dans <strong>Notifications</strong> (sous votre nom, en bas du menu, ou dans « Plus » sur mobile) et cliquez sur <strong>« Activer »</strong>.</p>
<p>Sur iPhone/iPad : ça ne fonctionne que depuis l'app installée sur l'écran d'accueil (Safari → Partager → « Sur l'écran d'accueil »), jamais depuis un onglet Safari classique. Sur Android, c'est direct depuis le navigateur.</p>
<p>Le mode opératoire complet est dans la <a href="{BASE_URL}/faq#notifications-mobile">FAQ</a>.</p>
""".strip(),
    },
]


async def run(dry_run: bool = False) -> None:
    async with AsyncSessionLocal() as db:
        for data in ARTICLES:
            existing = (await db.execute(
                select(NewsArticle).where(NewsArticle.title == data["title"])
            )).scalars().first()
            if existing:
                print(f"{'[DRY-RUN] ' if dry_run else ''}Mise à jour (déjà publiée) : {data['title']}")
                if not dry_run:
                    existing.content_html = data["content_html"]
            else:
                print(f"{'[DRY-RUN] ' if dry_run else ''}Publication : {data['title']}")
                if not dry_run:
                    db.add(NewsArticle(title=data["title"], content_html=data["content_html"]))
        if not dry_run:
            await db.commit()
    print("Terminé." if not dry_run else "\n[DRY-RUN] Rien n'a été écrit en base.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Affiche sans publier")
    args = parser.parse_args()
    asyncio.run(run(dry_run=args.dry_run))
