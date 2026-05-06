"""Domaine 11 — Projets & Commissions"""
import enum
from datetime import datetime, date
from typing import Optional
from sqlalchemy import String, Enum, DateTime, Date, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class ProjectType(str, enum.Enum):
    PROJECT    = "PROJECT"
    COMMISSION = "COMMISSION"


class ProjectStatus(str, enum.Enum):
    ACTIVE    = "ACTIVE"
    COMPLETED = "COMPLETED"
    ARCHIVED  = "ARCHIVED"


class ProjectMemberRole(str, enum.Enum):
    LEADER = "LEADER"
    MEMBER = "MEMBER"


class TaskStatus(str, enum.Enum):
    TODO        = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    DONE        = "DONE"
    CANCELLED   = "CANCELLED"


class TaskPriority(str, enum.Enum):
    LOW    = "LOW"
    MEDIUM = "MEDIUM"
    HIGH   = "HIGH"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]               = mapped_column(String(300))
    description: Mapped[Optional[str]] = mapped_column(Text)
    type: Mapped[ProjectType]       = mapped_column(Enum(ProjectType))
    status: Mapped[ProjectStatus]   = mapped_column(Enum(ProjectStatus), default=ProjectStatus.ACTIVE)
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    start_date: Mapped[Optional[date]] = mapped_column(Date)
    end_date: Mapped[Optional[date]]   = mapped_column(Date)

    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    members: Mapped[list["ProjectMember"]] = relationship(back_populates="project")
    tasks: Mapped[list["Task"]]             = relationship(back_populates="project")

    def __repr__(self) -> str:
        return f"<Project {self.name} [{self.type}]>"


class ProjectMember(Base):
    __tablename__ = "project_members"

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    member_id: Mapped[int]  = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[ProjectMemberRole] = mapped_column(Enum(ProjectMemberRole), default=ProjectMemberRole.MEMBER)
    joined_at: Mapped[datetime]     = mapped_column(DateTime, server_default=func.now())

    project: Mapped["Project"] = relationship(back_populates="members")
    member: Mapped["Member"]   = relationship()


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[Optional[int]] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    title: Mapped[str]               = mapped_column(String(300))
    description: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[TaskStatus]        = mapped_column(Enum(TaskStatus), default=TaskStatus.TODO)
    priority: Mapped[TaskPriority]    = mapped_column(Enum(TaskPriority), default=TaskPriority.MEDIUM)
    assigned_to_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    due_date: Mapped[Optional[date]]  = mapped_column(Date)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    project: Mapped[Optional["Project"]] = relationship(back_populates="tasks")
