"""Domaine 5 — Cotisations & Finance"""
import enum
from datetime import datetime, date
from typing import Optional
from sqlalchemy import (
    String, Enum, Boolean, DateTime, Date, Integer,
    Numeric, ForeignKey, Text, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class ContributionStatus(str, enum.Enum):
    PENDING = "PENDING"   # En attente
    PARTIAL = "PARTIAL"   # Paiement partiel
    PAID    = "PAID"      # Soldé
    EXEMPT  = "EXEMPT"    # Exempté


class PaymentMethod(str, enum.Enum):
    CASH     = "CASH"
    TRANSFER = "TRANSFER"
    CHECK    = "CHECK"
    OTHER    = "OTHER"


class TransactionType(str, enum.Enum):
    INCOME  = "INCOME"
    EXPENSE = "EXPENSE"


class BudgetLineType(str, enum.Enum):
    CHARGE_FIXE  = "CHARGE_FIXE"   # Local, assurances, etc.
    CAPITATION   = "CAPITATION"    # Nationale + régionale
    PROJET       = "PROJET"        # Projets spécifiques
    RESERVE      = "RESERVE"       # Réserve
    AUTRE        = "AUTRE"


class ReportStatus(str, enum.Enum):
    DRAFT    = "DRAFT"
    APPROVED = "APPROVED"


# ── Budget ─────────────────────────────────────────────────────────────────

class BudgetLine(Base):
    """
    Ligne du budget prévisionnel.
    Le montant de référence T3 est CALCULÉ depuis ces lignes,
    pas saisi manuellement.
    """
    __tablename__ = "budget_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    masonic_year_id: Mapped[int] = mapped_column(ForeignKey("masonic_years.id"))
    label: Mapped[str]           = mapped_column(String(300))
    type: Mapped[BudgetLineType] = mapped_column(Enum(BudgetLineType))
    amount: Mapped[float]        = mapped_column(Numeric(10, 2))
    order_position: Mapped[int]  = mapped_column(Integer, default=0)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    def __repr__(self) -> str:
        return f"<BudgetLine {self.label} {self.amount}€>"


class ContributionConfig(Base):
    """
    Configuration des cotisations pour une année.
    reference_amount = calculé automatiquement depuis budget_lines.
    """
    __tablename__ = "contribution_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    masonic_year_id: Mapped[int] = mapped_column(ForeignKey("masonic_years.id"), unique=True)

    # Montant calculé depuis le budget (mis à jour quand budget change)
    reference_amount: Mapped[float]         = mapped_column(Numeric(10, 2))
    national_capitation_rate: Mapped[float] = mapped_column(Numeric(10, 2))
    regional_capitation_rate: Mapped[float] = mapped_column(Numeric(10, 2))
    active_members_count: Mapped[int]        = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    tiers: Mapped[list["ContributionTier"]] = relationship(back_populates="config")

    def __repr__(self) -> str:
        return f"<ContributionConfig ref={self.reference_amount}€>"


class ContributionTier(Base):
    """Les 5 tranches de cotisation (T1 à T5)."""
    __tablename__ = "contribution_tiers"

    id: Mapped[int] = mapped_column(primary_key=True)
    config_id: Mapped[int] = mapped_column(ForeignKey("contribution_configs.id", ondelete="CASCADE"))
    tier_number: Mapped[int]   = mapped_column(Integer)     # 1 à 5
    label: Mapped[str]         = mapped_column(String(100)) # "Très aménagée"
    coefficient: Mapped[float] = mapped_column(Numeric(5, 2))
    # amount = reference_amount × coefficient (calculé)
    amount: Mapped[float]      = mapped_column(Numeric(10, 2))

    config: Mapped["ContributionConfig"] = relationship(back_populates="tiers")

    def __repr__(self) -> str:
        return f"<ContributionTier T{self.tier_number} ×{self.coefficient} = {self.amount}€>"


class MemberContribution(Base):
    """Cotisation assignée à un membre pour une année."""
    __tablename__ = "member_contributions"

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int]       = mapped_column(ForeignKey("members.id"))
    masonic_year_id: Mapped[int] = mapped_column(ForeignKey("masonic_years.id"))
    tier_id: Mapped[int]         = mapped_column(ForeignKey("contribution_tiers.id"))

    base_amount: Mapped[float]       = mapped_column(Numeric(10, 2))  # cotisation pure
    capitation_amount: Mapped[float] = mapped_column(Numeric(10, 2))  # nat + régionale
    total_amount: Mapped[float]      = mapped_column(Numeric(10, 2))  # total dû

    status: Mapped[ContributionStatus] = mapped_column(
        Enum(ContributionStatus), default=ContributionStatus.PENDING
    )
    due_date: Mapped[Optional[date]] = mapped_column(Date)
    notes: Mapped[Optional[str]]     = mapped_column(Text)

    # Relations
    payments: Mapped[list["Payment"]] = relationship(back_populates="contribution")
    quitus: Mapped[Optional["Quitus"]] = relationship(back_populates="contribution", uselist=False)

    def __repr__(self) -> str:
        return f"<MemberContribution member={self.member_id} year={self.masonic_year_id} [{self.status}]>"

    @property
    def amount_paid(self) -> float:
        return sum(p.amount for p in self.payments)

    @property
    def amount_remaining(self) -> float:
        return float(self.total_amount) - self.amount_paid


class Payment(Base):
    """Paiement reçu pour une cotisation."""
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    member_contribution_id: Mapped[int] = mapped_column(
        ForeignKey("member_contributions.id", ondelete="CASCADE")
    )
    amount: Mapped[float]         = mapped_column(Numeric(10, 2))
    payment_date: Mapped[date]    = mapped_column(Date)
    method: Mapped[PaymentMethod] = mapped_column(Enum(PaymentMethod))
    reference: Mapped[Optional[str]] = mapped_column(String(200))
    notes: Mapped[Optional[str]]     = mapped_column(Text)
    recorded_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    contribution: Mapped["MemberContribution"] = relationship(back_populates="payments")

    def __repr__(self) -> str:
        return f"<Payment {self.amount}€ [{self.method}]>"


class Quitus(Base):
    """Décharge formelle — ce membre est en règle de cotisation."""
    __tablename__ = "quitus"

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int]       = mapped_column(ForeignKey("members.id"))
    masonic_year_id: Mapped[int] = mapped_column(ForeignKey("masonic_years.id"))
    contribution_id: Mapped[int] = mapped_column(
        ForeignKey("member_contributions.id"), unique=True
    )

    issued_at: Mapped[datetime]  = mapped_column(DateTime, server_default=func.now())
    issued_by_id: Mapped[int]    = mapped_column(ForeignKey("members.id"))
    valid_until: Mapped[Optional[date]] = mapped_column(Date)
    pdf_path: Mapped[Optional[str]]     = mapped_column(String(500))
    notes: Mapped[Optional[str]]        = mapped_column(Text)

    contribution: Mapped["MemberContribution"] = relationship(back_populates="quitus")

    def __repr__(self) -> str:
        return f"<Quitus member={self.member_id} year={self.masonic_year_id}>"


class BudgetCategory(Base):
    """Catégories de budget pour la comptabilité."""
    __tablename__ = "budget_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    masonic_year_id: Mapped[int] = mapped_column(ForeignKey("masonic_years.id"))
    name: Mapped[str]             = mapped_column(String(200))
    type: Mapped[TransactionType] = mapped_column(Enum(TransactionType))
    planned_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0)

    transactions: Mapped[list["Transaction"]] = relationship(back_populates="category")


class Transaction(Base):
    """Dépense ou recette."""
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    masonic_year_id: Mapped[int]  = mapped_column(ForeignKey("masonic_years.id"))
    category_id: Mapped[Optional[int]] = mapped_column(ForeignKey("budget_categories.id"))

    date: Mapped[date]              = mapped_column(Date)
    label: Mapped[str]              = mapped_column(String(300))
    amount: Mapped[float]           = mapped_column(Numeric(10, 2))
    type: Mapped[TransactionType]   = mapped_column(Enum(TransactionType))
    attachment_url: Mapped[Optional[str]] = mapped_column(String(500))
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime]    = mapped_column(DateTime, server_default=func.now())

    category: Mapped[Optional["BudgetCategory"]] = relationship(back_populates="transactions")


class AccountingReport(Base):
    """Bilan comptable annuel formel."""
    __tablename__ = "accounting_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    masonic_year_id: Mapped[int] = mapped_column(ForeignKey("masonic_years.id"), unique=True)
    content_html: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[ReportStatus]         = mapped_column(Enum(ReportStatus), default=ReportStatus.DRAFT)
    approved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    pdf_path: Mapped[Optional[str]]          = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
