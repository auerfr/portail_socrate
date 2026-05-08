"""Domaine — Messagerie interne ciblée"""
import enum
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    String, Enum, Boolean, DateTime, ForeignKey, Text, Integer,
    UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class MessageTargetType(str, enum.Enum):
    ALL      = "ALL"       # Tous les membres actifs
    GRADE    = "GRADE"     # Grade minimum (APPRENTI | COMPAGNON | MAITRE)
    FUNCTION = "FUNCTION"  # Une ou plusieurs fonctions
    GROUP    = "GROUP"     # Un groupe existant (par group_id)
    MANUAL   = "MANUAL"    # Liste manuelle de member_id


class Message(Base):
    """Message envoyé par un officier à un groupe de membres."""
    __tablename__ = "messages"

    id: Mapped[int]         = mapped_column(primary_key=True)
    subject: Mapped[str]    = mapped_column(String(300))
    body: Mapped[str]       = mapped_column(Text)

    sender_id: Mapped[int]  = mapped_column(ForeignKey("members.id"))
    target_type: Mapped[MessageTargetType] = mapped_column(Enum(MessageTargetType))
    # JSON : {"grade":"MAITRE"} | {"functions":["VM"]} | {"group_id":3} | {"member_ids":[1,2]}
    target_filter: Mapped[Optional[str]] = mapped_column(Text)

    # Réponse à un message (thread)
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("messages.id"))

    visio_url: Mapped[Optional[str]]      = mapped_column(String(500))

    sent_at: Mapped[Optional[datetime]]  = mapped_column(DateTime)   # None = brouillon
    created_at: Mapped[datetime]         = mapped_column(DateTime, server_default=func.now())

    recipients: Mapped[list["MessageRecipient"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )
    replies: Mapped[list["Message"]] = relationship(
        "Message", foreign_keys="[Message.parent_id]",
        cascade="all, delete-orphan",
    )
    attachments: Mapped[list["MessageAttachment"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Message #{self.id} '{self.subject}'>"


class MessageAttachment(Base):
    """Pièce jointe à un message interne."""
    __tablename__ = "message_attachments"

    id: Mapped[int]          = mapped_column(primary_key=True)
    message_id: Mapped[int]  = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))

    # Nom original du fichier (affiché à l'utilisateur)
    filename: Mapped[str]    = mapped_column(String(300))
    # Nom de stockage sur disque (uuid + ext, sans caractères dangereux)
    stored_name: Mapped[str] = mapped_column(String(300), unique=True)
    mime_type: Mapped[Optional[str]] = mapped_column(String(100))
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer)

    uploaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    message: Mapped["Message"] = relationship(back_populates="attachments")

    def __repr__(self) -> str:
        return f"<MessageAttachment '{self.filename}' msg={self.message_id}>"


class MessageRecipient(Base):
    """Un destinataire d'un message."""
    __tablename__ = "message_recipients"

    id: Mapped[int]          = mapped_column(primary_key=True)
    message_id: Mapped[int]  = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    member_id: Mapped[int]   = mapped_column(ForeignKey("members.id"))

    delivered_at: Mapped[datetime]          = mapped_column(DateTime, server_default=func.now())
    read_at: Mapped[Optional[datetime]]     = mapped_column(DateTime)
    email_sent: Mapped[bool]                = mapped_column(Boolean, default=False)

    message: Mapped["Message"] = relationship(back_populates="recipients")

    __table_args__ = (
        UniqueConstraint("message_id", "member_id", name="uq_message_recipient"),
    )

    def __repr__(self) -> str:
        return f"<MessageRecipient msg={self.message_id} member={self.member_id}>"
