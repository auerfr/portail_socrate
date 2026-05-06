"""Domaine 8 — Forum & Discussions"""
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Boolean, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


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
    messages: Mapped[list["ForumMessage"]] = relationship(back_populates="subject")
    subscriptions: Mapped[list["ForumSubscription"]] = relationship(back_populates="subject")


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


class ForumSubscription(Base):
    __tablename__ = "forum_subscriptions"

    subject_id: Mapped[int] = mapped_column(ForeignKey("forum_subjects.id", ondelete="CASCADE"), primary_key=True)
    member_id: Mapped[int]  = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), primary_key=True)
    notify_by_email: Mapped[bool] = mapped_column(Boolean, default=True)

    subject: Mapped["ForumSubject"] = relationship(back_populates="subscriptions")
