"""Normalise les noms de loges et d'orients dans la table visitors."""
import sqlite3

DB = "socrate_local.db"

# Mapping lodge_name → forme canonique
LODGE_MAP = {
    # ARBRE
    "ARBRE ET LA PIERRE":               "ARBRE ET LA PIERRE",
    "ARBRE et la PIERRE":               "ARBRE ET LA PIERRE",
    # Amis du Jeune Henri
    "AMIS DU JEUNE HENRI":              "Les Amis du Jeune Henri",
    "Amis du Jeune Henri":              "Les Amis du Jeune Henri",
    "Les Amis du Jeune Henri":          "Les Amis du Jeune Henri",
    # Saint Antoine
    "De Saint Antoine et des Amis Réunis":   "Saint Antoine et des Amis Réunis",
    "Saint Antoine et des Amis Réunis":      "Saint Antoine et des Amis Réunis",
    # Enfants double union
    "ENFANT DE LA DOUBLE UNION":        "Enfants de la Double Union",
    "ENFANTS de la double union":       "Enfants de la Double Union",
    # Héliopolis
    "HELIOPOLIS RENAISSANTE":           "Héliopolis Renaissante",
    "HELIOPOLIS Renaissante":           "Héliopolis Renaissante",
    "HEliopolis Renaissante":           "Héliopolis Renaissante",
    # Étoile
    "Etoile au coeur du Granit":        "L'Étoile au Cœur du Granit",
    "L'Etoile au Coeur du Granit":      "L'Étoile au Cœur du Granit",
    "L'Étoile au Cœur du Granit":       "L'Étoile au Cœur du Granit",
    "L'étoile au coeur du Granit":      "L'Étoile au Cœur du Granit",
    # Le Travail
    "LE TRAVAIL":                       "Le Travail",
    "Le TRAVAIL":                       "Le Travail",
    "Le travail":                       "Le Travail",
    # Maître Villard
    "MAitre Villars de Honnecourt":     "Maître Villard de Honnecourt",
    "Maitre Villard de Honnecourt":     "Maître Villard de Honnecourt",
    "Maitre Villard de Honnercourt":    "Maître Villard de Honnecourt",
    # La Noble Amitié
    "La Noble amitie":                  "La Noble Amitié",
    "La noble amitié":                  "La Noble Amitié",
    # Progrès Émilie
    "Progres et Diversité Emilie du Chatelet":       "Progrès et Diversité Émilie du Châtelet",
    "Progrès et diversité Emilie du Chatelet":       "Progrès et Diversité Émilie du Châtelet",
    "Progrès et diversité.Emilie du Châtelet":       "Progrès et Diversité Émilie du Châtelet",
    # Sirius et Vega
    "SIRIUS ET VEGA":                   "Sirius et Vega",
    # Vitruve
    "VITRUVE":                          "Vitruve",
    # La Vita
    "la vita":                          "La Vita",
    # Les 3 Globes
    "les 3 Globes":                     "Les 3 Globes",
    # Les 3 Piliers
    "Les 3 PILIERS":                    "Les 3 Piliers",
    # La Triple équerre
    "La Triple equerre":                "La Triple Équerre",
    # RL XXIème
    "RL XXIeme Siècle":                 "RL XXIème Siècle",
    # Médiation
    "MEDIATION":                        "Médiation",
    # AGORA → déjà ok
    "AGORA":                            "Agora",
    # STOA → ok
    "STOA":                             "Stoa",
}

# Mapping orient_city → forme canonique
ORIENT_MAP = {
    "METZ":               "Metz",
    "MEtz":               "Metz",
    "metz":               "Metz",
    "REMIREMONT":         "Remiremont",
    "PONT-A-MOUSSON":     "Pont-à-Mousson",
    "Pont-a-Mousson":     "Pont-à-Mousson",
    "PONT_A_MOUSSON":     "Pont-à-Mousson",
    "PONT-A_MOUSSON":     "Pont-à-Mousson",
    "BRIEY":              "Briey",
    "THIONVILLE":         "Thionville",
    "NANCY":              "Nancy",
    "nancy":              "Nancy",
    "SAINT-AVOLD":        "Saint-Avold",
    "BAR-LE-DUC":         "Bar-le-Duc",
    "LUXEMBOURG":         "Luxembourg",
}

con = sqlite3.connect(DB)
cur = con.cursor()

# Update lodge_name
lodge_count = 0
for old, new in LODGE_MAP.items():
    n = cur.execute(
        "UPDATE visitors SET lodge_name=? WHERE lodge_name=? AND lodge_name!=?",
        (new, old, new)
    ).rowcount
    if n:
        print(f"  lodge: {repr(old)} → {repr(new)} ({n} lignes)")
        lodge_count += n

# Update orient_city
orient_count = 0
for old, new in ORIENT_MAP.items():
    n = cur.execute(
        "UPDATE visitors SET orient_city=? WHERE orient_city=? AND orient_city!=?",
        (new, old, new)
    ).rowcount
    if n:
        print(f"  orient: {repr(old)} → {repr(new)} ({n} lignes)")
        orient_count += n

con.commit()
con.close()
print(f"\n✓ {lodge_count} noms de loges normalisés, {orient_count} orients normalisés.")
