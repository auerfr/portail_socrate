"""Petits utilitaires de formatage de texte."""

_VOYELLES = set("AEIOUÀÂÄÆÈÉÊËÎÏŒÔÖÙÛÜ")


def masonic_write(name: str) -> str:
    """Écriture maçonnique d'un nom de famille, par souci de confidentialité :
    consonnes uniquement (voyelles, espaces et signes retirés), comme déjà
    utilisé pour les membres non autorisés à voir un nom en clair (cf.
    anon_nom dans template_engine.py, qui réutilise cette même fonction).
    Ex : "Dupont" -> "DPNT", "Agnouna Onana" -> "GNNNN".
    Repli sur une initiale si moins de 2 consonnes (ex : nom d'une lettre).
    """
    if not name:
        return name
    upper = name.upper()
    consonnes = [c for c in upper if c.isalpha() and c not in _VOYELLES]
    if len(consonnes) < 2:
        return (upper[0] + "…") if upper else "…"
    return "".join(consonnes)
