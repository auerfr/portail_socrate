"""Gestion des templates emails éditables.

Chaque template est identifié par une clé (ex: 'email_tpl_new_message').
La valeur stockée dans SystemSetting est un dict :
  { "subject": "...", "body_html": "...", "body_text": "..." }

Variables disponibles dans les templates (syntaxe {{ var }}) :
  lodge_name, member_name, portal_url, + variables spécifiques au type.

Si un champ est vide → on utilise la valeur par défaut codée en dur.
"""
import re
from typing import Optional

_VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")

TEMPLATE_KEYS = {
    "email_tpl_new_message": {
        "label": "Nouvelle messagerie interne",
        "description": "Envoyé quand un membre reçoit un message interne.",
        "vars": ["lodge_name", "portal_url", "sender_name", "subject", "body_preview", "message_url"],
        "default_subject": "[{{ lodge_name }}] Nouveau message : {{ subject }}",
    },
    "email_tpl_task_assigned": {
        "label": "Tâche assignée (projets)",
        "description": "Envoyé quand une tâche est assignée à un membre.",
        "vars": ["lodge_name", "portal_url", "task_title", "project_name", "project_url"],
        "default_subject": "[{{ lodge_name }}] 📋 Tâche assignée : {{ task_title }}",
    },
    "email_tpl_meeting_reminder": {
        "label": "Rappel de tenue",
        "description": "Rappel envoyé avant une tenue.",
        "vars": ["lodge_name", "portal_url", "meeting_date", "meeting_type", "meeting_location"],
        "default_subject": "[{{ lodge_name }}] Rappel tenue du {{ meeting_date }}",
    },
    "email_tpl_password_reset": {
        "label": "Réinitialisation mot de passe",
        "description": "Email envoyé pour réinitialiser le mot de passe.",
        "vars": ["lodge_name", "portal_url", "reset_url", "member_name"],
        "default_subject": "[{{ lodge_name }}] Réinitialisation de votre mot de passe",
    },
    "email_tpl_backup_report": {
        "label": "Rapport de sauvegarde",
        "description": "Email envoyé à l'admin après une sauvegarde.",
        "vars": ["lodge_name", "backup_filename", "backup_size", "backup_date"],
        "default_subject": "[{{ lodge_name }}] ✅ Sauvegarde du {{ backup_date }}",
    },
    "email_tpl_mailing_campaign": {
        "label": "Campagne de diffusion (pied de page)",
        "description": "Personnalise le pied de page de chaque campagne de diffusion.",
        "vars": ["lodge_name", "portal_url", "list_name", "unsub_url"],
        "default_subject": "",  # sujet géré par la campagne elle-même
    },
}


def render_template(template_str: str, variables: dict) -> str:
    """Substitue {{ var }} dans le template."""
    return _VAR_RE.sub(lambda m: str(variables.get(m.group(1), m.group(0))), template_str)


async def get_template(key: str, db=None) -> dict:
    """Retourne la config du template (depuis SystemSetting ou défaut)."""
    from app.services.settings_store import get_setting
    stored = await get_setting(key, db=db) or {}
    meta = TEMPLATE_KEYS.get(key, {})
    return {
        "key": key,
        "label": meta.get("label", key),
        "description": meta.get("description", ""),
        "vars": meta.get("vars", []),
        "default_subject": meta.get("default_subject", ""),
        "subject": stored.get("subject", ""),
        "body_html": stored.get("body_html", ""),
        "body_text": stored.get("body_text", ""),
    }


async def save_template(db, key: str, subject: str, body_html: str,
                        body_text: str, actor_id: Optional[int] = None) -> None:
    from app.services.settings_store import set_setting
    await set_setting(db, key, {
        "subject": subject.strip(),
        "body_html": body_html.strip(),
        "body_text": body_text.strip(),
    }, actor_id=actor_id)


async def get_subject(key: str, variables: dict, db=None) -> str:
    """Retourne le sujet rendu pour un template donné."""
    tpl = await get_template(key, db=db)
    raw = tpl["subject"] or tpl["default_subject"]
    return render_template(raw, variables)
