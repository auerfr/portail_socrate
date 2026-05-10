"""Domaine PV — Procès-verbaux de tenues"""
import enum
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Enum, DateTime, Integer, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class ReportStatus(str, enum.Enum):
    BROUILLON = "BROUILLON"   # En cours de rédaction
    SOUMIS    = "SOUMIS"      # Soumis au VM pour approbation
    APPROUVE  = "APPROUVE"    # Approuvé et archivé


class MeetingReport(Base):
    """Procès-verbal d'une tenue."""
    __tablename__ = "meeting_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    meeting_id: Mapped[int] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), unique=True, index=True
    )
    content: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[ReportStatus] = mapped_column(
        Enum(ReportStatus), default=ReportStatus.BROUILLON
    )

    author_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, onupdate=func.now())

    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    approved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Lien vers le document GED archivé
    archived_doc_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )

    meeting: Mapped[Optional[object]] = relationship(
        "Meeting", foreign_keys=[meeting_id], lazy="selectin"
    )
    author: Mapped[Optional[object]] = relationship(
        "Member", foreign_keys=[author_id], lazy="selectin"
    )
    approved_by: Mapped[Optional[object]] = relationship(
        "Member", foreign_keys=[approved_by_id], lazy="selectin"
    )
