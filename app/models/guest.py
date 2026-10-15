"""Domaine — Accès visiteur (frères d'autres loges, droits très restreints).

Un GuestAccount est volontairement une entité séparée de Member/User : il
n'a pas de fonction en loge, et la quasi-totalité des permissions de
l'appli reposent sur fonction + grade combinés. Le mélanger dans Member
aurait obligé à faire gérer "et si c'est None ?" à des dizaines d'endroits
déjà écrits pour des membres. Séparé, un compte visiteur ne peut
strictement rien faire par défaut : seules les routes qui dépendent
explicitement de require_guest_auth (voir app/dependencies.py) lui sont
accessibles — tout le reste de l'application lui est fermé par
construction, pas par une liste noire à tenir à jour.

Il a en revanche bien un grade maçonnique réel (c'est un Maçon d'une autre
loge) : le contenu qui lui est ouvert (programme, documents) reste filtré
par ce grade exactement comme pour un membre, en plus de l'opt-in explicite
posé par l'officier — un visiteur Apprenti ne voit jamais une tenue ou un
document réservé aux Maîtres même si elle est par ailleurs marquée
visible aux visiteurs.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Boolean, DateTime, Enum, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.identity import MasonicGrade


class GuestAccount(Base):
    __tablename__ = "guest_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)

    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    home_lodge: Mapped[Optional[str]] = mapped_column(String(200))
    obedience: Mapped[Optional[str]] = mapped_column(String(200))
    # Grade réel du visiteur (renseigné par l'officier qui invite, qui le
    # connaît) — sert de plafond sur tout contenu grade-gated qui lui est
    # ouvert. NULL = traité comme APPRENTI (le plus restrictif) tant que ce
    # n'est pas renseigné, jamais comme "tout voir".
    masonic_grade: Mapped[Optional[MasonicGrade]] = mapped_column(Enum(MasonicGrade), nullable=True)

    # Authentification — vide tant que l'onboarding (1er lien magique) n'est
    # pas terminé. 2FA obligatoire pour ce type de compte (pas une option
    # comme pour les membres) : onboarding_completed ne passe à True
    # qu'une fois le mot de passe ET le TOTP configurés.
    password_hash: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    totp_secret: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False)

    magic_token: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, unique=True, index=True)
    magic_token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Expiration = désactivation, jamais suppression automatique (décision du
    # 14/10/2026) : garde l'historique, permet une réactivation simple si le
    # frère revient, et ne casse pas une éventuelle promotion ultérieure.
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    invited_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"), nullable=True)
    # Rattachement à une fiche Visitor existante (frère déjà venu
    # physiquement à une tenue) — évite de ressaisir loge/obédience et relie
    # l'historique de présence.
    linked_visitor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("visitors.id", ondelete="SET NULL"), nullable=True)
    # Renseigné lors d'une promotion en membre à part entière (Phase 4) —
    # le compte visiteur n'est alors plus utilisable mais reste comme trace.
    promoted_to_member_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    invited_by: Mapped[Optional["Member"]] = relationship(foreign_keys=[invited_by_id])
    linked_visitor: Mapped[Optional["Visitor"]] = relationship(foreign_keys=[linked_visitor_id])
    promoted_to_member: Mapped[Optional["Member"]] = relationship(foreign_keys=[promoted_to_member_id])

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    def __repr__(self) -> str:
        return f"<GuestAccount {self.full_name} — {self.home_lodge}>"


class GuestSession(Base):
    """Session visiteur enregistrée pour permettre la révocation — même
    principe que UserSession (app/models/system.py), table séparée pour ne
    jamais mélanger les deux espaces de session."""
    __tablename__ = "guest_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    guest_account_id: Mapped[int] = mapped_column(
        ForeignKey("guest_accounts.id", ondelete="CASCADE"), index=True
    )
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64))
    user_agent: Mapped[Optional[str]] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
