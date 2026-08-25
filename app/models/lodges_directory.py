"""Domaine 15 — Répertoire des loges voisines (annuaire externe)

Liste de référence des loges des environs (orient, rite, obédience,
horaire et rythme théorique de réunion) — permet aux FF∴ et SS∴ de
repérer une loge à visiter. Rythme théorique uniquement (pas de dates
réelles confirmées — voir le module "Planches reçues" pour ça)."""
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, DateTime, JSON, ForeignKey, Float, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class NeighboringLodge(Base):
    """Loge externe de référence — rythme théorique de réunion."""
    __tablename__ = "neighboring_lodges"

    id: Mapped[int] = mapped_column(primary_key=True)
    orient: Mapped[str]              = mapped_column(String(200))
    region: Mapped[Optional[str]]    = mapped_column(String(100))
    name: Mapped[str]                = mapped_column(String(300))
    rite: Mapped[Optional[str]]      = mapped_column(String(100))
    obedience: Mapped[Optional[str]] = mapped_column(String(100))
    address: Mapped[Optional[str]]   = mapped_column(String(300))  # adresse du temple
    latitude: Mapped[Optional[float]]  = mapped_column(Float)
    longitude: Mapped[Optional[float]] = mapped_column(Float)
    meeting_time: Mapped[Optional[str]] = mapped_column(String(20))
    # Rythme théorique : liste de {"week": 1-5, "day": "Lundi".."Dimanche"}
    schedule: Mapped[Optional[list]] = mapped_column(JSON)
    notes: Mapped[Optional[str]]     = mapped_column(Text)

    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<NeighboringLodge {self.name} ({self.orient})>"
