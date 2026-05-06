"""Domaine 13 — Vie Associative Maçonnique
Candidats, Enquêtes, Tableau de Loge, Bilan Moral, Visites
"""
import enum
from datetime import datetime, date
from typing import Optional
from sqlalchemy import String, Enum, Boolean, DateTime, Date, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class CandidateStatus(str, enum.Enum):
    PENDING      = "PENDING"       # En attente d'enquête
    ENQUIRY      = "ENQUIRY"       # Enquête en cours
    VOTED        = "VOTED"         # Voté en tenue
    ACCEPTED     = "ACCEPTED"      # Accepté, initiation à planifier
    INITIATED    = "INITIATED"     # Initié
    REJECTED     = "REJECTED"      # Rejeté
    WITHDRAWN    = "WITHDRAWN"     # Candidature retirée


class EnquiryResult(str, enum.Enum):
    FAVORABLE   = "FAVORABLE"
    UNFAVORABLE = "UNFAVORABLE"
    RESERVED    = "RESERVED"


class MoralReportStatus(str, enum.Enum):
    DRAFT     = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED  = "APPROVED"


class VoteResult(str, enum.Enum):
    FOR       = "FOR"
    AGAINST   = "AGAINST"
    ABSTAIN   = "ABSTAIN"


# ── Candidats ──────────────────────────────────────────────────────────────

class Candidate(Base):
    """Profane candidat à l'initiation."""
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Identité civile
    civility: Mapped[Optional[str]]  = mapped_column(String(10))
    last_name: Mapped[str]           = mapped_column(String(100))
    first_name: Mapped[str]          = mapped_column(String(100))
    email: Mapped[Optional[str]]     = mapped_column(String(200))
    phone: Mapped[Optional[str]]     = mapped_column(String(30))
    birth_date: Mapped[Optional[date]] = mapped_column(Date)
    profession: Mapped[Optional[str]]  = mapped_column(String(200))
    address: Mapped[Optional[str]]     = mapped_column(Text)

    # Parrainage
    sponsor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    co_sponsor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))

    # Suivi
    status: Mapped[CandidateStatus] = mapped_column(
        Enum(CandidateStatus), default=CandidateStatus.PENDING
    )
    presentation_date: Mapped[Optional[date]] = mapped_column(Date)  # présenté en tenue
    initiation_date: Mapped[Optional[date]]   = mapped_column(Date)
    notes: Mapped[Optional[str]]              = mapped_column(Text)

    # Lien membre créé après initiation
    member_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    enquiries: Mapped[list["Enquiry"]] = relationship(back_populates="candidate")

    def __repr__(self) -> str:
        return f"<Candidate {self.last_name} {self.first_name} [{self.status}]>"


class Enquiry(Base):
    """Rapport d'enquête d'un frère sur un candidat."""
    __tablename__ = "enquiries"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int]  = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"))
    enquirer_id: Mapped[int]   = mapped_column(ForeignKey("members.id"))

    result: Mapped[Optional[EnquiryResult]] = mapped_column(Enum(EnquiryResult))
    report_html: Mapped[Optional[str]]      = mapped_column(Text)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    candidate: Mapped["Candidate"] = relationship(back_populates="enquiries")

    def __repr__(self) -> str:
        return f"<Enquiry candidate={self.candidate_id} enquirer={self.enquirer_id}>"


# ── Tableau de Loge ────────────────────────────────────────────────────────

class OfficerAssignment(Base):
    """
    Tableau de Loge officiel — officiers par année maçonnique.
    Historique complet des fonctions.
    """
    __tablename__ = "officer_assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    masonic_year_id: Mapped[int] = mapped_column(ForeignKey("masonic_years.id"))
    member_id: Mapped[int]       = mapped_column(ForeignKey("members.id"))
    function: Mapped[str]        = mapped_column(String(100))  # VM, 1S, 2S, etc.
    investiture_date: Mapped[Optional[date]] = mapped_column(Date)
    end_date: Mapped[Optional[date]]         = mapped_column(Date)
    is_current: Mapped[bool]                 = mapped_column(Boolean, default=True)
    notes: Mapped[Optional[str]]             = mapped_column(Text)

    def __repr__(self) -> str:
        return f"<OfficerAssignment {self.function} member={self.member_id} year={self.masonic_year_id}>"


# ── Bilan Moral ────────────────────────────────────────────────────────────

class MoralReport(Base):
    """Bilan moral annuel — document formel structuré."""
    __tablename__ = "moral_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    masonic_year_id: Mapped[int] = mapped_column(ForeignKey("masonic_years.id"), unique=True)

    # Sections structurées
    section_opening: Mapped[Optional[str]]     = mapped_column(Text)  # Mot d'ouverture VM
    section_attendance: Mapped[Optional[str]]  = mapped_column(Text)  # Stats (auto-calculées)
    section_works: Mapped[Optional[str]]       = mapped_column(Text)  # Travaux de l'année
    section_initiations: Mapped[Optional[str]] = mapped_column(Text)  # Initiations/passages/élévations
    section_projects: Mapped[Optional[str]]    = mapped_column(Text)  # Projets et commissions
    section_financial: Mapped[Optional[str]]   = mapped_column(Text)  # Résumé financier
    section_closing: Mapped[Optional[str]]     = mapped_column(Text)  # Conclusion

    status: Mapped[MoralReportStatus] = mapped_column(
        Enum(MoralReportStatus), default=MoralReportStatus.DRAFT
    )
    approved_by_id: Mapped[Optional[int]]   = mapped_column(ForeignKey("members.id"))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    pdf_path: Mapped[Optional[str]]         = mapped_column(String(500))

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<MoralReport year={self.masonic_year_id} [{self.status}]>"


# ── Registre des visites ───────────────────────────────────────────────────

class LodgeVisit(Base):
    """
    Visite d'un frère de la loge chez une autre loge.
    Complément de meeting_visitors (qui gère les visites entrantes).
    """
    __tablename__ = "lodge_visits"

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))

    visited_lodge: Mapped[str]         = mapped_column(String(200))
    visited_orient: Mapped[Optional[str]] = mapped_column(String(200))
    visited_obedience: Mapped[Optional[str]] = mapped_column(String(200))
    visit_date: Mapped[date]           = mapped_column(Date)
    meeting_grade: Mapped[Optional[str]] = mapped_column(String(50))
    notes: Mapped[Optional[str]]       = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<LodgeVisit {self.member_id} → {self.visited_lodge} [{self.visit_date}]>"
