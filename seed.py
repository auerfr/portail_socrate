"""
Script de démarrage — crée les données initiales :
  - Année maçonnique courante
  - Config loge
  - Membre admin (login: admin / mdp: admin)
  - Groupes : Commission Finances, Commission Solidarité
  - (idempotent : peut être relancé sans dupliquer)

Usage : python seed.py
"""
import asyncio
import os
import sys
from datetime import date

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from app.config import get_settings
from app.database import Base
from app.models.identity import Member, User, MasonicGrade, MemberStatus, LodgeFunction, Group, GroupType
from app.models.lodge import MasonicYear, LodgeSettings
from app.dependencies import hash_password

settings = get_settings()


async def seed():
    print("Connexion a la base de donnees...")
    engine = create_async_engine(settings.database_url, echo=False)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        print("Creation des tables...")
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as db:

        # ── Année maçonnique courante ──────────────────────────────────────────
        existing_year = await db.execute(select(MasonicYear).limit(1))
        if not existing_year.scalar_one_or_none():
            today = date.today()
            if today.month >= 9:
                start = date(today.year, 9, 1)
                end   = date(today.year + 1, 6, 30)
                label = f"{today.year + 4000}-{today.year + 4001}"
            else:
                start = date(today.year - 1, 9, 1)
                end   = date(today.year, 6, 30)
                label = f"{today.year + 3999}-{today.year + 4000}"

            year = MasonicYear(label=label, start_date=start, end_date=end, is_current=True)
            db.add(year)
            await db.flush()
            print(f"Annee maconnique creee : {label}")

        # ── Config loge ───────────────────────────────────────────────────────
        existing_lodge = await db.execute(select(LodgeSettings).limit(1))
        if not existing_lodge.scalar_one_or_none():
            lodge = LodgeSettings(
                name=settings.lodge_name,
                orient_city=settings.lodge_orient,
                obedience=settings.lodge_obedience,
            )
            db.add(lodge)
            print(f"Config loge creee : {settings.lodge_name}")

        # ── Groupes / Commissions ─────────────────────────────────────────────
        commissions = [
            ("Commission Finances",    "Gestion financière et budget de la loge — 5 membres", 5),
            ("Commission Solidarité",  "Entraide et solidarité fraternelle — 5 membres",      5),
        ]
        for name, desc, _ in commissions:
            existing = await db.execute(select(Group).where(Group.name == name))
            if not existing.scalar_one_or_none():
                g = Group(name=name, description=desc, type=GroupType.COMMISSION)
                db.add(g)
                print(f"Commission creee : {name}")

        # ── Membre & utilisateur admin ────────────────────────────────────────
        existing_member = await db.execute(select(Member).where(Member.email == "admin@loge.local"))
        if not existing_member.scalar_one_or_none():
            member = Member(
                last_name="Admin",
                first_name="Super",
                email="admin@loge.local",
                masonic_grade=MasonicGrade.MAITRE,
                status=MemberStatus.ACTIVE,
                lodge_function=LodgeFunction.VM,
            )
            db.add(member)
            await db.flush()

            user = User(
                member_id=member.id,
                login="admin",
                password_hash=hash_password("admin"),
                is_active=True,
                is_admin=True,
            )
            db.add(user)
            print("Utilisateur admin cree : login=admin / mdp=admin")
            print("IMPORTANT : changez le mot de passe apres la premiere connexion !")

        await db.commit()
        print("\nBase de donnees initialisee avec succes !")
        print("Lancer l'application : python -m uvicorn app.main:app --reload")
        print("Ouvrir : http://localhost:8000")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
