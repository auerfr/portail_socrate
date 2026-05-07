"""
Fix : supprime et recrée la table lodge_offices proprement,
puis insère les offices par défaut (idempotent).

Usage : python fix_lodge_offices.py
"""
import asyncio
import os
import sys

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from app.config import get_settings
from app.database import Base
# Importer TOUS les modèles pour que Base.metadata les connaisse
from app.models.identity import Member, User, MasonicGrade, MemberStatus, LodgeFunction, Group, GroupType
from app.models.lodge import MasonicYear, LodgeSettings, LodgeOffice
from app.models.meetings import Meeting, Attendance, Visitor, MeetingVisitor

settings = get_settings()

DEFAULT_OFFICES = [
    ("V\u2234M\u2234 \u2014 V\u00e9n\u00e9rable Ma\u00eetre",       10),
    ("1er S\u2234 \u2014 Premier Surveillant",                        20),
    ("2e S\u2234 \u2014 Second Surveillant",                          30),
    ("Or\u2234 \u2014 Orateur",                                       40),
    ("Sec\u2234 \u2014 Secr\u00e9taire",                              50),
    ("Tr\u00e9so\u2234 \u2014 Tr\u00e9sorier",                        60),
    ("Expert",                                                        70),
    ("M\u2234C\u2234 \u2014 Ma\u00eetre des C\u00e9r\u00e9monies",    80),
    ("M\u2234H\u2234 \u2014 Ma\u00eetre Harmoniste",                  90),
    ("Hosp\u2234 \u2014 Hospitalier",                                100),
    ("Couvreur",                                                     110),
    ("Arch\u2234 \u2014 Architecte",                                 120),
    ("M\u2234B\u2234 \u2014 Ma\u00eetre des Banquets",               130),
]


async def fix():
    engine = create_async_engine(settings.database_url, echo=False)

    # 1. Supprimer la table existante (DROP IF EXISTS)
    async with engine.begin() as conn:
        print("Suppression de lodge_offices...")
        await conn.execute(text("DROP TABLE IF EXISTS lodge_offices"))

    # 2. Recréer toutes les tables (checkfirst=True par défaut)
    async with engine.begin() as conn:
        print("Recréation des tables...")
        await conn.run_sync(Base.metadata.create_all)

    # 3. Insérer les offices par défaut
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with SessionLocal() as db:
        existing = await db.execute(select(LodgeOffice).limit(1))
        if existing.scalar_one_or_none():
            print("Offices déjà présents, rien à faire.")
        else:
            for label, sort_order in DEFAULT_OFFICES:
                db.add(LodgeOffice(label=label, sort_order=sort_order))
            await db.commit()
            print(f"{len(DEFAULT_OFFICES)} offices insérés.")

    await engine.dispose()
    print("\nOK — Relancez l'application : python -m uvicorn app.main:app --reload")


if __name__ == "__main__":
    asyncio.run(fix())
