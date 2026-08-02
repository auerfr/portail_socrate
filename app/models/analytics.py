"""Domaine Analytics — pages vues internes (équivalent maison léger, sans JS,
sans cookie de suivi tiers). Alimenté par un middleware côté serveur dans
app/main.py, jamais par du JS embarqué chez le membre."""
from datetime import datetime
from typing import Optional
from sqlalchemy import String, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class PageView(Base):
    __tablename__ = "page_views"

    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(String(300), index=True)
    # Hôte d'origine seulement (jamais l'URL complète, potentiellement porteuse
    # de jetons) : "direct" (pas de referrer), "interne" (même origine), ou
    # le nom d'hôte externe (ex: "mail.google.com").
    referrer_host: Mapped[str] = mapped_column(String(200), default="direct", index=True)
    device: Mapped[str] = mapped_column(String(20), default="inconnu")  # mobile / tablette / ordinateur / bot / inconnu
    member_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("members.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # jti du token d'accès au moment de la vue — sert à regrouper les pages
    # vues d'une même session pour estimer sa durée.
    session_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    def __repr__(self) -> str:
        return f"<PageView {self.path} [{self.created_at}]>"
