"""Rattache les planches de la bibliothèque (GED) aux tenues où elles ont été présentées.

Deux temps, pour que rien ne soit enregistré sans validation humaine :

1. Proposer — lit les planches de la bibliothèque (dossiers « Planches … » de la
   GED, fichiers nommés « NOM Prénom AAAA Titre.pdf ») et le texte des tracés de
   tenue, puis propose pour chaque planche la tenue la plus probable :

       python scripts/rapprocher_planches.py proposer propositions_planches.csv

   Le fichier CSV s'ouvre dans Excel. Colonne « valider » : « oui » est déjà mis
   quand la proposition est très nette ; à vous de confirmer, corriger
   (colonne meeting_id, cf. « autres_candidats ») ou laisser vide pour ignorer.

2. Appliquer — crée ou met à jour une fiche Planche par ligne validée, reliée
   à sa tenue et au document de la bibliothèque (qui n'est ni copié, ni
   déplacé, ni modifié) :

       python scripts/rapprocher_planches.py appliquer propositions_planches.csv            # simulation
       python scripts/rapprocher_planches.py appliquer propositions_planches.csv --ecrire   # enregistrement

Relançable sans doublon : une planche déjà rattachée est mise à jour.
"""
import argparse
import asyncio
import csv
import logging
import re
import sys
import unicodedata
import zipfile
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models.documents import DocFolder, Document
from app.models.identity import Member
from app.models.meetings import Meeting
from app.models.planches import Planche, PlancheGrade, PlancheStatus
import app.models  # noqa: F401 — enregistre toutes les tables

logging.getLogger("pypdf").setLevel(logging.ERROR)

_MOIS = {"jan": 1, "janv": 1, "fev": 2, "fevr": 2, "mar": 3, "mars": 3, "avr": 4, "mai": 5, "jun": 6, "juin": 6,
         "jul": 7, "juil": 7, "aou": 8, "aout": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12}
_TRACE_RE = re.compile(r"trac[ée] tenue (\d{3})\s*-?\s*(\d{1,2}) (\w+)\.? (\d{4})\s*(\w*)", re.I)
_PLANCHE_RE = re.compile(r"^(.+?)\s+((?:19|20)\d{2})\s+(.+?)\.[A-Za-z0-9]{2,5}$")
_STOP = set("le la les de des du un une et en au aux pour par sur dans est que qui ou se ce sa son ses mon ma mes "
            "ton ta tes entre faut il elle nous vous avec sans plus moins tout tous comme mais donc quel quelle".split())
YES = {"oui", "o", "x", "1", "yes", "y"}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return " " + re.sub(r"[^a-z0-9]+", " ", s).strip() + " "


def doc_text(path: str) -> str:
    """Texte brut d'un tracé (Word ou PDF) ; chaîne vide si illisible."""
    p = Path(path or "")
    if not p.exists():
        return ""
    try:
        if p.suffix.lower() == ".docx":
            xml = zipfile.ZipFile(p).read("word/document.xml").decode("utf8")
            return " ".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml))
        if p.suffix.lower() == ".pdf":
            from pypdf import PdfReader
            return " ".join((page.extract_text() or "") for page in PdfReader(str(p)).pages)
    except Exception:
        return ""
    return ""


def folder_grade(name: str) -> str:
    n = (name or "").lower()
    if "comp" in n:
        return PlancheGrade.COMPAGNON.value
    if "app" in n:
        return PlancheGrade.APPRENTI.value
    if "m∴" in n or "maître" in n or "maitre" in n or "milieu" in n:
        return PlancheGrade.MAITRE.value
    return PlancheGrade.TOUS.value


def split_author(raw: str) -> tuple[str, str]:
    """« DUBOIS Jean-Luc » → (« DUBOIS », « Jean-Luc »)."""
    tokens = raw.replace("_", " ").split()
    last = [t for t in tokens if t.isupper() and len(t) > 2]
    first = [t for t in tokens if t not in last]
    return " ".join(last) or tokens[0], " ".join(first)


def meeting_label(m: Meeting) -> str:
    num = f"n°{m.meeting_number} " if m.meeting_number is not None else ""
    grade = {"APPRENTI": "App", "COMPAGNON": "Comp", "MAITRE": "M"}.get(m.grade.value, m.grade.value)
    return f"{num}du {m.meeting_date.strftime('%d/%m/%Y')} ({grade})"


# ── 1. Proposer ──────────────────────────────────────────────────────────────

async def proposer(db, out_path: str) -> None:
    folders = {fid: name for fid, name in await db.execute(select(DocFolder.id, DocFolder.name))}
    planche_folders = {fid: n for fid, n in folders.items()
                       if "planche" in (n or "").lower() and "programme" not in (n or "").lower()}
    docs = list(await db.execute(
        select(Document.id, Document.name, Document.folder_id, Document.storage_path)
        .where(Document.deleted_at.is_(None))
    ))
    meetings = list((await db.execute(select(Meeting))).scalars())
    by_date = defaultdict(list)
    for m in meetings:
        by_date[m.meeting_date].append(m)

    # Tracés → tenue → texte (Word et PDF d'une même tenue fusionnés)
    texts: dict[int, str] = defaultdict(str)
    for doc_id, name, _, path in docs:
        t = _TRACE_RE.search(name or "")
        if not t:
            continue
        month = _MOIS.get(norm(t[3]).strip())
        try:
            d = date(int(t[4]), month, int(t[2])) if month else None
        except ValueError:
            d = None
        same_day = by_date.get(d, [])
        if not same_day:
            continue
        suffix = norm(t[5]).strip()
        grade = "COMPAGNON" if suffix.startswith("com") else "MAITRE" if suffix.startswith("ma") else "APPRENTI"
        target = next((m for m in same_day if m.grade.value == grade), same_day[0])
        texts[target.id] += norm(doc_text(path))
    print(f"Tracés lus : {len(texts)} tenues avec du texte exploitable")

    planches = []
    for doc_id, name, fid, _ in docs:
        if fid not in planche_folders:
            continue
        m = _PLANCHE_RE.match((name or "").replace("\\'", "'"))
        if not m:
            planches.append((doc_id, name, "", None, name, folder_grade(planche_folders[fid])))
            continue
        planches.append((doc_id, name, m[1], int(m[2]), re.sub(r"\s+", " ", m[3]).strip(),
                         folder_grade(planche_folders[fid])))

    # Scores bruts : mots du titre retrouvés + nom de l'auteur cité + titre exact
    raw: dict[int, dict[int, float]] = {}
    for doc_id, _, author, year, title, _ in planches:
        words = [w for w in norm(title).split() if len(w) > 3 and w not in _STOP]
        last, _first = split_author(author) if author else ("", "")
        scores = {}
        for mid, txt in texts.items():
            mtg = next(x for x in meetings if x.id == mid)
            if year and not (date(year - 1, 9, 1) <= mtg.meeting_date <= date(year + 1, 6, 30)):
                continue
            title_part = sum(1 for w in words if f" {w} " in txt) / len(words) if words else 0
            score = 2 * title_part + (1 if last and norm(last) in txt else 0) + (1 if len(words) >= 2 and norm(title) in txt else 0)
            if score >= 1.5:
                scores[mid] = score
        raw[doc_id] = scores

    # Les tracés qui citent beaucoup de planches (récapitulatifs de fin d'année) pèsent moins
    citations = defaultdict(int)
    for scores in raw.values():
        for mid in scores:
            citations[mid] += 1

    rows = []
    for doc_id, name, author, year, title, grade in planches:
        ranked = sorted(((s / (2 if citations[mid] > 4 else 1), mid) for mid, s in raw[doc_id].items()), reverse=True)
        best = ranked[0] if ranked else None
        second = ranked[1][0] if len(ranked) > 1 else 0
        sure = bool(best and best[0] >= 2.5 and best[0] - second >= 0.75)
        mtg = next((x for x in meetings if best and x.id == best[1]), None)
        last, first = split_author(author) if author else ("", "")
        rows.append({
            "document_id": doc_id, "fichier": name, "auteur": f"{first} {last}".strip(), "annee": year or "",
            "titre": title, "grade": grade,
            "meeting_id": mtg.id if mtg else "", "tenue_proposee": meeting_label(mtg) if mtg else "",
            "score": round(best[0], 2) if best else "",
            "autres_candidats": " | ".join(
                f"{meeting_label(next(x for x in meetings if x.id == mid))} [id {mid}] {s:.2f}"
                for s, mid in ranked[1:4]),
            "valider": "oui" if sure else "",
        })

    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["document_id"], delimiter=";")
        w.writeheader()
        w.writerows(rows)
    sure = sum(1 for r in rows if r["valider"])
    found = sum(1 for r in rows if r["meeting_id"])
    print(f"{len(rows)} planches : {sure} propositions nettes (« oui » pré-rempli), "
          f"{found - sure} à vérifier, {len(rows) - found} sans tenue trouvée.")
    print(f"→ {out_path}")


# ── 2. Appliquer ─────────────────────────────────────────────────────────────

async def appliquer(db, csv_path: str) -> None:
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f, delimiter=";") if (r.get("valider") or "").strip().lower() in YES]
    members = list((await db.execute(select(Member))).scalars())
    existing = {p.library_doc_id: p for p in (await db.execute(
        select(Planche).where(Planche.library_doc_id.isnot(None))
    )).scalars()}
    created = updated = 0
    for r in rows:
        doc_id = int(r["document_id"])
        meeting = await db.get(Meeting, int(r["meeting_id"])) if (r.get("meeting_id") or "").strip().isdigit() else None
        if not meeting:
            print(f"  ⚠ Ignorée (pas de tenue) : {r['fichier']}")
            continue
        last, first = split_author(r["auteur"]) if (r.get("auteur") or "").strip() else ("", "")
        # Auteur membre de la loge (même nom, même initiale) sinon nom en clair
        candidates = [m for m in members if norm(m.last_name) == norm(last)]
        if len(candidates) > 1 and first:
            candidates = [m for m in candidates if norm(m.first_name)[:2] == norm(first)[:2]]
        author = candidates[0] if len(candidates) == 1 else None
        p = existing.get(doc_id)
        if not p:
            p = Planche(library_doc_id=doc_id, status=PlancheStatus.PUBLIE)
            db.add(p)
            created += 1
        else:
            updated += 1
        p.title = r["titre"]
        p.grade = r.get("grade") or PlancheGrade.TOUS.value
        p.meeting_id = meeting.id
        p.published_at = datetime.combine(meeting.meeting_date, datetime.min.time())
        p.author_id = author.id if author else None
        p.author_name = None if author else (r.get("auteur") or None)
        print(f"  ✓ {r['titre'][:60]:60s} → {meeting_label(meeting)}"
              f" · {'membre' if author else 'auteur extérieur'} {r.get('auteur', '')}")
    print(f"\n{created} planche(s) rattachée(s), {updated} mise(s) à jour.")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("proposer")
    p1.add_argument("csv", nargs="?", default="propositions_planches.csv")
    p2 = sub.add_parser("appliquer")
    p2.add_argument("csv")
    p2.add_argument("--ecrire", action="store_true", help="enregistrer (sinon simulation)")
    args = ap.parse_args()

    engine = create_async_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            if args.cmd == "proposer":
                await proposer(db, args.csv)
            else:
                await appliquer(db, args.csv)
                if args.ecrire:
                    await db.commit()
                    print("✅ Enregistré.")
                else:
                    await db.rollback()
                    print("Simulation — relancer avec --ecrire pour enregistrer.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
