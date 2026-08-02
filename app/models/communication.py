"""Domaine 10 — Annonces internes"""
from datetime import date, datetime
from typing import Optional
from sqlalchemy import Boolean, Date, String, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


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
