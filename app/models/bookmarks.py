"""Domaine — Liens à partager (Bookmarks collectifs type Pocket)."""
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, Boolean, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class Bookmark(Base):
    __tablename__ = "bookmarks"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str]             = mapped_column(String(2000))
    title: Mapped[str]           = mapped_column(String(300))
    description: Mapped[Optional[str]] = mapped_column(Text)
    tags: Mapped[Optional[str]]  = mapped_column(String(300))   # tags séparés par virgule
    is_public: Mapped[bool]      = mapped_column(Boolean, default=True)

    added_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
