"""Export RGPD (droit à la portabilité) — ZIP JSON des données d'un membre.

Utilisé à la fois par l'export admin (n'importe quel membre) et par le
self-service (le membre exporte ses propres données)."""
import io
import json
import zipfile
from datetime import datetime

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


async def build_member_export_zip(db: AsyncSession, member: Member, requested_by: str) -> io.BytesIO:
    """Construit le ZIP RGPD (donnees.json + README.txt) pour un membre donné."""
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
        ("attendances",            "SELECT * FROM attendances WHERE member_id = :id"),
        ("messages_sent",          "SELECT id, subject, body, sent_at FROM messages WHERE sender_id = :id"),
        ("messages_received",      "SELECT m.id, m.subject, m.body, m.sent_at FROM messages m "
                                    "JOIN message_recipients mr ON mr.message_id = m.id WHERE mr.member_id = :id"),
        ("documents_authored",     "SELECT id, name, created_at FROM documents WHERE author_id = :id"),
        ("planches_authored",      "SELECT id, title, status, created_at, published_at FROM planches WHERE author_id = :id"),
        ("forum_messages",         "SELECT id, subject_id, created_at FROM forum_messages WHERE created_by_id = :id"),
        ("news_authored",          "SELECT id, title, created_at FROM news_articles WHERE author_id = :id"),
        ("poll_votes",             "SELECT id, poll_id, option_id, voted_at FROM poll_votes WHERE member_id = :id"),
        ("tasks_assigned",         "SELECT id, title, status, due_date FROM tasks WHERE assigned_to_id = :id"),
        ("task_comments",          "SELECT id, task_id, content, created_at FROM task_comments WHERE author_id = :id"),
        ("audit_actions",          "SELECT id, action, resource_type, target_label, created_at FROM audit_logs WHERE actor_id = :id"),
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
            "Ce ZIP contient l'ensemble des données personnelles associées à ce membre\n"
            "dans la base de la loge, hors fichiers uploadés.\n",
        )
    buf.seek(0)
    return buf
