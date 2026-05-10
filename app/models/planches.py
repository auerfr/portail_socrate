"""Domaine — Planches & travaux maçonniques"""
import enum
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Boolean, DateTime, Integer, Text, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class PlancheStatus(str, enum.Enum):
    BROUILLON = "BROUILLON"
    PUBLIE    = "PUBLIE"


class PlancheGrade(str, enum.Enum):
    TOUS       = "TOUS"        # visible par tous les membres
    APPRENTI   = "APPRENTI"
    COMPAGNON  = "COMPAGNON"
    MAITRE     = "MAITRE"


class Planche(Base):
    __tablename__ = "planches"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))

    # Mode 1 : rédaction en ligne
    content: Mapped[Optional[str]] = mapped_column(Text)

    # Mode 2 : fichier uploadé (PDF, Word, image…)
    file_path: Mapped[Optional[str]] = mapped_column(String(500))
    original_filename: Mapped[Optional[str]] = mapped_column(String(300))
    mime_type: Mapped[Optional[str]] = mapped_column(String(100))
    file_size: Mapped[Optional[int]] = mapped_column(Integer)

    status: Mapped[PlancheStatus] = mapped_column(
        String(20), default=PlancheStatus.BROUILLON
    )
    grade: Mapped[PlancheGrade] = mapped_column(
        String(20), default=PlancheGrade.TOUS
    )

    author_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    meeting_id: Mapped[Optional[int]] = mapped_column(ForeignKey("meetings.id"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, onupdate=func.now())
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Lien vers le Document GED (créé à la publication)
    archived_doc_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )

    author:  Mapped[Optional[object]] = relationship("Member",  foreign_keys=[author_id],  lazy="selectin")
    meeting: Mapped[Optional[object]] = relationship("Meeting", foreign_keys=[meeting_id], lazy="selectin")
    comments: Mapped[list] = relationship(
        "PlancheComment", back_populates="planche",
        order_by="PlancheComment.created_at", lazy="selectin",
        cascade="all, delete-orphan",
    )


class PlancheComment(Base):
    __tablename__ = "planche_comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    planche_id: Mapped[int] = mapped_column(ForeignKey("planches.id", ondelete="CASCADE"))
    author_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    planche: Mapped[object] = relationship("Planche", back_populates="comments")
    author:  Mapped[Optional[object]] = relationship("Member", foreign_keys=[author_id], lazy="selectin")
