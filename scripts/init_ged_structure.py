"""
Script d'initialisation de la structure GED du Portail Socrate.
Recrée l'arborescence Agora existante avec les bonnes permissions.
Idempotent : ne recrée pas ce qui existe déjà (vérifie par nom + parent).
"""
import asyncio
import sys
import io
from pathlib import Path

# Forcer UTF-8 sur stdout (Windows console cp1252 ne supporte pas ∴)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Ajouter le répertoire racine au path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import engine, AsyncSessionLocal
from app.models.documents import DocSpace, DocFolder, MinGrade, DocAccessMode
from app.models.groups import LodgeGroup


# ── Couleurs console ────────────────────────────────────────────────────────
def ok(msg):  print(f"  [OK]  {msg}")
def skip(msg): print(f"  [--]  {msg} (déjà existant)")
def section(msg): print(f"\n{'='*60}\n  {msg}\n{'='*60}")


# ── Helpers ─────────────────────────────────────────────────────────────────

async def get_groups(db: AsyncSession) -> dict[str, int]:
    """Récupère les IDs des groupes système par slug."""
    r = await db.execute(select(LodgeGroup).where(LodgeGroup.slug.isnot(None)))
    return {g.slug: g.id for g in r.scalars().all()}


async def get_or_create_space(
    db: AsyncSession, name: str,
    min_grade: MinGrade = MinGrade.ALL,
    group_id: int | None = None,
    order: int = 0,
    description: str = "",
) -> tuple[DocSpace, bool]:
    r = await db.execute(select(DocSpace).where(DocSpace.name == name))
    existing = r.scalar_one_or_none()
    if existing:
        return existing, False
    space = DocSpace(
        name=name,
        description=description,
        min_grade=min_grade,
        group_id=group_id,
        order_position=order,
        access_mode=DocAccessMode.GRADE,
    )
    db.add(space)
    await db.flush()  # pour obtenir l'id
    return space, True


async def get_or_create_folder(
    db: AsyncSession,
    name: str,
    space_id: int,
    parent_id: int | None = None,
    min_grade: MinGrade = MinGrade.ALL,
    group_id: int | None = None,
    order: int = 0,
) -> tuple[DocFolder, bool]:
    stmt = (
        select(DocFolder)
        .where(
            DocFolder.name == name,
            DocFolder.space_id == space_id,
            DocFolder.parent_id == parent_id,
        )
    )
    r = await db.execute(stmt)
    existing = r.scalar_one_or_none()
    if existing:
        return existing, False
    folder = DocFolder(
        name=name,
        space_id=space_id,
        parent_id=parent_id,
        min_grade=min_grade,
        group_id=group_id,
        order_position=order,
    )
    db.add(folder)
    await db.flush()
    return folder, True


def log(name: str, created: bool, indent: int = 0):
    prefix = "  " * indent
    if created:
        ok(f"{prefix}{name}")
    else:
        skip(f"{prefix}{name}")


# ── Structure complète ───────────────────────────────────────────────────────

async def build_structure(db: AsyncSession):
    grp = await get_groups(db)
    G = grp  # alias court

    # ════════════════════════════════════════════════════════════════════════
    section("1. ADMINISTRATIF")
    # ════════════════════════════════════════════════════════════════════════
    sp, c = await get_or_create_space(
        db, "Administratif",
        group_id=G.get("conseil"),  # Visible aux officiers
        order=10,
        description="Secrétariat, trésorerie, dossiers membres et documents administratifs",
    )
    log("Administratif", c)

    # — Secrétariat —
    sec, c = await get_or_create_folder(db, "Secrétariat", sp.id, group_id=G.get("secretariat"), order=10)
    log("Secrétariat", c, 1)

    for name, order in [
        ("Enquêtes : Formulaires et Guide", 10),
        ("Présences et activité", 20),
    ]:
        f, c = await get_or_create_folder(db, name, sp.id, sec.id, group_id=G.get("secretariat"), order=order)
        log(name, c, 2)

    prog, c = await get_or_create_folder(db, "Programmes", sp.id, sec.id, group_id=G.get("secretariat"), order=30)
    log("Programmes", c, 2)
    for name, order in [
        ("PP SRP 2022-2023", 10), ("PP SRP 2023-2024", 20),
        ("PP SRP 2024-2025", 30), ("PP SRP 2025-2026", 40),
    ]:
        f, c = await get_or_create_folder(db, name, sp.id, prog.id, group_id=G.get("secretariat"), order=order)
        log(name, c, 3)

    traces, c = await get_or_create_folder(db, "Tracés Word", sp.id, sec.id, group_id=G.get("secretariat"), order=40)
    log("Tracés Word", c, 2)
    for name, order in [
        ("Tracés 2022-2023", 10), ("Tracés 2023-2024", 20),
        ("Tracés 2024-2025", 30), ("Tracés 2025-2026", 40),
    ]:
        f, c = await get_or_create_folder(db, name, sp.id, traces.id, group_id=G.get("secretariat"), order=order)
        log(name, c, 3)

    post, c = await get_or_create_folder(db, "Dossiers Postulants", sp.id, sec.id, group_id=G.get("secretariat"), order=50)
    log("Dossiers Postulants", c, 2)

    members_dir, c = await get_or_create_folder(db, "Dossiers membres", sp.id, sec.id, group_id=G.get("secretariat"), order=60)
    log("Dossiers membres", c, 2)

    anciens, c = await get_or_create_folder(db, "Dossiers anciens membres", sp.id, sec.id, group_id=G.get("secretariat"), order=70)
    log("Dossiers anciens membres", c, 2)

    # — Trésorerie —
    treso, c = await get_or_create_folder(db, "Trésorerie", sp.id, group_id=G.get("tresorerie"), order=20)
    log("Trésorerie", c, 1)
    for name, order in [
        ("Relevés Compte BP", 10),
        ("CM documents Socrate", 20),
        ("Archives", 30),
    ]:
        f, c = await get_or_create_folder(db, name, sp.id, treso.id, group_id=G.get("tresorerie"), order=order)
        log(name, c, 2)
    conf, c = await get_or_create_folder(db, "Confidentiel Tranche", sp.id, treso.id, group_id=G.get("tresorerie"), order=40)
    log("Confidentiel Tranche", c, 2)
    f, c = await get_or_create_folder(db, "2023", sp.id, conf.id, group_id=G.get("tresorerie"), order=10)
    log("2023", c, 3)

    # — Documents Divers Admin —
    f, c = await get_or_create_folder(db, "Documents Divers Admin", sp.id, group_id=G.get("conseil"), order=30)
    log("Documents Divers Admin", c, 1)

    # — Création et Statut —
    crea, c = await get_or_create_folder(db, "Création et Statut", sp.id, group_id=G.get("conseil"), order=40)
    log("Création et Statut", c, 1)
    for name, order in [
        ("Creation Socrate Raison et Progrès", 10),
        ("Crédit Mutuel Docs Socrate", 20),
        ("Socrate Creation", 30),
    ]:
        f, c = await get_or_create_folder(db, name, sp.id, crea.id, group_id=G.get("conseil"), order=order)
        log(name, c, 2)

    # — Courrier —
    f, c = await get_or_create_folder(db, "Courrier prise de contact", sp.id, group_id=G.get("secretariat"), order=50)
    log("Courrier prise de contact", c, 1)

    # ════════════════════════════════════════════════════════════════════════
    section("2. RITUELS")
    # ════════════════════════════════════════════════════════════════════════
    sp, c = await get_or_create_space(
        db, "Rituels",
        min_grade=MinGrade.APPRENTI,  # Au moins apprenti pour voir l'espace
        order=20,
        description="Rituels et documents de travail par degré",
    )
    log("Rituels", c)

    # App∴
    app, c = await get_or_create_folder(db, "Rituels App∴", sp.id, group_id=G.get("apprentis"), order=10)
    log("Rituels App∴", c, 1)
    for name, order in [
        ("Archives", 10), ("Références et Documents divers", 20),
        ("Cérémonie réception", 30), ("Dialogue et Tuileur", 40),
        ("Rituel, Tuileurs Documents en cours", 50),
    ]:
        f, c = await get_or_create_folder(db, name, sp.id, app.id, group_id=G.get("apprentis"), order=order)
        log(name, c, 2)
    ritu, c = await get_or_create_folder(db, "Rituel, Tuileurs Documents en cours", sp.id, app.id, group_id=G.get("apprentis"), order=50)
    # Version Word dedans
    f, c = await get_or_create_folder(db, "Version WORD doc App∴", sp.id, ritu.id if not ritu else ritu.id, group_id=G.get("apprentis"), order=10)
    log("Version WORD doc App∴", c, 3)

    # Comp∴
    comp, c = await get_or_create_folder(db, "Rituels Comp∴", sp.id, group_id=G.get("compagnons"), order=20)
    log("Rituels Comp∴", c, 1)
    for name, order in [
        ("Archives", 10), ("Document de Travail", 20),
        ("Tuileurs et Livre du Devoir", 30),
    ]:
        f, c = await get_or_create_folder(db, name, sp.id, comp.id, group_id=G.get("compagnons"), order=order)
        log(name, c, 2)

    # MM∴
    mm, c = await get_or_create_folder(db, "Rituels MM∴", sp.id, group_id=G.get("maitres"), order=30)
    log("Rituels MM∴", c, 1)
    for name, order in [
        ("Documents de Travail", 10), ("Socrate Rituel MM∴", 20),
        ("Conseil de MM∴", 30), ("Archives", 40),
    ]:
        f, c = await get_or_create_folder(db, name, sp.id, mm.id, group_id=G.get("maitres"), order=order)
        log(name, c, 2)
    chan, c = await get_or_create_folder(db, "Chantiers de MM∴", sp.id, mm.id, group_id=G.get("maitres"), order=5)
    log("Chantiers de MM∴", c, 2)
    for i in range(1, 6):
        f, c = await get_or_create_folder(db, f"Chantier n°{i}", sp.id, chan.id, group_id=G.get("maitres"), order=i*10)
        log(f"Chantier n°{i}", c, 3)

    # Rituels communs (tous membres)
    for name, order in [
        ("Rituel officiel et Lalande", 40), ("Allumage Socrate", 50),
        ("Socrate Textes Fondateurs", 60), ("Rituels réception", 70),
        ("Loge de Table", 80), ("Tenue funèbre", 90),
    ]:
        f, c = await get_or_create_folder(db, name, sp.id, min_grade=MinGrade.APPRENTI, order=order)
        log(name, c, 1)

    # ════════════════════════════════════════════════════════════════════════
    section("3. PLANCHES")
    # ════════════════════════════════════════════════════════════════════════
    sp, c = await get_or_create_space(
        db, "Planches",
        min_grade=MinGrade.APPRENTI,
        order=30,
        description="Planches déposées en loge, par degré",
    )
    log("Planches", c)

    planche_folders = [
        ("A — Planches en Loge d'App∴",        G.get("apprentis"),  None,           10),
        ("B — Planches en Ch∴ de Comp∴",        G.get("compagnons"), None,           20),
        ("C — Planches en Ch∴ du M∴",           G.get("maitres"),    None,           30),
        ("D — Questions à l'étude des Loges",   None,                MinGrade.APPRENTI, 40),
        ("E — Chroniques Maçonniques",          None,                MinGrade.APPRENTI, 50),
    ]
    for name, gid, mg, order in planche_folders:
        f, c = await get_or_create_folder(db, name, sp.id, group_id=gid, min_grade=mg or MinGrade.ALL, order=order)
        log(name, c, 1)

    # Sous-dossiers Questions à l'étude
    quest, c = await get_or_create_folder(db, "D — Questions à l'étude des Loges", sp.id, min_grade=MinGrade.APPRENTI, order=40)
    for year, order in [("2022-2023", 10), ("2023-2024", 20), ("2024-2025", 30)]:
        f, c = await get_or_create_folder(db, f"Questions à l'étude {year}", sp.id, quest.id, min_grade=MinGrade.APPRENTI, order=order)
        log(f"Questions à l'étude {year}", c, 2)

    # ════════════════════════════════════════════════════════════════════════
    section("4. PLANCHES PROGRAMMES")
    # ════════════════════════════════════════════════════════════════════════
    sp, c = await get_or_create_space(
        db, "Planches Programmes",
        min_grade=MinGrade.APPRENTI,
        order=40,
        description="Planches au programme des tenues, par année",
    )
    log("Planches Programmes", c)
    for name, order in [
        ("Planches programmes externes", 5),
        ("Planches programmes 2022-2023", 10),
        ("Planches programmes 2023-2024", 20),
        ("Planches programmes 2024-2025", 30),
        ("Planches programmes 2025-2026", 40),
    ]:
        f, c = await get_or_create_folder(db, name, sp.id, min_grade=MinGrade.APPRENTI, order=order)
        log(name, c, 1)

    # ════════════════════════════════════════════════════════════════════════
    section("5. LIVRE D'ARCHITECTURE")
    # ════════════════════════════════════════════════════════════════════════
    sp, c = await get_or_create_space(
        db, "Livre d'Architecture",
        group_id=G.get("maitres"),
        order=50,
        description="Tracés et comptes rendus des chantiers de loge",
    )
    log("Livre d'Architecture", c)
    for name, order in [
        ("Tracés Chantiers 2022", 5),
        ("Tracés 2022-2023", 10), ("Tracés 2023-2024", 20),
        ("Tracés 2024-2025", 30), ("Tracés 2025-2026", 40),
    ]:
        f, c = await get_or_create_folder(db, name, sp.id, group_id=G.get("maitres"), order=order)
        log(name, c, 1)

    # ════════════════════════════════════════════════════════════════════════
    section("6. COLLÈGE MAÎTRES OFFICIERS")
    # ════════════════════════════════════════════════════════════════════════
    sp, c = await get_or_create_space(
        db, "Collège Maîtres Officiers",
        group_id=G.get("conseil"),
        order=60,
        description="Documents réservés aux officiers en exercice",
    )
    log("Collège Maîtres Officiers", c)

    # ════════════════════════════════════════════════════════════════════════
    section("7. AGAPES")
    # ════════════════════════════════════════════════════════════════════════
    sp, c = await get_or_create_space(
        db, "Agapes",
        min_grade=MinGrade.ALL,
        order=70,
        description="Menus, photos et souvenirs des agapes",
    )
    log("Agapes", c)

    # ════════════════════════════════════════════════════════════════════════
    section("8. HARMONIE")
    # ════════════════════════════════════════════════════════════════════════
    sp, c = await get_or_create_space(
        db, "Harmonie",
        min_grade=MinGrade.ALL,
        order=80,
        description="Partitions et documents musicaux",
    )
    log("Harmonie", c)

    # ════════════════════════════════════════════════════════════════════════
    section("9. SCEAUX & COMMUNICATION")
    # ════════════════════════════════════════════════════════════════════════
    sp, c = await get_or_create_space(
        db, "Sceaux & Communication",
        min_grade=MinGrade.ALL,
        order=90,
        description="Logos, sceaux, affiches et supports de communication",
    )
    log("Sceaux & Communication", c)
    for name, order in [
        ("Affiche", 10), ("Goodies", 20),
        ("Logo Blanc", 30), ("Logo Noir", 40),
    ]:
        f, c = await get_or_create_folder(db, name, sp.id, order=order)
        log(name, c, 1)
    tapis, c = await get_or_create_folder(db, "Tapis de Loge", sp.id, order=50)
    log("Tapis de Loge", c, 1)
    f, c = await get_or_create_folder(db, "Fichiers Cédric", sp.id, tapis.id, order=10)
    log("Fichiers Cédric", c, 2)

    # ════════════════════════════════════════════════════════════════════════
    section("10. SOCRATE RÉFLEXIONS & PROJETS")
    # ════════════════════════════════════════════════════════════════════════
    sp, c = await get_or_create_space(
        db, "Socrate Réflexions & Projets",
        min_grade=MinGrade.ALL,
        order=100,
        description="Projets, réflexions et archives de la loge",
    )
    log("Socrate Réflexions & Projets", c)

    immo, c = await get_or_create_folder(db, "Immobilier / Local", sp.id, order=10)
    log("Immobilier / Local", c, 1)
    for name, order in [("Archives", 10), ("Photos", 20), ("Plans divers", 30)]:
        f, c = await get_or_create_folder(db, name, sp.id, immo.id, order=order)
        log(name, c, 2)

    bien, c = await get_or_create_folder(db, "Biennale 2026", sp.id, order=20)
    log("Biennale 2026", c, 1)
    for name, order in [("Compte rendu", 10), ("Affiches et créations", 20)]:
        f, c = await get_or_create_folder(db, name, sp.id, bien.id, order=order)
        log(name, c, 2)
    aff, c = await get_or_create_folder(db, "Affiches et créations", sp.id, bien.id, order=20)
    f, c = await get_or_create_folder(db, "Back", sp.id, aff.id, order=10)
    log("Back", c, 3)

    for name, order in [
        ("Pierre d'Angle Socrate", 30),
        ("Allumage des Feux Socrate 2022", 40),
        ("Rose Philosophique", 50),
    ]:
        f, c = await get_or_create_folder(db, name, sp.id, order=order)
        log(name, c, 1)

    # ════════════════════════════════════════════════════════════════════════
    section("11. DOCUMENTS MAÇONNIQUES")
    # ════════════════════════════════════════════════════════════════════════
    sp, c = await get_or_create_space(
        db, "Documents Maçonniques",
        min_grade=MinGrade.APPRENTI,
        order=110,
        description="Documents généraux de la franc-maçonnerie",
    )
    log("Documents Maçonniques", c)

    # ════════════════════════════════════════════════════════════════════════
    section("12. DIVERS")
    # ════════════════════════════════════════════════════════════════════════
    sp, c = await get_or_create_space(
        db, "Divers",
        min_grade=MinGrade.ALL,
        order=120,
        description="Photos d'allumage, images et documents divers",
    )
    log("Divers", c)
    for name, order in [("Allumage Photos", 10), ("Images BO", 20)]:
        f, c = await get_or_create_folder(db, name, sp.id, order=order)
        log(name, c, 1)


# ── Main ────────────────────────────────────────────────────────────────────

async def main():
    print("\nInitialisation de la structure GED — Portail Socrate")
    print("=" * 60)

    async with AsyncSessionLocal() as db:
        # Vérifier que les groupes système existent
        grp = await get_groups(db)
        if not grp:
            print("\nERREUR : Aucun groupe système trouvé en base.")
            print("Démarrez le serveur une fois pour initialiser les groupes.")
            return

        print(f"\nGroupes disponibles : {list(grp.keys())}")

        await build_structure(db)
        await db.commit()

    print("\n" + "=" * 60)
    print("  Structure GED créée avec succès.")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
