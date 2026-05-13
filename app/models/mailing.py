"""Domaine — Listes de diffusion & campagnes email.

Option C (module minimaliste) : segments statiques ou dynamiques, envoi
groupé avec pièces jointes GED, désinscription RGPD. SANS tracking dans
cette phase.
"""
import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    String, Enum, DateTime, ForeignKey, Text, JSON, Integer, Boolean, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class MailingListType(str, enum.Enum):
    STATIC  = "STATIC"    # liste à composition manuelle (MailingListMember)
    DYNAMIC = "DYNAMIC"   # calculée à la volée selon `criteria`


class CampaignStatus(str, enum.Enum):
    DRAFT    = "DRAFT"
    SENDING  = "SENDING"
    SENT     = "SENT"
    FAILED   = "FAILED"
    CANCELLED= "CANCELLED"


class DeliveryStatus(str, enum.Enum):
    PENDING       = "PENDING"
    SENT          = "SENT"
    FAILED        = "FAILED"
    UNSUBSCRIBED  = "UNSUBSCRIBED"   # destinataire désinscrit avant envoi
    NO_EMAIL      = "NO_EMAIL"        # pas d'email renseigné


class MailingList(Base):
    """Une liste de diffusion (statique ou dynamique)."""
    __tablename__ = "mailing_lists"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]              = mapped_column(String(200))
    description: Mapped[Optional[str]] = mapped_column(Text)
    list_type: Mapped[MailingListType] = mapped_column(
        Enum(MailingListType), default=MailingListType.STATIC
    )
    # Pour les listes dynamiques : critères JSON
    # ex: {"status":["ACTIVE"], "grade":["MAITRE"], "group_ids":[3,5]}
    criteria: Mapped[Optional[dict]] = mapped_column(JSON)
    # Pour les listes "système" pré-créées qu'on ne supprime pas
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)

    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    members: Mapped[list["MailingListMember"]] = relationship(
        back_populates="mailing_list", cascade="all, delete-orphan",
    )
    campaigns: Mapped[list["MailingCampaign"]] = relationship(
        back_populates="mailing_list", cascade="all, delete-orphan",
    )


class MailingListMember(Base):
    """Pour les listes statiques : un membre inscrit dans une liste.

    Pour les listes dynamiques on utilise cette table uniquement pour les
    **désinscriptions** (entrée avec `unsubscribed_at` non NULL).
    """
    __tablename__ = "mailing_list_members"

    list_id: Mapped[int] = mapped_column(
        ForeignKey("mailing_lists.id", ondelete="CASCADE"), primary_key=True
    )
    member_id: Mapped[int] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE"), primary_key=True
    )
    subscribed_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    unsubscribed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    mailing_list: Mapped["MailingList"] = relationship(back_populates="members")


class MailingListExternal(Base):
    """Pour les listes statiques : un contact externe inscrit.
    Pour les listes dynamiques, on utilise cette table uniquement pour
    permettre des **inscriptions** manuelles d'externes (les critères
    dynamiques ne s'appliquent qu'aux Members).
    """
    __tablename__ = "mailing_list_externals"

    list_id: Mapped[int] = mapped_column(
        ForeignKey("mailing_lists.id", ondelete="CASCADE"), primary_key=True
    )
    external_id: Mapped[int] = mapped_column(
        ForeignKey("external_contacts.id", ondelete="CASCADE"), primary_key=True
    )
    subscribed_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    unsubscribed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class MailingCampaign(Base):
    """Une campagne d'envoi groupé."""
    __tablename__ = "mailing_campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    list_id: Mapped[int] = mapped_column(
        ForeignKey("mailing_lists.id", ondelete="CASCADE"), index=True
    )

    subject: Mapped[str]    = mapped_column(String(300))
    body_md: Mapped[str]    = mapped_column(Text)   # Markdown source
    reply_to: Mapped[Optional[str]] = mapped_column(String(300))

    # Pièces jointes : liste de dicts {"doc_id":int,"filename":str} (depuis GED)
    # ou {"url":str,"filename":str} pour PJ externes
    attachments: Mapped[Optional[list]] = mapped_column(JSON)

    status: Mapped[CampaignStatus] = mapped_column(
        Enum(CampaignStatus), default=CampaignStatus.DRAFT, index=True
    )
    # Stats agrégées (mises à jour à la fin de l'envoi)
    recipients_count: Mapped[int] = mapped_column(Integer, default=0)
    sent_count: Mapped[int]       = mapped_column(Integer, default=0)
    failed_count: Mapped[int]     = mapped_column(Integer, default=0)
    opened_count: Mapped[int]     = mapped_column(Integer, default=0)
    clicked_count: Mapped[int]    = mapped_column(Integer, default=0)
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    sender_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime]  = mapped_column(DateTime, server_default=func.now())
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    mailing_list: Mapped["MailingList"] = relationship(back_populates="campaigns")
    deliveries: Mapped[list["MailingDelivery"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan",
    )


class MailingDelivery(Base):
    """Trace d'envoi pour un destinataire d'une campagne."""
    __tablename__ = "mailing_deliveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("mailing_campaigns.id", ondelete="CASCADE"), index=True
    )
    member_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("members.id", ondelete="SET NULL"), index=True
    )
    external_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("external_contacts.id", ondelete="SET NULL"), index=True
    )
    email: Mapped[str] = mapped_column(String(300))

    status: Mapped[DeliveryStatus] = mapped_column(
        Enum(DeliveryStatus), default=DeliveryStatus.PENDING
    )
    error: Mapped[Optional[str]] = mapped_column(Text)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    opened_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    clicked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    click_count: Mapped[int] = mapped_column(Integer, default=0)

    campaign: Mapped["MailingCampaign"] = relationship(back_populates="deliveries")
