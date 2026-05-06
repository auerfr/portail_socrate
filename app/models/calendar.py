"""Domaine 7 — Calendrier"""
import enum
from datetime import datetime, date
from typing import Optional
from sqlalchemy import String, Enum, Boolean, DateTime, Date, ForeignKey, Text, JSON, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class RecurrenceType(str, enum.Enum):
    NONE    = "NONE"
    DAILY   = "DAILY"
    WEEKLY  = "WEEKLY"
    MONTHLY = "MONTHLY"
    YEARLY  = "YEARLY"


class EventVisibility(str, enum.Enum):
    ALL       = "ALL"       # Tous les membres
    GRADE     = "GRADE"     # Grade minimum requis
    FUNCTION  = "FUNCTION"  # Fonctions spécifiques


class AttendeeStatus(str, enum.Enum):
    INVITED   = "INVITED"
    CONFIRMED = "CONFIRMED"
    DECLINED  = "DECLINED"


class CalendarCategory(Base):
    """Catégories d'événements avec couleur."""
    __tablename__ = "calendar_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]   = mapped_column(String(100), unique=True)
    color: Mapped[str]  = mapped_column(String(10))   # ex: "#2563eb"
    icon: Mapped[Optional[str]] = mapped_column(String(50))
    description: Mapped[Optional[str]] = mapped_column(Text)
    order_position: Mapped[int] = mapped_column(default=0)

    events: Mapped[list["Event"]] = relationship(back_populates="category")

    def __repr__(self) -> str:
        return f"<CalendarCategory {self.name}>"


class Event(Base):
    """Événement calendrier avec gestion de la récurrence."""
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]              = mapped_column(String(300))
    description_html: Mapped[Optional[str]] = mapped_column(Text)
    category_id: Mapped[Optional[int]] = mapped_column(ForeignKey("calendar_categories.id"))

    date_start: Mapped[datetime] = mapped_column(DateTime)
    date_end: Mapped[Optional[datetime]] = mapped_column(DateTime)
    is_all_day: Mapped[bool] = mapped_column(Boolean, default=False)

    location: Mapped[Optional[str]]  = mapped_column(String(300))
    address: Mapped[Optional[str]]   = mapped_column(Text)
    visio_url: Mapped[Optional[str]] = mapped_column(String(500))

    # Récurrence
    recurrence_type: Mapped[RecurrenceType] = mapped_column(
        Enum(RecurrenceType), default=RecurrenceType.NONE
    )
    recurrence_end_date: Mapped[Optional[date]] = mapped_column(Date)
    recurrence_exceptions: Mapped[Optional[list]] = mapped_column(JSON)  # dates exclues

    # Visibilité
    visibility: Mapped[EventVisibility] = mapped_column(
        Enum(EventVisibility), default=EventVisibility.ALL
    )
    min_grade: Mapped[Optional[str]] = mapped_column(String(50))

    # Lien avec une tenue (si l'événement correspond à une tenue)
    linked_meeting_id: Mapped[Optional[int]] = mapped_column(ForeignKey("meetings.id"))

    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    category: Mapped[Optional["CalendarCategory"]] = relationship(back_populates="events")
    attendees: Mapped[list["EventAttendee"]] = relationship(back_populates="event")

    def __repr__(self) -> str:
        return f"<Event {self.title} [{self.date_start}]>"


class EventAttendee(Base):
    """Participant confirmé à un événement."""
    __tablename__ = "event_attendees"

    event_id: Mapped[int]  = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[AttendeeStatus] = mapped_column(Enum(AttendeeStatus), default=AttendeeStatus.INVITED)

    event: Mapped["Event"]   = relationship(back_populates="attendees")
    member: Mapped["Member"] = relationship()
