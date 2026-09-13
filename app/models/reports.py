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
    APPROUVE  = "APPROUVE"    # Approuvé par le V∴M∴, corps narratif archivé (document de travail)
    ADOPTE    = "ADOPTE"      # Lu et adopté en tenue (déclaration Secrétaire) — en attente du PDF signé
    ARCHIVE   = "ARCHIVE"     # PDF signé (scan) importé — pièce officielle


class MeetingReport(Base):
    """Statut d'approbation/archivage du tracé d'une tenue (le contenu narratif
    vit désormais sur Meeting.compte_rendu_html — tracé et PV sont un seul document)."""
    __tablename__ = "meeting_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    meeting_id: Mapped[int] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), unique=True, index=True
    )
    content: Mapped[Optional[str]] = mapped_column(Text)  # legacy, non utilisé
    status: Mapped[ReportStatus] = mapped_column(
        Enum(ReportStatus), default=ReportStatus.BROUILLON
    )

    author_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, onupdate=func.now())

    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    approved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Dernière consultation par le V∴M∴ (ou un admin) pendant que le tracé est
    # SOUMIS — permet à la Secrétaire de savoir si elle doit relancer. Remis à
    # None dès que le corps du tracé est modifié après soumission (la lecture
    # précédente ne porte plus sur la version actuelle).
    viewed_by_vm_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    viewed_by_vm_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"), nullable=True)

    # Motif indiqué par le V∴M∴ en renvoyant le tracé en brouillon — remis à
    # None dès la nouvelle soumission (la Secrétaire l'a déjà vu et traité).
    reject_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rejected_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Lien vers le document GED archivé (corps narratif HTML — "PDF de travail")
    archived_doc_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )

    # Déclaration "lu et adopté en tenue" par la Secrétaire (pas de workflow
    # de vote — simple constat) : étape en aval de l'approbation V∴M∴, avant
    # l'import du PDF signé.
    adopted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    adopted_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"), nullable=True)

    # Délégation ponctuelle du droit d'importer le PDF signé à un membre —
    # ne vaut que pour CE tracé, n'accorde aucun autre droit, ne modifie pas
    # le rôle général du membre désigné.
    upload_delegate_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"), nullable=True)

    # PDF scanné signé (pièce officielle qui fait autorité) — stocké comme un
    # Document GED normal dans le même dossier "PV {année}" que le document
    # de travail, pour hériter automatiquement des mêmes règles de visibilité
    # (grade de la tenue) sans dupliquer de logique d'accès.
    signed_pdf_doc_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    signed_pdf_uploaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    signed_pdf_uploaded_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"), nullable=True)

    meeting: Mapped[Optional[object]] = relationship(
        "Meeting", foreign_keys=[meeting_id], lazy="selectin"
    )
    author: Mapped[Optional[object]] = relationship(
        "Member", foreign_keys=[author_id], lazy="selectin"
    )
    approved_by: Mapped[Optional[object]] = relationship(
        "Member", foreign_keys=[approved_by_id], lazy="selectin"
    )
    viewed_by_vm: Mapped[Optional[object]] = relationship(
        "Member", foreign_keys=[viewed_by_vm_id], lazy="selectin"
    )
    adopted_by: Mapped[Optional[object]] = relationship(
        "Member", foreign_keys=[adopted_by_id], lazy="selectin"
    )
    upload_delegate: Mapped[Optional[object]] = relationship(
        "Member", foreign_keys=[upload_delegate_id], lazy="selectin"
    )
    signed_pdf_uploaded_by: Mapped[Optional[object]] = relationship(
        "Member", foreign_keys=[signed_pdf_uploaded_by_id], lazy="selectin"
    )
    signed_pdf_doc: Mapped[Optional[object]] = relationship(
        "Document", foreign_keys=[signed_pdf_doc_id], lazy="selectin"
    )
