"""Domaine 10 — Communication Email + Annonces internes"""
import enum
from datetime import date, datetime
from typing import Optional
from sqlalchemy import Boolean, Date, String, Enum, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class EmailCategory(str, enum.Enum):
    CONVOCATION      = "CONVOCATION"
    PROGRAMME        = "PROGRAMME"
    RAPPEL_COTISATION = "RAPPEL_COTISATION"
    RELANCE_COTISATION = "RELANCE_COTISATION"
    QUITUS           = "QUITUS"
    INVITATION       = "INVITATION"
    GENERAL          = "GENERAL"


class CampaignStatus(str, enum.Enum):
    DRAFT     = "DRAFT"
    SCHEDULED = "SCHEDULED"
    SENDING   = "SENDING"
    SENT      = "SENT"
    FAILED    = "FAILED"


class RecipientStatus(str, enum.Enum):
    PENDING = "PENDING"
    SENT    = "SENT"
    FAILED  = "FAILED"
    BOUNCED = "BOUNCED"


class EmailTemplate(Base):
    """Template email réutilisable."""
    __tablename__ = "email_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]              = mapped_column(String(200))
    subject: Mapped[str]           = mapped_column(String(500))
    body_html: Mapped[str]         = mapped_column(Text)
    category: Mapped[EmailCategory] = mapped_column(Enum(EmailCategory))

    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<EmailTemplate {self.name}>"


class EmailCampaign(Base):
    """Envoi email vers un groupe."""
    __tablename__ = "email_campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[Optional[int]] = mapped_column(ForeignKey("email_templates.id"))

    # Snapshot au moment de l'envoi
    subject: Mapped[str]   = mapped_column(String(500))
    body_html: Mapped[str] = mapped_column(Text)

    # Cible
    target_group_id: Mapped[Optional[int]] = mapped_column(ForeignKey("groups.id"))

    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    sent_at: Mapped[Optional[datetime]]      = mapped_column(DateTime)
    status: Mapped[CampaignStatus]           = mapped_column(
        Enum(CampaignStatus), default=CampaignStatus.DRAFT
    )

    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    recipients: Mapped[list["EmailRecipient"]] = relationship(back_populates="campaign")

    def __repr__(self) -> str:
        return f"<EmailCampaign '{self.subject}' [{self.status}]>"


class EmailRecipient(Base):
    """Destinataire individuel d'une campagne."""
    __tablename__ = "email_recipients"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("email_campaigns.id", ondelete="CASCADE"))
    email: Mapped[str]       = mapped_column(String(200))
    member_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))

    status: Mapped[RecipientStatus] = mapped_column(
        Enum(RecipientStatus), default=RecipientStatus.PENDING
    )
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    campaign: Mapped["EmailCampaign"] = relationship(back_populates="recipients")


# ── Annonces internes ─────────────────────────────────────────────────────────

class Announcement(Base):
    """Annonce officielle postée par le secrétariat, visible sur le dashboard."""
    __tablename__ = "announcements"

    id: Mapped[int]          = mapped_column(primary_key=True)
    title: Mapped[str]       = mapped_column(String(300))
    content: Mapped[str]     = mapped_column(Text)
    author_id: Mapped[int]   = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    is_pinned: Mapped[bool]  = mapped_column(Boolean, default=False)
    expires_at: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    author: Mapped["Member"] = relationship(
        "Member", foreign_keys=[author_id]
    )
    reads: Mapped[list["AnnouncementRead"]] = relationship(
        back_populates="announcement", cascade="all, delete-orphan"
    )

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_at and self.expires_at < date.today())

    def __repr__(self) -> str:
        return f"<Announcement '{self.title}'>"


class AnnouncementRead(Base):
    """Suivi de lecture : un membre a vu une annonce."""
    __tablename__ = "announcement_reads"

    id: Mapped[int]               = mapped_column(primary_key=True)
    announcement_id: Mapped[int]  = mapped_column(ForeignKey("announcements.id", ondelete="CASCADE"))
    member_id: Mapped[int]        = mapped_column(ForeignKey("members.id", ondelete="CASCADE"))
    read_at: Mapped[datetime]     = mapped_column(DateTime, server_default=func.now())

    announcement: Mapped["Announcement"]          = relationship(back_populates="reads")
    member: Mapped["Member"] = relationship("Member")
