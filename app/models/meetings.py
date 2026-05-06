"""Domaine 3 — Tenues, Présences & Agapes"""
import enum
import secrets
from datetime import datetime, date
from typing import Optional
from sqlalchemy import (
    String, Enum, Boolean, DateTime, Date, Integer,
    ForeignKey, Text, UniqueConstraint, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class MeetingGrade(str, enum.Enum):
    APPRENTI  = "APPRENTI"
    COMPAGNON = "COMPAGNON"
    MAITRE    = "MAITRE"
    ALL       = "ALL"     # Toutes loges réunies


class MeetingType(str, enum.Enum):
    BLANCHE      = "BLANCHE"
    INSTRUCTION  = "INSTRUCTION"
    INITIATION   = "INITIATION"
    INSTALLATION = "INSTALLATION"
    ELECTION     = "ELECTION"
    PASSAGE      = "PASSAGE"     # Passage Compagnon
    ELEVATION    = "ELEVATION"   # Élévation Maître
    FETE         = "FETE"        # Saint-Jean, etc.
    EXTRA        = "EXTRA"       # Extraordinaire


class AttendanceStatus(str, enum.Enum):
    PRESENT = "PRESENT"
    EXCUSED = "EXCUSED"
    ABSENT  = "ABSENT"


class DietaryRestriction(str, enum.Enum):
    NONE       = "NONE"
    VEGETARIAN = "VEGETARIAN"
    NO_PORK    = "NO_PORK"
    VEGAN      = "VEGAN"
    OTHER      = "OTHER"


class GuestStatus(str, enum.Enum):
    PENDING   = "PENDING"
    CONFIRMED = "CONFIRMED"
    DECLINED  = "DECLINED"


class VisitorStatus(str, enum.Enum):
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"


class WaitlistStatus(str, enum.Enum):
    WAITING   = "WAITING"
    NOTIFIED  = "NOTIFIED"    # Prévenu qu'une place s'est libérée
    CONFIRMED = "CONFIRMED"
    EXPIRED   = "EXPIRED"


# ── Meetings ───────────────────────────────────────────────────────────────

class Meeting(Base):
    """Tenue (réunion rituelle)."""
    __tablename__ = "meetings"

    id: Mapped[int] = mapped_column(primary_key=True)
    masonic_year_id: Mapped[int] = mapped_column(ForeignKey("masonic_years.id"))

    meeting_date: Mapped[date]        = mapped_column(Date, index=True)
    meeting_time: Mapped[Optional[str]] = mapped_column(String(10))  # "20:30"
    title: Mapped[Optional[str]]      = mapped_column(String(300))
    theme: Mapped[Optional[str]]      = mapped_column(String(300))
    grade: Mapped[MeetingGrade]       = mapped_column(Enum(MeetingGrade))
    type: Mapped[MeetingType]         = mapped_column(Enum(MeetingType), default=MeetingType.BLANCHE)
    location: Mapped[Optional[str]]   = mapped_column(String(300))
    address: Mapped[Optional[str]]    = mapped_column(Text)

    # Contenu
    agenda_html: Mapped[Optional[str]]   = mapped_column(Text)   # Ordre du jour

    # Workflow
    is_locked: Mapped[bool]               = mapped_column(Boolean, default=False)
    locked_by_id: Mapped[Optional[int]]   = mapped_column(ForeignKey("members.id"))
    locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Inscription publique (lien dans le programme PDF)
    token: Mapped[str] = mapped_column(
        String(64), unique=True, default=lambda: secrets.token_urlsafe(32)
    )
    registration_open: Mapped[bool] = mapped_column(Boolean, default=True)
    registration_closes_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Agapes
    agape_enabled: Mapped[bool]          = mapped_column(Boolean, default=True)
    agape_capacity: Mapped[Optional[int]] = mapped_column(Integer)
    agape_location: Mapped[Optional[str]] = mapped_column(String(300))

    # Visio (si tenue hybride)
    visio_url: Mapped[Optional[str]] = mapped_column(String(500))

    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # Relations
    attendances: Mapped[list["Attendance"]]       = relationship(back_populates="meeting")
    meeting_visitors: Mapped[list["MeetingVisitor"]] = relationship(back_populates="meeting")
    meeting_guests: Mapped[list["MeetingGuest"]]    = relationship(back_populates="meeting")
    waitlist: Mapped[list["MeetingWaitlist"]]        = relationship(back_populates="meeting")
    tracing_sections: Mapped[list["TracingSection"]] = relationship(
        "TracingSection", back_populates="meeting"
    )

    def __repr__(self) -> str:
        return f"<Meeting {self.meeting_date} [{self.grade}]>"


class Attendance(Base):
    """Présence d'un frère de la loge à une tenue."""
    __tablename__ = "attendances"
    __table_args__ = (UniqueConstraint("meeting_id", "member_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"))
    member_id: Mapped[int]  = mapped_column(ForeignKey("members.id", ondelete="CASCADE"))

    status: Mapped[AttendanceStatus] = mapped_column(Enum(AttendanceStatus))
    excuse_reason: Mapped[Optional[str]] = mapped_column(Text)

    # Agapes
    agape: Mapped[bool] = mapped_column(Boolean, default=False)
    agape_guests: Mapped[int] = mapped_column(Integer, default=0)

    comment: Mapped[Optional[str]] = mapped_column(Text)
    registered_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Relations
    meeting: Mapped["Meeting"] = relationship(back_populates="attendances")
    member: Mapped["Member"]   = relationship()


class Visitor(Base):
    """Maçon visiteur (frère d'une autre loge)."""
    __tablename__ = "visitors"

    id: Mapped[int] = mapped_column(primary_key=True)
    last_name: Mapped[str]   = mapped_column(String(100))
    first_name: Mapped[str]  = mapped_column(String(100))
    lodge_name: Mapped[Optional[str]]   = mapped_column(String(200))
    orient_city: Mapped[Optional[str]]  = mapped_column(String(200))
    obedience: Mapped[Optional[str]]    = mapped_column(String(200))
    masonic_grade: Mapped[Optional[str]] = mapped_column(String(50))
    is_vm: Mapped[bool]                  = mapped_column(Boolean, default=False)
    email: Mapped[Optional[str]]         = mapped_column(String(200))
    phone: Mapped[Optional[str]]         = mapped_column(String(30))
    program_optin: Mapped[bool]          = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime]         = mapped_column(DateTime, server_default=func.now())

    meeting_visits: Mapped[list["MeetingVisitor"]] = relationship(back_populates="visitor")

    def __repr__(self) -> str:
        return f"<Visitor {self.last_name} — {self.lodge_name}>"


class MeetingVisitor(Base):
    """Présence d'un visiteur maçon à une tenue."""
    __tablename__ = "meeting_visitors"

    id: Mapped[int] = mapped_column(primary_key=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"))
    visitor_id: Mapped[int] = mapped_column(ForeignKey("visitors.id", ondelete="CASCADE"))

    status: Mapped[VisitorStatus] = mapped_column(Enum(VisitorStatus), default=VisitorStatus.CONFIRMED)
    agape: Mapped[bool]           = mapped_column(Boolean, default=False)
    agape_guests: Mapped[int]     = mapped_column(Integer, default=0)
    comment: Mapped[Optional[str]] = mapped_column(Text)
    token_used: Mapped[Optional[str]] = mapped_column(String(200))  # quel lien utilisé
    registered_at: Mapped[datetime]   = mapped_column(DateTime, server_default=func.now())

    meeting: Mapped["Meeting"]  = relationship(back_populates="meeting_visitors")
    visitor: Mapped["Visitor"]  = relationship(back_populates="meeting_visits")


class MeetingGuest(Base):
    """Invité profane (non-maçon) à l'agape."""
    __tablename__ = "meeting_guests"

    id: Mapped[int] = mapped_column(primary_key=True)
    meeting_id: Mapped[int]      = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"))
    invited_by_id: Mapped[int]   = mapped_column(ForeignKey("members.id"))

    last_name: Mapped[str]  = mapped_column(String(100))
    first_name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str]      = mapped_column(String(200))

    # Lien d'invitation unique et nominatif
    token: Mapped[str] = mapped_column(
        String(64), unique=True, default=lambda: secrets.token_urlsafe(32)
    )

    agape: Mapped[bool] = mapped_column(Boolean, default=True)
    dietary_restrictions: Mapped[DietaryRestriction] = mapped_column(
        Enum(DietaryRestriction), default=DietaryRestriction.NONE
    )
    status: Mapped[GuestStatus] = mapped_column(Enum(GuestStatus), default=GuestStatus.PENDING)
    registered_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    meeting: Mapped["Meeting"]   = relationship(back_populates="meeting_guests")
    invited_by: Mapped["Member"] = relationship()


class MeetingWaitlist(Base):
    """Liste d'attente agape quand la capacité est atteinte."""
    __tablename__ = "meeting_waitlist"

    id: Mapped[int] = mapped_column(primary_key=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"))

    # Peut être un membre ou un visiteur (pas les deux)
    member_id: Mapped[Optional[int]]  = mapped_column(ForeignKey("members.id"))
    visitor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("visitors.id"))

    # Ou un externe non-maçon
    external_name: Mapped[Optional[str]]  = mapped_column(String(200))
    external_email: Mapped[Optional[str]] = mapped_column(String(200))

    position: Mapped[int] = mapped_column(Integer)  # rang dans la liste
    status: Mapped[WaitlistStatus] = mapped_column(
        Enum(WaitlistStatus), default=WaitlistStatus.WAITING
    )
    notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    meeting: Mapped["Meeting"] = relationship(back_populates="waitlist")
