"""Domaine 2 — Configuration Loge"""
import enum
from datetime import datetime, date
from typing import Optional
from sqlalchemy import String, Boolean, DateTime, Date, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class LodgeSettings(Base):
    __tablename__ = "lodge_settings"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Identité
    name: Mapped[str]         = mapped_column(String(200))
    orient_city: Mapped[str]  = mapped_column(String(200))
    obedience: Mapped[str]    = mapped_column(String(200))
    rite: Mapped[Optional[str]] = mapped_column(String(200))
    logo_url: Mapped[Optional[str]] = mapped_column(String(500))
    temple_address: Mapped[Optional[str]] = mapped_column(Text)

    # SMTP
    smtp_from: Mapped[Optional[str]]  = mapped_column(String(200))
    smtp_host: Mapped[Optional[str]]  = mapped_column(String(200))
    smtp_port: Mapped[Optional[int]]  = mapped_column(Integer)
    smtp_user: Mapped[Optional[str]]  = mapped_column(String(200))
    smtp_pass_enc: Mapped[Optional[str]] = mapped_column(String(500))  # chiffré
    smtp_secure: Mapped[Optional[str]]   = mapped_column(String(10))

    # cPanel
    cpanel_api_url: Mapped[Optional[str]]   = mapped_column(String(500))
    cpanel_api_token_enc: Mapped[Optional[str]] = mapped_column(String(500))

    # Visio
    visio_provider: Mapped[Optional[str]]    = mapped_column(String(50))
    visio_server_url: Mapped[Optional[str]]  = mapped_column(String(500))
    visio_room_prefix: Mapped[Optional[str]] = mapped_column(String(100))

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class MasonicYear(Base):
    """Année maçonnique — de septembre à juin."""
    __tablename__ = "masonic_years"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str]      = mapped_column(String(20), unique=True)  # ex: "5025-5026"
    start_date: Mapped[date] = mapped_column(Date)   # 1er sept
    end_date: Mapped[date]   = mapped_column(Date)   # 30 juin
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<MasonicYear {self.label}>"
