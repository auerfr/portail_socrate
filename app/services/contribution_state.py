"""État temporel de l'appel à tranche.

Renvoie un dict décrivant clairement où on en est, avec un libellé court
pour l'UI et un booléen `is_active` qui fait foi pour autoriser ou non
l'enregistrement de tranches par les FF/SS.
"""
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from app.models.finance import ContributionConfig


@dataclass
class AppelState:
    is_active: bool       # autorisé pour les membres ? (saisie tranche possible)
    label: str            # libellé court pour badge
    color: str            # "green" / "amber" / "gray" / "rose"
    detail: str           # phrase explicative
    is_window: bool       # date courante dans la fenêtre [opens_at, closes_at]
    is_closed_manual: bool  # cloturé manuellement (tier_selection_closed_at posé)


def get_appel_state(cfg: Optional[ContributionConfig], today: Optional[date] = None) -> AppelState:
    """Détermine l'état actuel de l'appel à tranche."""
    today = today or date.today()
    if not cfg:
        return AppelState(False, "Non configuré", "gray",
                          "Aucune configuration de cotisations pour cette année.",
                          False, False)

    opens   = cfg.tier_selection_opens_at
    closes  = cfg.tier_selection_closes_at
    closed  = cfg.tier_selection_closed_at
    bool_on = bool(cfg.tier_selection_open)

    in_window = False
    if opens and closes:
        in_window = opens <= today <= closes

    # Cas 1 : clos manuellement
    if closed is not None:
        return AppelState(False, "Clos", "gray",
                          f"Appel clos le {closed.strftime('%d/%m/%Y')}.",
                          in_window, True)

    # Cas 2 : fenêtre dépassée
    if closes and today > closes:
        return AppelState(False, "Clos (échéance)", "gray",
                          f"Fenêtre terminée le {closes.strftime('%d/%m/%Y')}.",
                          False, False)

    # Cas 3 : avant la date d'ouverture
    if opens and today < opens:
        days = (opens - today).days
        return AppelState(False, "À venir", "amber",
                          f"Ouvre le {opens.strftime('%d/%m/%Y')} (dans {days} j).",
                          False, False)

    # Cas 4 : dans la fenêtre + flag actif
    if bool_on and in_window:
        days_left = (closes - today).days if closes else None
        d = f" (clôture le {closes.strftime('%d/%m/%Y')})" if closes else ""
        return AppelState(True, "Ouvert", "green",
                          f"Les membres peuvent saisir leur tranche{d}.",
                          True, False)

    # Cas 5 : fenêtre OK mais flag désactivé
    if in_window and not bool_on:
        return AppelState(False, "Désactivé", "amber",
                          "Fenêtre en cours mais l'appel n'a pas été lancé.",
                          True, False)

    # Cas 6 : flag actif sans dates → laisser passer (rétrocompat)
    if bool_on and not opens and not closes:
        return AppelState(True, "Ouvert (sans dates)", "amber",
                          "Aucune fenêtre temporelle définie.",
                          False, False)

    # Cas par défaut
    return AppelState(False, "Fermé", "gray",
                      "L'appel à tranche n'est pas actif.",
                      False, False)
