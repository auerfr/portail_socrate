"""
Diagnostic en LECTURE SEULE — vérifie si les votes d'un sondage ont perdu
leur identité de votant (member_id NULL) suite à l'ancien mécanisme
d'anonymisation, sans rien modifier.

Usage :
    python scripts/diag_poll_votes.py <poll_id>
    python scripts/diag_poll_votes.py --all   (liste tous les sondages)
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import app.main  # noqa: F401
from app.database import AsyncSessionLocal
from app.models.content import Poll, PollVote
from sqlalchemy import select, func


async def main():
    async with AsyncSessionLocal() as db:
        if len(sys.argv) > 1 and sys.argv[1] == "--all":
            r = await db.execute(select(Poll).order_by(Poll.created_at.desc()))
            polls = r.scalars().all()
        elif len(sys.argv) > 1:
            poll = await db.get(Poll, int(sys.argv[1]))
            polls = [poll] if poll else []
        else:
            print("Usage: python scripts/diag_poll_votes.py <poll_id>  ou  --all")
            return

        for poll in polls:
            r1 = await db.execute(
                select(func.count()).where(PollVote.poll_id == poll.id, PollVote.member_id.is_(None))
            )
            orphaned = r1.scalar() or 0
            r2 = await db.execute(
                select(func.count()).where(PollVote.poll_id == poll.id, PollVote.member_id.is_not(None))
            )
            attributed = r2.scalar() or 0
            r3 = await db.execute(
                select(func.count(func.distinct(PollVote.member_id))).where(
                    PollVote.poll_id == poll.id, PollVote.member_id.is_not(None)
                )
            )
            distinct_voters = r3.scalar() or 0

            print(f"\nSondage #{poll.id} — {poll.title!r} (anonyme={poll.is_anonymous}, type={poll.vote_type})")
            print(f"  Lignes de vote SANS identité (member_id NULL) : {orphaned}")
            print(f"  Lignes de vote AVEC identité                  : {attributed}")
            print(f"  Votants distincts identifiables                : {distinct_voters}")
            if orphaned:
                print("  -> Ces lignes existent toujours et comptent dans les résultats/pourcentages,")
                print("     mais ne peuvent pas être attribuées à une personne (votes anonymes")
                print("     enregistrés AVANT le correctif du 9 septembre).")


if __name__ == "__main__":
    asyncio.run(main())
