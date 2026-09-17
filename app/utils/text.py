"""Petits utilitaires de formatage de texte."""


def masonic_write(name: str) -> str:
    """Écriture maçonnique d'un nom de famille : chaque lettre séparée par
    un triple point (∴), par souci de confidentialité dans les documents
    écrits (tracés). Les espaces, tirets et apostrophes des noms composés
    sont conservés tels quels entre les groupes de lettres.
    Ex : "Dupont" -> "D∴U∴P∴O∴N∴T∴", "Del Vecchio" -> "D∴E∴L∴ V∴E∴C∴C∴H∴I∴O∴"
    """
    if not name:
        return name
    return "".join(f"{c}∴" if c.isalpha() else c for c in name.upper())
