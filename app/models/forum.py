"""Domaine 8 — Forum & Discussions (style Framavox/Loomio)"""
import enum
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Boolean, DateTime, ForeignKey, Text, Integer, Enum as SAEnum, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class StancePosition(str, enum.Enum):
    AGREE     = "AGREE"      # ✓ Accord
    DISAGREE  = "DISAGREE"   # ✗ Désaccord
    ABSTAIN   = "ABSTAIN"    # ⊘ Abstention
    BLOCK     = "BLOCK"      # ⛔ Bloquer


class ForumTheme(Base):
    __tablename__ = "forum_themes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]   = mapped_column(String(200))
    color: Mapped[Optional[str]] = mapped_column(String(10))
    description: Mapped[Optional[str]] = mapped_column(Text)
    min_grade: Mapped[Optional[str]]   = mapped_column(String(50))
    order_position: Mapped[int]        = mapped_column(default=0)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    subjects: Mapped[list["ForumSubject"]] = relationship(back_populates="theme")


class ForumSubject(Base):
    __tablename__ = "forum_subjects"

    id: Mapped[int] = mapped_column(primary_key=True)
    theme_id: Mapped[int] = mapped_column(ForeignKey("forum_themes.id", ondelete="CASCADE"))
    title: Mapped[str]    = mapped_column(String(300))
    is_pinned: Mapped[bool]  = mapped_column(Boolean, default=False)
    is_locked: Mapped[bool]  = mapped_column(Boolean, default=False)
    last_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    theme: Mapped["ForumTheme"]          = relationship(back_populates="subjects")
    messages: Mapped[list["ForumMessage"]] = relationship(
        back_populates="subject", cascade="all, delete-orphan",
    )
    subscriptions: Mapped[list["ForumSubscription"]] = relationship(
        back_populates="subject", cascade="all, delete-orphan",
    )
    decisions: Mapped[list["ForumDecision"]] = relationship(
        "ForumDecision", cascade="all, delete-orphan",
        primaryjoin="ForumSubject.id == ForumDecision.subject_id",
    )


class ForumMessage(Base):
    __tablename__ = "forum_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int]          = mapped_column(ForeignKey("forum_subjects.id", ondelete="CASCADE"))
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("forum_messages.id"))
    content_html: Mapped[str]        = mapped_column(Text)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime]  = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime]  = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    subject: Mapped["ForumSubject"] = relationship(back_populates="messages")
    replies: Mapped[list["ForumMessage"]] = relationship()
    attachments: Mapped[list["ForumAttachment"]] = relationship(
        back_populates="message", cascade="all, delete-orphan",
    )


class ForumSubscription(Base):
    __tablename__ = "forum_subscriptions"

    subject_id: Mapped[int] = mapped_column(ForeignKey("forum_subjects.id", ondelete="CASCADE"), primary_key=True)
    member_id: Mapped[int]  = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), primary_key=True)
    notify_by_email: Mapped[bool] = mapped_column(Boolean, default=True)

    subject: Mapped["ForumSubject"] = relationship(back_populates="subscriptions")


class ForumDecision(Base):
    """Proposition de décision inline dans un fil (style Framavox)."""
    __tablename__ = "forum_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("forum_subjects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str]        = mapped_column(String(300))
    description_html: Mapped[Optional[str]] = mapped_column(Text)
    closes_at: Mapped[Optional[datetime]]   = mapped_column(DateTime)
    closed_at: Mapped[Optional[datetime]]   = mapped_column(DateTime)
    outcome_html: Mapped[Optional[str]]     = mapped_column(Text)  # rédigé par le proposeur après clôture
    created_by_id: Mapped[Optional[int]]    = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime]            = mapped_column(DateTime, server_default=func.now())

    stances: Mapped[list["ForumStance"]] = relationship(back_populates="decision", cascade="all, delete-orphan")


class ForumStance(Base):
    """Position d'un membre sur une décision."""
    __tablename__ = "forum_stances"

    id: Mapped[int] = mapped_column(primary_key=True)
    decision_id: Mapped[int] = mapped_column(ForeignKey("forum_decisions.id", ondelete="CASCADE"), index=True)
    member_id: Mapped[int]   = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True)
    position: Mapped[StancePosition] = mapped_column(SAEnum(StancePosition))
    reason: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    decision: Mapped["ForumDecision"] = relationship(back_populates="stances")


class AttachmentKind(str, enum.Enum):
    FILE     = "FILE"      # Upload local
    LINK     = "LINK"      # URL externe
    DOCUMENT = "DOCUMENT"  # Référence à un Document de la GED


class ForumAttachment(Base):
    """Pièce jointe d'un message du forum (fichier, lien externe ou doc GED)."""
    __tablename__ = "forum_attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("forum_messages.id", ondelete="CASCADE"), index=True,
    )
    kind: Mapped[AttachmentKind] = mapped_column(SAEnum(AttachmentKind))

    # Champs communs
    label: Mapped[Optional[str]]      = mapped_column(String(300))

    # FILE — fichier uploadé
    storage_path: Mapped[Optional[str]]    = mapped_column(String(500))
    original_filename: Mapped[Optional[str]] = mapped_column(String(300))
    mime_type: Mapped[Optional[str]]   = mapped_column(String(100))
    file_size: Mapped[Optional[int]]   = mapped_column(Integer)

    # LINK — URL externe
    url: Mapped[Optional[str]]         = mapped_column(String(2000))

    # DOCUMENT — référence GED
    document_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL")
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    message: Mapped["ForumMessage"] = relationship(back_populates="attachments")
