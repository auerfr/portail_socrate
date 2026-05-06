"""Domaine 4 — Programmes mensuels"""
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Boolean, DateTime, Date, Integer, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class Program(Base):
    """Programme mensuel — document maître contenant les liens d'inscription."""
    __tablename__ = "programs"

    id: Mapped[int] = mapped_column(primary_key=True)
    masonic_year_id: Mapped[int] = mapped_column(ForeignKey("masonic_years.id"))
    title: Mapped[str]   = mapped_column(String(300))
    month: Mapped[int]   = mapped_column(Integer)   # 1-12
    year: Mapped[int]    = mapped_column(Integer)

    content_html: Mapped[Optional[str]]    = mapped_column(Text)   # message du VM
    next_meetings_text: Mapped[Optional[str]] = mapped_column(Text)  # "À noter" — tenues à venir hors programme
    pdf_path: Mapped[Optional[str]]        = mapped_column(String(500))

    # Envoi
    sent_at: Mapped[Optional[datetime]]      = mapped_column(DateTime)
    sent_by_id: Mapped[Optional[int]]        = mapped_column(ForeignKey("members.id"))
    email_campaign_id: Mapped[Optional[int]] = mapped_column(ForeignKey("email_campaigns.id"))

    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime]  = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime]  = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    meetings: Mapped[list["ProgramMeeting"]] = relationship(back_populates="program")

    def __repr__(self) -> str:
        return f"<Program {self.title}>"


class ProgramMeeting(Base):
    """Tenues incluses dans un programme avec leur URL d'inscription."""
    __tablename__ = "program_meetings"

    program_id: Mapped[int] = mapped_column(
        ForeignKey("programs.id", ondelete="CASCADE"), primary_key=True
    )
    meeting_id: Mapped[int] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True
    )
    order_position: Mapped[int]           = mapped_column(Integer, default=0)
    registration_url: Mapped[Optional[str]] = mapped_column(String(500))  # généré depuis meeting.token

    program: Mapped["Program"]  = relationship(back_populates="meetings")
    meeting: Mapped["Meeting"]  = relationship()


class ReceivedProgram(Base):
    """Programmes reçus d'autres loges — invitations reçues."""
    __tablename__ = "received_programs"

    id: Mapped[int] = mapped_column(primary_key=True)
    from_lodge: Mapped[str]    = mapped_column(String(200))
    from_orient: Mapped[Optional[str]] = mapped_column(String(200))
    received_at: Mapped[datetime]      = mapped_column(Date)
    pdf_path: Mapped[Optional[str]]    = mapped_column(String(500))

    # Suivi de réponse
    response_sent: Mapped[bool]               = mapped_column(Boolean, default=False)
    response_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    response_by_id: Mapped[Optional[int]]     = mapped_column(ForeignKey("members.id"))
    notes: Mapped[Optional[str]]              = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<ReceivedProgram from {self.from_lodge}>"
