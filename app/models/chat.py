"""Domaine 9 — Messagerie instantanée (remplace Telegram)"""
import enum
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Enum, Boolean, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class ChannelType(str, enum.Enum):
    GENERAL    = "GENERAL"    # Canal général ouvert à tous
    GRADE      = "GRADE"      # Filtré par grade
    FUNCTION   = "FUNCTION"   # Réservé aux fonctions (ex: officiers)
    COMMISSION = "COMMISSION" # Lié à une commission/projet
    DIRECT     = "DIRECT"     # Message privé entre deux membres


class MessageContentType(str, enum.Enum):
    TEXT  = "TEXT"
    IMAGE = "IMAGE"
    FILE  = "FILE"


class ChatChannel(Base):
    """Canal de messagerie."""
    __tablename__ = "chat_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]               = mapped_column(String(200))
    description: Mapped[Optional[str]] = mapped_column(Text)
    type: Mapped[ChannelType]       = mapped_column(Enum(ChannelType))

    # Filtres pour canaux dynamiques
    grade_filter: Mapped[Optional[str]]    = mapped_column(String(50))
    function_filter: Mapped[Optional[str]] = mapped_column(String(50))

    # Seuls les admins/VM peuvent écrire
    is_readonly: Mapped[bool] = mapped_column(Boolean, default=False)

    # URL visio lancée depuis ce canal
    visio_url: Mapped[Optional[str]] = mapped_column(String(500))

    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    members: Mapped[list["ChatChannelMember"]] = relationship(back_populates="channel")
    messages: Mapped[list["ChatMessage"]]       = relationship(back_populates="channel")

    def __repr__(self) -> str:
        return f"<ChatChannel #{self.name} [{self.type}]>"


class ChatChannelMember(Base):
    """Appartenance d'un membre à un canal."""
    __tablename__ = "chat_channel_members"

    channel_id: Mapped[int] = mapped_column(ForeignKey("chat_channels.id", ondelete="CASCADE"), primary_key=True)
    member_id: Mapped[int]  = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), primary_key=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    is_muted: Mapped[bool]      = mapped_column(Boolean, default=False)

    channel: Mapped["ChatChannel"] = relationship(back_populates="members")
    member: Mapped["Member"]       = relationship()


class ChatMessage(Base):
    """Message dans un canal."""
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("chat_channels.id", ondelete="CASCADE"))
    sender_id: Mapped[int]  = mapped_column(ForeignKey("members.id"))

    content: Mapped[Optional[str]]       = mapped_column(Text)
    content_type: Mapped[MessageContentType] = mapped_column(
        Enum(MessageContentType), default=MessageContentType.TEXT
    )
    attachment_url: Mapped[Optional[str]] = mapped_column(String(500))
    reply_to_id: Mapped[Optional[int]]   = mapped_column(ForeignKey("chat_messages.id"))

    is_deleted: Mapped[bool]              = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime]          = mapped_column(DateTime, server_default=func.now())
    edited_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    channel: Mapped["ChatChannel"]   = relationship(back_populates="messages")
    sender: Mapped["Member"]          = relationship()
    reply_to: Mapped[Optional["ChatMessage"]] = relationship(remote_side="ChatMessage.id")


class ChatRead(Base):
    """Statut de lecture par canal et par membre."""
    __tablename__ = "chat_reads"

    channel_id: Mapped[int] = mapped_column(ForeignKey("chat_channels.id", ondelete="CASCADE"), primary_key=True)
    member_id: Mapped[int]  = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), primary_key=True)
    last_read_message_id: Mapped[Optional[int]] = mapped_column(ForeignKey("chat_messages.id"))
    last_read_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
