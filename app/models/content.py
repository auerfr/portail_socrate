"""Domaine 14 — Contenu & Vie Sociale (Agora)
Actualités, Sondages, Contacts, Liens
"""
from datetime import datetime, date
from typing import Optional
from sqlalchemy import String, Boolean, DateTime, Date, Integer, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


# ── Actualités ─────────────────────────────────────────────────────────────

class NewsArticle(Base):
    """Actualité / annonce sur le tableau d'affichage."""
    __tablename__ = "news_articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]              = mapped_column(String(300))
    content_html: Mapped[str]       = mapped_column(Text)
    is_featured: Mapped[bool]       = mapped_column(Boolean, default=False)
    is_online: Mapped[bool]         = mapped_column(Boolean, default=True)
    publish_from: Mapped[Optional[datetime]] = mapped_column(DateTime)
    publish_until: Mapped[Optional[datetime]] = mapped_column(DateTime)
    min_grade: Mapped[Optional[str]] = mapped_column(String(50))

    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<NewsArticle {self.title}>"


# ── Sondages & Votes ───────────────────────────────────────────────────────

class Poll(Base):
    """Sondage ou vote."""
    __tablename__ = "polls"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]           = mapped_column(String(300))
    description: Mapped[Optional[str]] = mapped_column(Text)
    is_multiple: Mapped[bool]    = mapped_column(Boolean, default=False)  # multi-réponses
    is_anonymous: Mapped[bool]   = mapped_column(Boolean, default=False)
    is_public_vote: Mapped[bool] = mapped_column(Boolean, default=False)  # vote public
    ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    min_grade: Mapped[Optional[str]]    = mapped_column(String(50))

    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    options: Mapped[list["PollOption"]] = relationship(back_populates="poll")
    votes: Mapped[list["PollVote"]]     = relationship(back_populates="poll")


class PollOption(Base):
    __tablename__ = "poll_options"

    id: Mapped[int] = mapped_column(primary_key=True)
    poll_id: Mapped[int]    = mapped_column(ForeignKey("polls.id", ondelete="CASCADE"))
    label: Mapped[str]      = mapped_column(String(300))
    order_position: Mapped[int] = mapped_column(Integer, default=0)

    poll: Mapped["Poll"]           = relationship(back_populates="options")
    votes: Mapped[list["PollVote"]] = relationship(back_populates="option")


class PollVote(Base):
    __tablename__ = "poll_votes"

    id: Mapped[int] = mapped_column(primary_key=True)
    poll_id: Mapped[int]   = mapped_column(ForeignKey("polls.id", ondelete="CASCADE"))
    option_id: Mapped[int] = mapped_column(ForeignKey("poll_options.id", ondelete="CASCADE"))
    member_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))  # null si anonyme
    voted_at: Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())

    poll: Mapped["Poll"]     = relationship(back_populates="votes")
    option: Mapped["PollOption"] = relationship(back_populates="votes")


# ── Contacts ───────────────────────────────────────────────────────────────

class ContactFolder(Base):
    """Dossiers hiérarchiques pour l'annuaire."""
    __tablename__ = "contact_folders"

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("contact_folders.id"))
    name: Mapped[str]                = mapped_column(String(200))
    min_grade: Mapped[Optional[str]] = mapped_column(String(50))
    order_position: Mapped[int]      = mapped_column(Integer, default=0)

    children: Mapped[list["ContactFolder"]] = relationship()
    contacts: Mapped[list["Contact"]]       = relationship(back_populates="folder")


class Contact(Base):
    """Contact dans l'annuaire (loges amies, prestataires, institutions)."""
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    folder_id: Mapped[Optional[int]] = mapped_column(ForeignKey("contact_folders.id"))

    civility: Mapped[Optional[str]] = mapped_column(String(10))
    last_name: Mapped[str]          = mapped_column(String(100))
    first_name: Mapped[Optional[str]] = mapped_column(String(100))
    company: Mapped[Optional[str]]  = mapped_column(String(200))
    function: Mapped[Optional[str]] = mapped_column(String(200))
    address: Mapped[Optional[str]]  = mapped_column(Text)
    phone: Mapped[Optional[str]]    = mapped_column(String(30))
    email: Mapped[Optional[str]]    = mapped_column(String(200))
    website: Mapped[Optional[str]]  = mapped_column(String(500))
    notes: Mapped[Optional[str]]    = mapped_column(Text)

    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    folder: Mapped[Optional["ContactFolder"]] = relationship(back_populates="contacts")

    def __repr__(self) -> str:
        return f"<Contact {self.last_name} {self.first_name or ''}>"


# ── Liens partagés ─────────────────────────────────────────────────────────

class LinkFolder(Base):
    __tablename__ = "link_folders"

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("link_folders.id"))
    name: Mapped[str]                = mapped_column(String(200))
    min_grade: Mapped[Optional[str]] = mapped_column(String(50))
    order_position: Mapped[int]      = mapped_column(Integer, default=0)

    children: Mapped[list["LinkFolder"]] = relationship()
    links: Mapped[list["SharedLink"]]    = relationship(back_populates="folder")


class SharedLink(Base):
    """Lien / ressource partagée."""
    __tablename__ = "shared_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    folder_id: Mapped[Optional[int]] = mapped_column(ForeignKey("link_folders.id"))
    title: Mapped[str]               = mapped_column(String(300))
    url: Mapped[str]                 = mapped_column(String(1000))
    description: Mapped[Optional[str]] = mapped_column(Text)
    min_grade: Mapped[Optional[str]]   = mapped_column(String(50))

    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    folder: Mapped[Optional["LinkFolder"]] = relationship(back_populates="links")
