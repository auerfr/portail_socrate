"""Script de dédoublonnage des visiteurs (Visitor) en base SQLite.

Pour chaque groupe de visiteurs ayant le même nom (insensible à la casse) :
- Garde le plus "riche" (le plus de champs renseignés, sinon le plus ancien)
- Redirige tous les MeetingVisitor des doublons vers le visiteur conservé
- Supprime les doublons

Usage :
    python scripts/dedup_visitors.py [--dry-run]
"""
import asyncio
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, delete, update, func, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.models.meetings import Visitor, MeetingVisitor
from app.config import get_settings
settings = get_settings()


def _richness(v: Visitor) -> int:
    """Score de richesse d'une fiche visiteur (plus grand = mieux)."""
    score = 0
    for field in (v.lodge_name, v.orient_city, v.obedience, v.email, v.phone, v.masonic_grade):
        if field and field.strip():
            score += 1
    return score


async def run(dry_run: bool = False) -> None:
    engine = create_async_engine(settings.database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        # Trouver les noms en doublon
        dup_q = await db.execute(
            select(
                func.lower(Visitor.last_name).label("ln"),
                func.lower(Visitor.first_name).label("fn"),
                func.count(Visitor.id).label("cnt"),
            )
            .group_by(func.lower(Visitor.last_name), func.lower(Visitor.first_name))
            .having(func.count(Visitor.id) > 1)
        )
        dupes = dup_q.all()

        if not dupes:
            print("Aucun doublon trouvé.")
            return

        total_merged = 0
        for row in dupes:
            ln, fn = row.ln, row.fn
            visitors_r = await db.execute(
                select(Visitor).where(
                    func.lower(Visitor.last_name) == ln,
                    func.lower(Visitor.first_name) == fn,
                ).order_by(Visitor.id)
            )
            visitors = visitors_r.scalars().all()

            # Choisir le "meilleur" : plus de champs, sinon le plus ancien (id le plus petit)
            best = max(visitors, key=lambda v: (_richness(v), -v.id))
            to_delete = [v for v in visitors if v.id != best.id]

            print(f"\n{'[DRY-RUN] ' if dry_run else ''}Doublon : {best.last_name} {best.first_name}")
            print(f"  → Conservé : id={best.id}  loge={best.lodge_name}  orient={best.orient_city}")
            for v in to_delete:
                print(f"  ✗ Supprimé : id={v.id}  loge={v.lodge_name}  orient={v.orient_city}")

            if not dry_run:
                # Enrichir la fiche conservée avec les données des doublons
                for v in to_delete:
                    if not best.lodge_name and v.lodge_name:
                        best.lodge_name = v.lodge_name
                    if not best.orient_city and v.orient_city:
                        best.orient_city = v.orient_city
                    if not best.obedience and v.obedience:
                        best.obedience = v.obedience
                    if not best.email and v.email:
                        best.email = v.email
                    if not best.phone and v.phone:
                        best.phone = v.phone

                # Réattribuer les MeetingVisitor des doublons vers le meilleur
                for v in to_delete:
                    # Récupérer les MV du doublon
                    mv_r = await db.execute(
                        select(MeetingVisitor).where(MeetingVisitor.visitor_id == v.id)
                    )
                    for mv in mv_r.scalars().all():
                        # Vérifier qu'il n'y a pas déjà un MV pour (meeting, best.id)
                        conflict = await db.execute(
                            select(MeetingVisitor).where(
                                MeetingVisitor.meeting_id == mv.meeting_id,
                                MeetingVisitor.visitor_id == best.id,
                            )
                        )
                        if conflict.scalar_one_or_none():
                            # Conflit : supprimer le doublon MV
                            await db.delete(mv)
                        else:
                            mv.visitor_id = best.id

                    # Supprimer le Visitor doublon
                    await db.delete(v)

                total_merged += len(to_delete)

        if not dry_run:
            await db.commit()
            print(f"\n✓ {total_merged} visiteur(s) doublon(s) fusionné(s).")
        else:
            print(f"\n[DRY-RUN] {total_merged} visiteur(s) seraient fusionnés.")

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dédoublonnage des visiteurs")
    parser.add_argument("--dry-run", action="store_true", help="Aperçu sans modification")
    args = parser.parse_args()
    asyncio.run(run(dry_run=args.dry_run))
