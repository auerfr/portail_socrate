"""Corrige les données OJ corrompues + ajoute vm_member_id/secretary_member_id si besoin."""
import asyncio, sys, os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ".")

OJ = "\n".join([
    "19H30",
    "Accueil et habillage (installation) du Temple",
    "20H00",
    "Fondation, trace et reglage de la loge (ouverture symbolique)",
    "Livre d'architecture et correspondances officielles",
    "Minute des correspondants aux commissions",
    "Point sur les travaux des chantiers et groupes de travail",
    "Dialogue rituel",
    "Conclusion de l'Orateur - Chaine d'Union - Circulation des troncs",
    "Effacement et fermeture de la loge (Fermeture symbolique)",
    "22H30",
    "Agape fraternelle (Reservation imperative 2 jours au moins avant la Tenue)",
])

SCH = "\n".join([
    "TTen. le 1er jeudi et le 3eme mercredi",
    "Chantiers App. : les 2eme mardi du mois",
    "Chantiers Comp. : les 2eme jeudi du mois",
    "Chantiers MM. : les 4eme mardi du mois",
])

ADDR = "Salle \"la Colonie\", Av. General Patton, 54700 MOUSSON"

async def main():
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
    from app.models.lodge import LodgeSettings

    engine = create_async_engine("sqlite+aiosqlite:///./socrate_local.db")
    Sess = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Sess() as db:
        r = await db.execute(select(LodgeSettings).limit(1))
        lodge = r.scalar_one_or_none()
        if lodge:
            lodge.common_agenda    = OJ
            lodge.standard_schedule = SCH
            lodge.temple_address   = ADDR
            await db.commit()
            print(f"OK - OJ corrige ({len(OJ)} chars)")
            print(f"Premiere ligne OJ : {OJ.splitlines()[0]}")
            print(f"Lignes OJ : {len(OJ.splitlines())}")
        else:
            print("ERREUR: aucune lodge_settings trouvee")
    await engine.dispose()

asyncio.run(main())
