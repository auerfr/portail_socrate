"""Domaine 2 — Configuration Loge"""
import enum
from datetime import datetime, date
from typing import Optional
from sqlalchemy import String, Boolean, DateTime, Date, Integer, Text, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class LodgeSettings(Base):
    __tablename__ = "lodge_settings"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Identité
    name: Mapped[str]         = mapped_column(String(200))
    orient_city: Mapped[str]  = mapped_column(String(200))
    obedience: Mapped[str]    = mapped_column(String(200))
    rite: Mapped[Optional[str]] = mapped_column(String(200))
    loge_number: Mapped[Optional[str]] = mapped_column(String(20))   # ex: "4276"
    logo_url: Mapped[Optional[str]] = mapped_column(String(500))
    temple_address: Mapped[Optional[str]] = mapped_column(Text)

    # Temple
    temple_name: Mapped[Optional[str]]  = mapped_column(String(300))  # ex: "Salle la Colonie"
    temple_note: Mapped[Optional[str]]  = mapped_column(String(300))  # ex: "(lieu profane)"

    # Contacts officiels (affichés sur les programmes)
    # Référence optionnelle vers un membre (pour la sélection dans les paramètres)
    vm_member_id: Mapped[Optional[int]]          = mapped_column(ForeignKey("members.id"), nullable=True)
    secretary_member_id: Mapped[Optional[int]]   = mapped_column(ForeignKey("members.id"), nullable=True)
    # Champs libres (si pas de référence membre, ou pour afficher un nom maçonnique)
    vm_name_display: Mapped[Optional[str]]        = mapped_column(String(200))  # ex: "Alain FVR∴"
    vm_email_display: Mapped[Optional[str]]       = mapped_column(String(200))
    vm_phone: Mapped[Optional[str]]               = mapped_column(String(30))
    secretary_name_display: Mapped[Optional[str]] = mapped_column(String(200))
    secretary_email_display: Mapped[Optional[str]] = mapped_column(String(200))

    # Textes récurrents sur les programmes
    standard_schedule: Mapped[Optional[str]] = mapped_column(Text)  # horaires habituels
    chantiers_info: Mapped[Optional[str]]    = mapped_column(Text)  # chantiers de la loge
    common_agenda: Mapped[Optional[str]]     = mapped_column(Text)  # OJ commun à toutes tenues

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


class LodgeOffice(Base):
    """Office rituel configurable — label modifiable, membre assignable."""
    __tablename__ = "lodge_offices"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(100))          # ex: "Couvreur", "V∴M∴"
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    member_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"), nullable=True)


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
