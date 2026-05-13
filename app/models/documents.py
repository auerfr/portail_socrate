"""Domaine 6 — Bibliothèque documentaire (ex-Agora)"""
import enum
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Enum, Boolean, DateTime, Integer, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class DocAccessMode(str, enum.Enum):
    OPEN       = "OPEN"        # Accessible à tous les membres
    GRADE      = "GRADE"       # Accès par grade minimum
    INVITATION = "INVITATION"  # Sur invitation seulement


class DocStatus(str, enum.Enum):
    DRAFT     = "DRAFT"      # Brouillon
    SUBMITTED = "SUBMITTED"  # Soumis pour validation
    VALIDATED = "VALIDATED"  # Validé
    PUBLISHED = "PUBLISHED"  # Publié et visible


class MinGrade(str, enum.Enum):
    ALL       = "ALL"        # Tous les grades
    APPRENTI  = "APPRENTI"
    COMPAGNON = "COMPAGNON"
    MAITRE    = "MAITRE"


class DocSpace(Base):
    """Espace documentaire (ex: 'Bibliothèque', 'Secrétariat', 'Trésorerie')."""
    __tablename__ = "doc_spaces"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]        = mapped_column(String(200))
    description: Mapped[Optional[str]] = mapped_column(Text)
    access_mode: Mapped[DocAccessMode] = mapped_column(
        Enum(DocAccessMode), default=DocAccessMode.GRADE
    )
    min_grade: Mapped[MinGrade] = mapped_column(Enum(MinGrade), default=MinGrade.ALL)
    # Accès restreint à un groupe spécifique (prioritaire sur min_grade si défini)
    group_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("lodge_groups.id", ondelete="SET NULL"), nullable=True
    )
    is_public: Mapped[bool]     = mapped_column(Boolean, default=False)
    order_position: Mapped[int] = mapped_column(Integer, default=0)

    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    folders: Mapped[list["DocFolder"]] = relationship(
        back_populates="space",
        primaryjoin="DocSpace.id == DocFolder.space_id and DocFolder.parent_id == None"
    )

    def __repr__(self) -> str:
        return f"<DocSpace {self.name}>"


class DocFolder(Base):
    """Dossier hiérarchique avec droits d'accès par grade ou groupe."""
    __tablename__ = "doc_folders"

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int]          = mapped_column(ForeignKey("doc_spaces.id", ondelete="CASCADE"))
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("doc_folders.id"))

    name: Mapped[str]               = mapped_column(String(200))
    description: Mapped[Optional[str]] = mapped_column(Text)
    min_grade: Mapped[MinGrade]     = mapped_column(Enum(MinGrade), default=MinGrade.ALL)
    # Accès restreint à un groupe spécifique (prioritaire sur min_grade si défini)
    group_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("lodge_groups.id", ondelete="SET NULL"), nullable=True
    )
    order_position: Mapped[int]     = mapped_column(Integer, default=0)
    personal_owner_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE"), nullable=True
    )

    # ── Permissions granulaires (ajout Mai 2026) ──────────────────────────────
    # TÉLÉCHARGEMENT
    # - allow_download = False → lecture seule pour tout le monde (sauf admin)
    # - download_group_id défini → seul ce groupe peut télécharger
    # - sinon → tous ceux qui peuvent voir peuvent télécharger
    allow_download: Mapped[bool] = mapped_column(Boolean, default=True)
    download_group_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("lodge_groups.id", ondelete="SET NULL"), nullable=True
    )

    # ÉCRITURE (upload / édition / suppression)
    # - write_group_id défini → seul ce groupe peut modifier (+ admins)
    # - write_min_grade défini → grade minimum pour modifier
    # - les deux à None → héritage des droits de lecture (rétrocompat)
    write_group_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("lodge_groups.id", ondelete="SET NULL"), nullable=True
    )
    write_min_grade: Mapped[MinGrade] = mapped_column(
        Enum(MinGrade), default=MinGrade.ALL
    )

    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Relations
    space: Mapped["DocSpace"]           = relationship(back_populates="folders")
    children: Mapped[list["DocFolder"]] = relationship()
    documents: Mapped[list["Document"]] = relationship(back_populates="folder")

    def __repr__(self) -> str:
        return f"<DocFolder {self.name}>"


class Document(Base):
    """Document avec workflow de publication et versioning."""
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    folder_id: Mapped[int] = mapped_column(ForeignKey("doc_folders.id", ondelete="CASCADE"))

    name: Mapped[str]                  = mapped_column(String(300))
    description: Mapped[Optional[str]] = mapped_column(Text)
    original_filename: Mapped[Optional[str]] = mapped_column(String(300))
    mime_type: Mapped[Optional[str]]   = mapped_column(String(100))
    file_size: Mapped[Optional[int]]   = mapped_column(Integer)
    storage_path: Mapped[Optional[str]] = mapped_column(String(500))
    # Lien externe (Spotify, Apple Music, Deezer…) — exclusif avec storage_path
    link_url: Mapped[Optional[str]]    = mapped_column(String(2000))
    download_count: Mapped[int]        = mapped_column(Integer, default=0)

    # Workflow
    status: Mapped[DocStatus]        = mapped_column(Enum(DocStatus), default=DocStatus.DRAFT)
    author_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    validated_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    validated_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    folder: Mapped["DocFolder"]                 = relationship(back_populates="documents")
    versions: Mapped[list["DocumentVersion"]]   = relationship(back_populates="document")

    def __repr__(self) -> str:
        return f"<Document {self.name}>"


class DocumentVersion(Base):
    """Historique des versions d'un document."""
    __tablename__ = "document_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    version_number: Mapped[int] = mapped_column(Integer)
    storage_path: Mapped[str]   = mapped_column(String(500))
    file_size: Mapped[Optional[int]] = mapped_column(Integer)
    change_notes: Mapped[Optional[str]] = mapped_column(Text)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    document: Mapped["Document"] = relationship(back_populates="versions")


class DocShare(Base):
    """Lien de partage externe pour un document de la GED."""
    __tablename__ = "doc_shares"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    label: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # Contrôle d'accès externe
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    max_uses: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)   # None = illimité
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    password_hash: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    document: Mapped["Document"] = relationship()

    def __repr__(self) -> str:
        return f"<DocShare {self.token[:8]}… doc={self.document_id}>"
