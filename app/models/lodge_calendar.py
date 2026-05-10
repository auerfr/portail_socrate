"""Domaine 7 — Agenda global (événements libres de la loge)"""
import enum
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Enum, Boolean, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class EventType(str, enum.Enum):
    RITUAL   = "RITUAL"    # Tenue rituelle (auto-générée depuis meetings)
    AGAPE    = "AGAPE"     # Agape / repas de loge
    EXTERNAL = "EXTERNAL"  # Événement extérieur (convent, congrès…)
    ADMIN    = "ADMIN"     # Réunion administrative, bureau
    DEADLINE = "DEADLINE"  # Échéance (clôture cotisations, etc.)
    OTHER    = "OTHER"


class EventVisibility(str, enum.Enum):
    ALL                  = "ALL"                  # Tous les membres actifs
    MAITRES              = "MAITRES"              # Maîtres uniquement
    COMPAGNONS_ET_MAITRES = "COMPAGNONS_ET_MAITRES"  # Compagnons + Maîtres
    APPRENTIS            = "APPRENTIS"            # Apprentis uniquement
    OFFICERS             = "OFFICERS"             # Conseil d'officiers (toute fonction)
    GROUP                = "GROUP"                # Groupe spécifique (visibility_group_id)
    ADMIN                = "ADMIN"                # Administrateurs seulement


class LodgeEvent(Base):
    """Événement libre de l'agenda de la loge."""
    __tablename__ = "lodge_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[Optional[str]] = mapped_column(Text)
    location: Mapped[Optional[str]] = mapped_column(String(300))

    start_datetime: Mapped[datetime] = mapped_column(DateTime)
    end_datetime: Mapped[Optional[datetime]] = mapped_column(DateTime)
    all_day: Mapped[bool] = mapped_column(Boolean, default=True)

    event_type: Mapped[EventType] = mapped_column(Enum(EventType))
    visibility: Mapped[EventVisibility] = mapped_column(
        Enum(EventVisibility), default=EventVisibility.ALL
    )

    # Lien de réunion à distance (Zoom, Teams, Jitsi…)
    meeting_url: Mapped[Optional[str]] = mapped_column(String(500))

    # Pour GroupType.GROUP : groupe ciblé (résolution dynamique)
    visibility_group_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("lodge_groups.id", ondelete="SET NULL"), nullable=True
    )

    # Lien optionnel vers une tenue si cet event est issu d'une tenue
    meeting_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("meetings.id", ondelete="SET NULL"), nullable=True
    )
    masonic_year_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("masonic_years.id", ondelete="SET NULL"), nullable=True
    )

    is_personal: Mapped[bool] = mapped_column(Boolean, default=False)

    created_by_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    created_by: Mapped["Member"] = relationship(foreign_keys=[created_by_id])  # type: ignore[name-defined]

    def __repr__(self) -> str:
        return f"<LodgeEvent {self.title} [{self.start_datetime}]>"
