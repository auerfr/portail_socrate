"""Domaine — Groupes de membres (communication, agenda, droits)"""
import enum
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    String, Enum, Boolean, DateTime, ForeignKey, Text,
    UniqueConstraint, func, Integer,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class GroupType(str, enum.Enum):
    # ── Groupes système (lecture seule, membership calculé dynamiquement) ──
    GRADE    = "GRADE"     # Par grade maçonnique
    COUNCIL  = "COUNCIL"   # Conseil d'officiers (toutes fonctions sauf FRERE)
    PAIR     = "PAIR"      # Binôme prédéfini (ex: VM+Trésorier, VM+Secrétaire)
    # ── Groupes personnalisés (membership explicite en DB) ──
    COMMISSION = "COMMISSION"  # Commission (finance, solidarité…)
    CUSTOM     = "CUSTOM"      # Groupe libre créé par un officier


# Identifiants des groupes système prédéfinis (slugs stables)
SYSTEM_GROUPS = {
    "all":          {"name": "Tous les membres",      "type": GroupType.GRADE,   "grade_filter": None},
    "maitres":      {"name": "Maîtres",               "type": GroupType.GRADE,   "grade_filter": "MAITRE"},
    "compagnons":   {"name": "Compagnons",             "type": GroupType.GRADE,   "grade_filter": "COMPAGNON"},
    "apprentis":    {"name": "Apprentis",              "type": GroupType.GRADE,   "grade_filter": "APPRENTI"},
    "conseil":      {"name": "Conseil d'officiers",    "type": GroupType.COUNCIL, "grade_filter": None},
    "tresorerie":   {"name": "Trésorerie",             "type": GroupType.PAIR,    "functions": ["VM", "TRESORIER"]},
    "secretariat":  {"name": "Secrétariat",            "type": GroupType.PAIR,    "functions": ["VM", "SECRETAIRE"]},
}


class LodgeGroup(Base):
    """Groupe de membres — système ou personnalisé."""
    __tablename__ = "lodge_groups"

    id: Mapped[int]           = mapped_column(primary_key=True)
    slug: Mapped[Optional[str]] = mapped_column(String(80), unique=True)  # identifiant stable pour groupes système
    name: Mapped[str]         = mapped_column(String(200))
    description: Mapped[Optional[str]] = mapped_column(Text)
    color: Mapped[Optional[str]] = mapped_column(String(20), default="#3b5bdb")  # hex color pour badge

    group_type: Mapped[GroupType] = mapped_column(Enum(GroupType))
    is_system: Mapped[bool]      = mapped_column(Boolean, default=False)  # True = géré par le code, pas éditable

    # Pour groupes GRADE : filtre sur masonic_grade (None = tous)
    grade_filter: Mapped[Optional[str]] = mapped_column(String(20))
    # Pour groupes PAIR/COUNCIL : JSON list de fonctions
    function_filter: Mapped[Optional[str]] = mapped_column(Text)  # JSON ["VM","TRESORIER"]

    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime]         = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime]         = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    memberships: Mapped[list["GroupMembership"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<LodgeGroup '{self.name}' [{self.group_type}]>"


class GroupMembership(Base):
    """Appartenance explicite d'un membre à un groupe personnalisé."""
    __tablename__ = "lodge_group_memberships"

    id: Mapped[int]        = mapped_column(primary_key=True)
    group_id: Mapped[int]  = mapped_column(ForeignKey("lodge_groups.id", ondelete="CASCADE"))
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"))
    added_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    added_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    group: Mapped["LodgeGroup"] = relationship(back_populates="memberships")

    __table_args__ = (
        UniqueConstraint("group_id", "member_id", name="uq_group_member"),
    )

    def __repr__(self) -> str:
        return f"<GroupMembership group={self.group_id} member={self.member_id}>"
