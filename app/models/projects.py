"""Domaine 11 — Projets & Commissions"""
import enum
from datetime import datetime, date
from typing import Optional
from sqlalchemy import String, Enum, DateTime, Date, ForeignKey, Integer, Text, func
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
    color: Mapped[Optional[str]]    = mapped_column(String(10))
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
    assigned_to_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"))
    assigned_to_group_id: Mapped[Optional[int]] = mapped_column(ForeignKey("lodge_groups.id", ondelete="SET NULL"))
    progress: Mapped[int]              = mapped_column(Integer, default=0)
    start_date: Mapped[Optional[date]] = mapped_column(Date)
    due_date: Mapped[Optional[date]]   = mapped_column(Date)
    order_position: Mapped[int]        = mapped_column(Integer, default=0)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Lien forum & jalons
    forum_subject_id: Mapped[Optional[int]] = mapped_column(ForeignKey("forum_subjects.id", ondelete="SET NULL"))
    is_milestone: Mapped[bool]              = mapped_column(Integer, default=0)  # SQLite Boolean

    # Sous-tâches : hiérarchie via parent_task_id
    parent_task_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )

    # Notifications
    reminded_at: Mapped[Optional[datetime]] = mapped_column(DateTime)  # dernier rappel J-3 envoyé

    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    project: Mapped[Optional["Project"]] = relationship(back_populates="tasks")
    comments: Mapped[list["TaskComment"]] = relationship(
        back_populates="task", cascade="all, delete-orphan",
        order_by="TaskComment.created_at",
    )


class TaskDependency(Base):
    """Dépendance "finish-to-start" : `successor` ne peut commencer
    tant que `predecessor` n'est pas terminé."""
    __tablename__ = "task_dependencies"

    predecessor_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    successor_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TaskComment(Base):
    __tablename__ = "task_comments"

    id: Mapped[int]  = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"), nullable=True)
    content: Mapped[str]   = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    task: Mapped["Task"] = relationship(back_populates="comments")


class ProjectActivity(Base):
    """Journal d'activité d'un projet."""
    __tablename__ = "project_activities"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"))
    action: Mapped[str]   = mapped_column(String(50))   # CREATE_TASK, EDIT_TASK, COMMENT, STATUS, …
    target: Mapped[Optional[str]] = mapped_column(String(300))  # libellé (titre tâche, etc.)
    details: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ProjectTemplate(Base):
    """Modèle de projet pour générer rapidement une commission/chantier récurrent."""
    __tablename__ = "project_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]               = mapped_column(String(200))
    description: Mapped[Optional[str]] = mapped_column(Text)
    color: Mapped[Optional[str]]    = mapped_column(String(10))
    type: Mapped[ProjectType]       = mapped_column(Enum(ProjectType), default=ProjectType.PROJECT)

    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    tasks: Mapped[list["ProjectTemplateTask"]] = relationship(
        back_populates="template", cascade="all, delete-orphan",
        order_by="ProjectTemplateTask.order_position",
    )


class ProjectTemplateTask(Base):
    __tablename__ = "project_template_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("project_templates.id", ondelete="CASCADE"), index=True)
    title: Mapped[str]                 = mapped_column(String(300))
    description: Mapped[Optional[str]] = mapped_column(Text)
    priority: Mapped[TaskPriority]     = mapped_column(Enum(TaskPriority), default=TaskPriority.MEDIUM)
    offset_days_start: Mapped[Optional[int]] = mapped_column(Integer)  # vs date de création
    offset_days_due: Mapped[Optional[int]]   = mapped_column(Integer)
    order_position: Mapped[int] = mapped_column(Integer, default=0)

    template: Mapped["ProjectTemplate"] = relationship(back_populates="tasks")
