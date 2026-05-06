"""Domaine 1 — Identité & Accès"""
import enum
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Enum, Boolean, DateTime, Date, Integer, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class MasonicGrade(str, enum.Enum):
    APPRENTI  = "APPRENTI"
    COMPAGNON = "COMPAGNON"
    MAITRE    = "MAITRE"


class MemberStatus(str, enum.Enum):
    ACTIVE   = "ACTIVE"
    LEAVE    = "LEAVE"
    RESIGNED = "RESIGNED"
    STRUCK   = "STRUCK"
    DECEASED = "DECEASED"


class LodgeFunction(str, enum.Enum):
    VM               = "VM"
    PREMIER_S        = "PREMIER_S"
    SECOND_S         = "SECOND_S"
    ORATEUR          = "ORATEUR"
    SECRETAIRE       = "SECRETAIRE"
    TRESORIER        = "TRESORIER"
    EXPERT           = "EXPERT"
    MAITRE_CEREMONIES = "MAITRE_CEREMONIES"
    HOSPITALIER      = "HOSPITALIER"
    TUILEUR          = "TUILEUR"
    ARCHITECTE       = "ARCHITECTE"
    MAITRE_BANQUETS  = "MAITRE_BANQUETS"
    FRERE            = "FRERE"


class GroupType(str, enum.Enum):
    GRADE       = "GRADE"
    FUNCTION    = "FUNCTION"
    COMMISSION  = "COMMISSION"
    STATIC      = "STATIC"
    CPANEL_LIST = "CPANEL_LIST"


class AccessLevel(str, enum.Enum):
    READ    = "READ"
    COMMENT = "COMMENT"
    WRITE   = "WRITE"
    ADMIN   = "ADMIN"


class PrincipalType(str, enum.Enum):
    MEMBER   = "MEMBER"
    GROUP    = "GROUP"
    GRADE    = "GRADE"
    FUNCTION = "FUNCTION"


class ResourceType(str, enum.Enum):
    DOC_SPACE   = "DOC_SPACE"
    DOC_FOLDER  = "DOC_FOLDER"
    DOCUMENT    = "DOCUMENT"
    CHANNEL     = "CHANNEL"
    FORUM_THEME = "FORUM_THEME"


class Member(Base):
    __tablename__ = "members"

    id: Mapped[int] = mapped_column(primary_key=True)
    civility: Mapped[Optional[str]]        = mapped_column(String(10))
    last_name: Mapped[str]                 = mapped_column(String(100), index=True)
    first_name: Mapped[str]                = mapped_column(String(100))
    email: Mapped[str]                     = mapped_column(String(200), unique=True, index=True)
    phone: Mapped[Optional[str]]           = mapped_column(String(30))
    birth_date: Mapped[Optional[datetime]] = mapped_column(Date)
    photo_url: Mapped[Optional[str]]       = mapped_column(String(500))

    masonic_grade: Mapped[MasonicGrade] = mapped_column(Enum(MasonicGrade), default=MasonicGrade.APPRENTI, index=True)
    status: Mapped[MemberStatus]        = mapped_column(Enum(MemberStatus), default=MemberStatus.ACTIVE, index=True)
    status_date: Mapped[Optional[datetime]]     = mapped_column(Date)
    initiation_date: Mapped[Optional[datetime]] = mapped_column(Date)
    companion_date: Mapped[Optional[datetime]]  = mapped_column(Date)
    master_date: Mapped[Optional[datetime]]     = mapped_column(Date)

    lodge_function: Mapped[LodgeFunction]       = mapped_column(Enum(LodgeFunction), default=LodgeFunction.FRERE)
    function_start_date: Mapped[Optional[datetime]] = mapped_column(Date)
    function_end_date: Mapped[Optional[datetime]]   = mapped_column(Date)

    pin_code_hash: Mapped[Optional[str]] = mapped_column(String(200))
    program_optin: Mapped[bool]          = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped[Optional["User"]]                  = relationship(back_populates="member", uselist=False)
    group_memberships: Mapped[list["GroupMember"]]  = relationship(back_populates="member")

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def is_active(self) -> bool:
        return self.status == MemberStatus.ACTIVE


class User(Base):
    __tablename__ = "users"

    id: Mapped[int]        = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), unique=True)
    login: Mapped[str]         = mapped_column(String(100), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool]    = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool]     = mapped_column(Boolean, default=False)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    member: Mapped["Member"] = relationship(back_populates="user")


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int]   = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    type: Mapped[GroupType] = mapped_column(Enum(GroupType))

    grade_filter: Mapped[Optional[str]]    = mapped_column(String(50))
    function_filter: Mapped[Optional[str]] = mapped_column(String(50))
    cpanel_address: Mapped[Optional[str]]  = mapped_column(String(200))
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    is_auto: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    members: Mapped[list["GroupMember"]] = relationship(back_populates="group")


class GroupMember(Base):
    __tablename__ = "group_members"
    __table_args__ = (UniqueConstraint("group_id", "member_id"),)

    group_id: Mapped[int]  = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    group: Mapped["Group"]   = relationship(back_populates="members")
    member: Mapped["Member"] = relationship(back_populates="group_memberships")


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    resource_type: Mapped[ResourceType]   = mapped_column(Enum(ResourceType))
    resource_id: Mapped[int]              = mapped_column(Integer, index=True)
    principal_type: Mapped[PrincipalType] = mapped_column(Enum(PrincipalType))
    principal_id: Mapped[int]             = mapped_column(Integer)
    access_level: Mapped[AccessLevel]     = mapped_column(Enum(AccessLevel))
    created_at: Mapped[datetime]          = mapped_column(DateTime, server_default=func.now())
