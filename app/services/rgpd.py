"""Export RGPD (droit à la portabilité) — ZIP pour un membre donné, avec :
- mes-donnees.html : version lisible dans un navigateur (public visé)
- donnees.json     : version brute structurée (portabilité machine-readable)

Utilisé à la fois par l'export admin (n'importe quel membre) et par le
self-service (le membre exporte ses propres données)."""
import io
import json
import zipfile
from datetime import datetime
from html import escape

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import Member, User


def _serialize(obj) -> dict:
    out = {}
    for col in obj.__table__.columns:
        val = getattr(obj, col.name)
        if hasattr(val, "isoformat"):
            val = val.isoformat()
        elif hasattr(val, "value"):
            val = val.value
        out[col.name] = val
    return out


ATTENDANCE_LABELS = {"PRESENT": "Présent", "EXCUSED": "Excusé", "ABSENT": "Absent"}
PLANCHE_STATUS_LABELS = {"BROUILLON": "Brouillon", "PUBLIE": "Publiée"}
TASK_STATUS_LABELS = {"TODO": "À faire", "IN_PROGRESS": "En cours", "DONE": "Terminée"}
AUDIT_ACTION_LABELS = {
    "LOGIN": "Connexion", "LOGIN_FAILED": "Tentative de connexion échouée",
    "LOGIN_BLOCKED": "Connexion bloquée (compte désactivé)",
    "RGPD_EXPORT": "Export RGPD (par un administrateur)",
    "RGPD_SELF_EXPORT": "Export de mes données (RGPD)",
}


async def build_member_export_zip(db: AsyncSession, member: Member, requested_by: str) -> io.BytesIO:
    """Construit le ZIP RGPD (mes-donnees.html + donnees.json + README.txt)."""
    from sqlalchemy import select

    data = {"member": _serialize(member)}

    u = (await db.execute(select(User).where(User.member_id == member.id))).scalar_one_or_none()
    if u:
        d = _serialize(u)
        d.pop("password_hash", None)
        d.pop("reset_token", None)
        d.pop("totp_secret", None)
        data["user_account"] = d

    for label, sql in [
        ("attendances",
         "SELECT a.id, a.status, a.registered_at, m.meeting_date, m.title AS meeting_title "
         "FROM attendances a JOIN meetings m ON m.id = a.meeting_id WHERE a.member_id = :id "
         "ORDER BY m.meeting_date DESC"),
        ("messages_sent",
         "SELECT id, subject, body, sent_at FROM messages WHERE sender_id = :id ORDER BY sent_at DESC"),
        ("messages_received",
         "SELECT m.id, m.subject, m.body, m.sent_at FROM messages m "
         "JOIN message_recipients mr ON mr.message_id = m.id WHERE mr.member_id = :id "
         "ORDER BY m.sent_at DESC"),
        ("documents_authored",
         "SELECT id, name, created_at FROM documents WHERE author_id = :id ORDER BY created_at DESC"),
        ("planches_authored",
         "SELECT id, title, status, created_at, published_at FROM planches WHERE author_id = :id "
         "ORDER BY created_at DESC"),
        ("forum_messages",
         "SELECT fm.id, fs.title AS subject_title, fm.created_at FROM forum_messages fm "
         "JOIN forum_subjects fs ON fs.id = fm.subject_id WHERE fm.created_by_id = :id "
         "ORDER BY fm.created_at DESC"),
        ("news_authored",
         "SELECT id, title, created_at FROM news_articles WHERE created_by_id = :id ORDER BY created_at DESC"),
        ("poll_votes",
         "SELECT pv.id, p.title AS poll_title, po.label AS option_label, pv.voted_at "
         "FROM poll_votes pv JOIN polls p ON p.id = pv.poll_id "
         "JOIN poll_options po ON po.id = pv.option_id WHERE pv.member_id = :id "
         "ORDER BY pv.voted_at DESC"),
        ("tasks_assigned",
         "SELECT id, title, status, due_date FROM tasks WHERE assigned_to_id = :id ORDER BY due_date DESC"),
        ("task_comments",
         "SELECT id, task_id, content, created_at FROM task_comments WHERE author_id = :id "
         "ORDER BY created_at DESC"),
        ("audit_actions",
         "SELECT id, action, resource_type, target_label, created_at FROM audit_logs WHERE actor_id = :id "
         "ORDER BY created_at DESC LIMIT 200"),
    ]:
        try:
            r = await db.execute(sa_text(sql), {"id": member.id})
            rows = [dict(row._mapping) for row in r.fetchall()]
            for row in rows:
                for k, v in list(row.items()):
                    if hasattr(v, "isoformat"):
                        row[k] = v.isoformat()
            data[label] = rows
        except Exception as e:
            data[label] = {"_error": str(e)}

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mes-donnees.html", _build_html_report(data, member, requested_by))
        zf.writestr(
            "donnees.json",
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
        )
        zf.writestr(
            "README.txt",
            "Export RGPD - Portail Socrate\n"
            f"Membre : {member.last_name} {member.first_name} (id={member.id})\n"
            f"Date d'export : {datetime.now().isoformat()}\n"
            f"Demandé par : {requested_by}\n\n"
            "Ouvrez le fichier \"mes-donnees.html\" dans un navigateur (double-clic) pour\n"
            "une version lisible de vos données.\n"
            "Le fichier \"donnees.json\" contient les mêmes données au format brut,\n"
            "destiné à être relu par un autre logiciel si besoin.\n",
        )
    buf.seek(0)
    return buf


def _fmt_date(value) -> str:
    if not value:
        return "—"
    try:
        s = str(value)
        dt = datetime.fromisoformat(s[:19])
        return dt.strftime("%d/%m/%Y %H:%M") if dt.time().hour or dt.time().minute else dt.strftime("%d/%m/%Y")
    except Exception:
        return str(value)


def _section(title: str, rows: list, columns: list[tuple[str, str]]) -> str:
    """columns: liste de (clé, en-tête). Ignore la section si rows est vide."""
    if not rows or (isinstance(rows, dict) and "_error" in rows):
        return ""
    head = "".join(f"<th>{escape(h)}</th>" for _, h in columns)
    body_rows = []
    for row in rows:
        cells = "".join(f"<td>{escape(str(row.get(k, '') or '—'))}</td>" for k, _ in columns)
        body_rows.append(f"<tr>{cells}</tr>")
    return f"""
    <section>
      <h2>{escape(title)} <span class="count">({len(rows)})</span></h2>
      <table>
        <thead><tr>{head}</tr></thead>
        <tbody>{''.join(body_rows)}</tbody>
      </table>
    </section>"""


def _build_html_report(data: dict, member: Member, requested_by: str) -> str:
    m = data.get("member", {})
    u = data.get("user_account", {})

    profil_rows = [
        ("Civilité", m.get("civility")),
        ("Nom", f"{m.get('last_name', '')} {m.get('first_name', '')}"),
        ("Email", m.get("email")),
        ("Téléphone", m.get("phone")),
        ("Grade maçonnique", m.get("masonic_grade")),
        ("Fonction", m.get("lodge_function")),
        ("Statut", m.get("status")),
        ("Membre depuis", _fmt_date(m.get("membership_start_date"))),
        ("Identifiant de connexion", u.get("login")),
        ("2FA activée", "Oui" if u.get("totp_enabled") else "Non"),
        ("Dernière connexion", _fmt_date(u.get("last_login_at"))),
    ]
    profil_html = "".join(
        f"<tr><th>{escape(str(label))}</th><td>{escape(str(val)) if val not in (None, '') else '—'}</td></tr>"
        for label, val in profil_rows
    )

    def rows(key: str) -> list:
        """Retourne data[key] s'il s'agit bien d'une liste (jamais un {'_error': ...})."""
        val = data.get(key)
        return val if isinstance(val, list) else []

    attendances = rows("attendances")
    for a in attendances:
        a["status"] = ATTENDANCE_LABELS.get(a.get("status"), a.get("status"))
        a["meeting_date"] = _fmt_date(a.get("meeting_date"))
        a["registered_at"] = _fmt_date(a.get("registered_at"))

    for coll in ("messages_sent", "messages_received"):
        for row in rows(coll):
            row["sent_at"] = _fmt_date(row.get("sent_at"))

    for row in rows("planches_authored"):
        row["status"] = PLANCHE_STATUS_LABELS.get(row.get("status"), row.get("status"))
        row["published_at"] = _fmt_date(row.get("published_at")) if row.get("published_at") else "Non publiée"

    for row in rows("documents_authored"):
        row["created_at"] = _fmt_date(row.get("created_at"))
    for row in rows("forum_messages"):
        row["created_at"] = _fmt_date(row.get("created_at"))
    for row in rows("news_authored"):
        row["created_at"] = _fmt_date(row.get("created_at"))
    for row in rows("poll_votes"):
        row["voted_at"] = _fmt_date(row.get("voted_at"))
    for row in rows("tasks_assigned"):
        row["status"] = TASK_STATUS_LABELS.get(row.get("status"), row.get("status"))
        row["due_date"] = _fmt_date(row.get("due_date")) if row.get("due_date") else "—"
    for row in rows("task_comments"):
        row["created_at"] = _fmt_date(row.get("created_at"))
        content = row.get("content") or ""
        row["content"] = content[:150] + ("…" if len(content) > 150 else "")
    for row in rows("audit_actions"):
        row["action"] = AUDIT_ACTION_LABELS.get(row.get("action"), row.get("action"))
        row["created_at"] = _fmt_date(row.get("created_at"))

    sections = "".join([
        _section("Mes présences aux tenues", attendances,
                  [("meeting_date", "Date"), ("meeting_title", "Tenue"), ("status", "Statut")]),
        _section("Messages que j'ai envoyés", data.get("messages_sent") or [],
                  [("sent_at", "Date"), ("subject", "Sujet")]),
        _section("Messages que j'ai reçus", data.get("messages_received") or [],
                  [("sent_at", "Date"), ("subject", "Sujet")]),
        _section("Documents que j'ai déposés dans la bibliothèque", data.get("documents_authored") or [],
                  [("created_at", "Date"), ("name", "Nom du fichier")]),
        _section("Mes planches", data.get("planches_authored") or [],
                  [("title", "Titre"), ("status", "Statut"), ("published_at", "Publiée le")]),
        _section("Mes messages sur le forum", data.get("forum_messages") or [],
                  [("created_at", "Date"), ("subject_title", "Sujet")]),
        _section("Actualités que j'ai publiées", data.get("news_authored") or [],
                  [("created_at", "Date"), ("title", "Titre")]),
        _section("Mes votes aux sondages", data.get("poll_votes") or [],
                  [("voted_at", "Date"), ("poll_title", "Sondage"), ("option_label", "Mon vote")]),
        _section("Tâches qui me sont assignées", data.get("tasks_assigned") or [],
                  [("due_date", "Échéance"), ("title", "Tâche"), ("status", "Statut")]),
        _section("Mes commentaires sur les tâches", data.get("task_comments") or [],
                  [("created_at", "Date"), ("content", "Commentaire")]),
        _section("Historique de mes actions (connexions…)", data.get("audit_actions") or [],
                  [("created_at", "Date"), ("action", "Action")]),
    ])

    return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Mes données — {escape(m.get('last_name', ''))} {escape(m.get('first_name', ''))}</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", Arial, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1.5rem; color: #1a202c; line-height: 1.5; }}
  h1 {{ font-size: 1.4rem; margin-bottom: 0.25rem; }}
  .subtitle {{ color: #718096; font-size: 0.9rem; margin-bottom: 2rem; }}
  h2 {{ font-size: 1.05rem; margin-top: 2.25rem; margin-bottom: 0.75rem; border-bottom: 2px solid #2c7a7b; padding-bottom: 0.35rem; color: #234e52; }}
  .count {{ font-weight: normal; color: #a0aec0; font-size: 0.85rem; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
  th, td {{ text-align: left; padding: 0.5rem 0.75rem; border-bottom: 1px solid #e2e8f0; vertical-align: top; }}
  thead th {{ background: #f7fafc; color: #4a5568; font-weight: 600; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.02em; }}
  section table th:first-child, section table td:first-child {{ width: 30%; }}
  table.profil th {{ background: none; text-transform: none; font-weight: 600; width: 220px; color: #2d3748; }}
  .footer {{ margin-top: 3rem; padding-top: 1rem; border-top: 1px solid #e2e8f0; color: #a0aec0; font-size: 0.8rem; }}
  @media print {{ body {{ margin: 0; }} }}
</style>
</head>
<body>
  <h1>Mes données personnelles — Portail Socrate</h1>
  <p class="subtitle">
    Export du {escape(_fmt_date(datetime.now().isoformat()))} — demandé par {escape(requested_by)}
  </p>

  <section>
    <h2>Mon profil</h2>
    <table class="profil">{profil_html}</table>
  </section>

  {sections}

  <p class="footer">
    Ce document liste l'ensemble des données personnelles vous concernant conservées
    par le portail de la loge, hors fichiers uploadés (pièces jointes, documents).
    En cas de question sur ces données, contactez le Secrétaire ou le Vénérable Maître.
  </p>
</body>
</html>"""
