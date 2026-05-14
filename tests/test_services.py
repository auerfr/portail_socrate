"""Tests des services métier (sans réseau, sans DB pour les plus simples)."""
import pytest


def test_mailing_tokens_roundtrip():
    """make/verify_unsubscribe_token est symétrique."""
    from app.services.mailing import make_unsubscribe_token, verify_unsubscribe_token
    tok = make_unsubscribe_token(42, "m", 7)
    result = verify_unsubscribe_token(tok)
    assert result == (42, "m", 7)


def test_mailing_token_tampered():
    """Un token altéré est rejeté."""
    from app.services.mailing import verify_unsubscribe_token
    assert verify_unsubscribe_token("42.m.FAKESIG") is None
    assert verify_unsubscribe_token("not_a_token") is None


def test_tracking_tokens_roundtrip():
    """make/verify_tracking_token est symétrique."""
    from app.services.mailing import make_tracking_token, verify_tracking_token
    tok = make_tracking_token(123, "o")
    result = verify_tracking_token(tok)
    assert result == (123, "o")

    tok2 = make_tracking_token(99, "c")
    assert verify_tracking_token(tok2) == (99, "c")


def test_labels_get_label_enum():
    """get_label retourne la valeur .value d'un enum sans override."""
    from app.services.labels import get_label
    from app.models.identity import MasonicGrade
    result = get_label(MasonicGrade.APPRENTI)
    assert result == "APPRENTI"


def test_labels_get_label_none():
    """get_label retourne '' si None."""
    from app.services.labels import get_label
    assert get_label(None) == ""


def test_doc_index_extract_text_missing_file():
    """extract_text retourne '' si le fichier n'existe pas."""
    from app.services.doc_index import extract_text
    result = extract_text("/chemin/inexistant.pdf", "application/pdf")
    assert result == ""


def test_contribution_state_no_config():
    """get_appel_state retourne un état cohérent si pas de config."""
    from app.services.contribution_state import get_appel_state
    state = get_appel_state(None)
    assert not state.is_active
    assert state.color == "gray"


def test_email_template_render():
    """render_template substitue les variables correctement."""
    from app.services.email_templates import render_template
    result = render_template("Bonjour {{ prenom }} !", {"prenom": "Jean"})
    assert result == "Bonjour Jean !"
    # Variable inconnue → laissée telle quelle
    result2 = render_template("{{ inconnu }}", {})
    assert "inconnu" in result2


def test_permissions_all_defined():
    """ALL_PERMISSIONS est non vide et contient les permissions attendues."""
    from app.services.permissions import ALL_PERMISSIONS
    assert len(ALL_PERMISSIONS) >= 5
    assert "can_manage_finance" in ALL_PERMISSIONS
    assert "can_send_mailing" in ALL_PERMISSIONS
