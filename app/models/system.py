"""Système transversal — Audit, Notifications, Tokens, Pièces jointes"""
import enum
import secrets
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Enum, Boolean, DateTime, Integer, ForeignKey, Text, JSON, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class NotificationType(str, enum.Enum):
    INFO             = "INFO"
    WARNING          = "WARNING"
    ACTION_REQUIRED  = "ACTION_REQUIRED"


class TokenType(str, enum.Enum):
    GUEST_AGAPE        = "GUEST_AGAPE"        # Invitation profane agape
    VISITOR_MEETING    = "VISITOR_MEETING"     # Invitation visiteur maçon
    PASSWORD_RESET     = "PASSWORD_RESET"      # Réinitialisation mot de passe
    PROGRAM_SUBSCRIBE  = "PROGRAM_SUBSCRIBE"   # Abonnement programme
    PLATFORM_INVITE    = "PLATFORM_INVITE"     # Invitation à rejoindre la plateforme


class ReminderType(str, enum.Enum):
    MEETING_NO_RESPONSE   = "MEETING_NO_RESPONSE"   # Pas de réponse à une convocation
    COTISATION_DUE        = "COTISATION_DUE"         # Cotisation bientôt due
    COTISATION_OVERDUE_30 = "COTISATION_OVERDUE_30"  # Retard 30 jours
    COTISATION_OVERDUE_60 = "COTISATION_OVERDUE_60"  # Retard 60 jours
    COTISATION_OVERDUE_90 = "COTISATION_OVERDUE_90"  # Retard 90 jours


class ExportType(str, enum.Enum):
    BILAN_MORAL      = "BILAN_MORAL"
    BILAN_COMPTABLE  = "BILAN_COMPTABLE"
    PRESENCE         = "PRESENCE"
    COTISATIONS      = "COTISATIONS"
    PROGRAMME        = "PROGRAMME"
    QUITUS           = "QUITUS"
    TRACING          = "TRACING"


class TracingSectionType(str, enum.Enum):
    OUVERTURE = "OUVERTURE"
    PRESENCES = "PRESENCES"  # Auto-rempli depuis attendances
    ODJ       = "ODJ"        # Ordre du jour
    TRAVAUX   = "TRAVAUX"    # Planches présentées
    DIVERS    = "DIVERS"     # Questions diverses
    CLOTURE   = "CLOTURE"    # Clôture + signature


# ── Système ────────────────────────────────────────────────────────────────

class AuditLog(Base):
    """Log de toutes les actions — piste d'audit complète."""
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    action: Mapped[str]             = mapped_column(String(100), index=True)
    resource_type: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    resource_id: Mapped[Optional[int]] = mapped_column(Integer)
    target_label: Mapped[Optional[str]] = mapped_column(String(300))
    details: Mapped[Optional[dict]] = mapped_column(JSON)  # avant/après
    ip_address: Mapped[Optional[str]] = mapped_column(String(64))
    user_agent: Mapped[Optional[str]] = mapped_column(String(300))
    created_at: Mapped[datetime]      = mapped_column(DateTime, server_default=func.now(), index=True)

    def __repr__(self) -> str:
        return f"<AuditLog {self.action} on {self.resource_type}#{self.resource_id}>"


class Notification(Base):
    """Notification in-app pour un membre."""
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int]           = mapped_column(ForeignKey("members.id", ondelete="CASCADE"))
    type: Mapped[NotificationType]   = mapped_column(Enum(NotificationType))
    title: Mapped[str]               = mapped_column(String(300))
    message: Mapped[str]             = mapped_column(Text)
    link_url: Mapped[Optional[str]]  = mapped_column(String(500))
    is_read: Mapped[bool]            = mapped_column(Boolean, default=False)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime]     = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<Notification {self.type} → member={self.member_id}>"


class PushSubscription(Base):
    """Abonnement PWA push notifications."""
    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int]   = mapped_column(ForeignKey("members.id", ondelete="CASCADE"))
    endpoint: Mapped[str]    = mapped_column(String(1000))
    key_p256dh: Mapped[str]  = mapped_column(String(200))
    key_auth: Mapped[str]    = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Attachment(Base):
    """Pièce jointe sur n'importe quel objet (tenue, tâche, projet, message…)."""
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    object_type: Mapped[str]  = mapped_column(String(100))  # "meeting", "task", "forum_message"…
    object_id: Mapped[int]    = mapped_column(Integer, index=True)
    filename: Mapped[str]     = mapped_column(String(300))
    storage_path: Mapped[str] = mapped_column(String(500))
    mime_type: Mapped[Optional[str]] = mapped_column(String(100))
    file_size: Mapped[Optional[int]] = mapped_column(Integer)
    uploaded_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class InvitationToken(Base):
    """Token d'invitation unique — tous les cas d'invitation."""
    __tablename__ = "invitation_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str]          = mapped_column(
        String(100), unique=True, default=lambda: secrets.token_urlsafe(48)
    )
    type: Mapped[TokenType]     = mapped_column(Enum(TokenType))
    target_id: Mapped[Optional[int]] = mapped_column(Integer)   # ID de la tenue, cotisation, etc.
    email: Mapped[Optional[str]]     = mapped_column(String(200))
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    used_at: Mapped[Optional[datetime]]    = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at

    @property
    def is_used(self) -> bool:
        return self.used_at is not None


class ReminderLog(Base):
    """Log des relances automatiques envoyées."""
    __tablename__ = "reminder_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[ReminderType]   = mapped_column(Enum(ReminderType))
    target_id: Mapped[int]       = mapped_column(Integer)   # ID de la tenue ou cotisation
    member_id: Mapped[int]       = mapped_column(ForeignKey("members.id"))
    channel: Mapped[str]         = mapped_column(String(50))  # "email" | "push" | "chat"
    sent_at: Mapped[datetime]    = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<ReminderLog {self.type} → member={self.member_id}>"


class UserPreference(Base):
    """Préférences utilisateur."""
    __tablename__ = "user_preferences"

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), primary_key=True)
    lang: Mapped[str]      = mapped_column(String(10), default="fr")
    timezone: Mapped[str]  = mapped_column(String(50), default="Europe/Paris")
    notif_email: Mapped[bool] = mapped_column(Boolean, default=True)
    notif_push: Mapped[bool]  = mapped_column(Boolean, default=True)
    notif_chat: Mapped[bool]  = mapped_column(Boolean, default=True)
    theme: Mapped[str]        = mapped_column(String(20), default="light")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ExportArchive(Base):
    """Archive des exports générés (PDF, XLSX)."""
    __tablename__ = "export_archives"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[ExportType]   = mapped_column(Enum(ExportType))
    label: Mapped[str]         = mapped_column(String(300))
    period_start: Mapped[Optional[datetime]] = mapped_column(DateTime)
    period_end: Mapped[Optional[datetime]]   = mapped_column(DateTime)
    filename: Mapped[str]      = mapped_column(String(300))
    storage_path: Mapped[str]  = mapped_column(String(500))
    file_kind: Mapped[str]     = mapped_column(String(10))   # "PDF" | "XLSX"
    checksum_sha256: Mapped[Optional[str]] = mapped_column(String(64))
    created_by_id: Mapped[Optional[int]]  = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<ExportArchive {self.type} {self.file_kind}>"


class TracingSection(Base):
    """
    Section structurée du tracé (compte-rendu de tenue).
    Remplace le champ texte libre — chaque section est verrouillable.
    """
    __tablename__ = "tracing_sections"

    id: Mapped[int] = mapped_column(primary_key=True)
    meeting_id: Mapped[int]        = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"))
    section_type: Mapped[TracingSectionType] = mapped_column(Enum(TracingSectionType))
    content_html: Mapped[Optional[str]]      = mapped_column(Text)
    order_position: Mapped[int]              = mapped_column(Integer, default=0)
    is_locked: Mapped[bool]                  = mapped_column(Boolean, default=False)
    locked_by_id: Mapped[Optional[int]]      = mapped_column(ForeignKey("members.id"))
    locked_at: Mapped[Optional[datetime]]    = mapped_column(DateTime)

    updated_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    meeting: Mapped["Meeting"] = relationship(back_populates="tracing_sections", foreign_keys=[meeting_id])

    def __repr__(self) -> str:
        return f"<TracingSection {self.section_type} meeting={self.meeting_id}>"
