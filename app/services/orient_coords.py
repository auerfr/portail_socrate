"""Coordonnées (latitude, longitude) des orients — pour la carte des maçons passants.

Table locale, sans service de géocodage extérieur. Un orient absent de la
table est simplement listé à part sur la page ; il suffit de l'ajouter ici.
"""
import re
import unicodedata

ORIENT_COORDS: dict[str, tuple[float, float]] = {
    # Moselle
    "Metz": (49.119, 6.176), "Montigny-lès-Metz": (49.100, 6.154), "Woippy": (49.151, 6.151),
    "Thionville": (49.357, 6.168), "Yutz": (49.360, 6.190), "Thionville-Yutz": (49.358, 6.180),
    "Hayange": (49.329, 6.062), "Florange": (49.322, 6.120), "Fameck": (49.300, 6.110),
    "Hagondange": (49.250, 6.163), "Saint-Avold": (49.104, 6.707), "Forbach": (49.188, 6.896),
    "Creutzwald": (49.205, 6.695), "Boulay": (49.183, 6.493), "Sarreguemines": (49.110, 7.070),
    "Bitche": (49.052, 7.430), "Sarrebourg": (48.735, 7.054), "Château-Salins": (48.819, 6.513),
    "Dieuze": (48.812, 6.718),
    # Meurthe-et-Moselle
    "Nancy": (48.692, 6.184), "Vandoeuvre-lès-Nancy": (48.656, 6.170), "Villers-lès-Nancy": (48.673, 6.153),
    "Laxou": (48.684, 6.149), "Pont-à-Mousson": (48.904, 6.054), "Mousson": (48.906, 6.083),
    "Toul": (48.675, 5.891),
    "Lunéville": (48.592, 6.496), "Briey": (49.249, 5.940), "Jarny": (49.159, 5.878),
    "Longwy": (49.519, 5.766), "Villerupt": (49.467, 5.930),
    # Meuse, Vosges
    "Bar-le-Duc": (48.773, 5.160), "Verdun": (49.160, 5.384), "Commercy": (48.763, 5.592),
    "Epinal": (48.172, 6.449), "Épinal": (48.172, 6.449), "Remiremont": (48.017, 6.592),
    "Saint-Dié-des-Vosges": (48.285, 6.950), "Gérardmer": (48.072, 6.877),
    "Neufchâteau": (48.355, 5.696), "Mirecourt": (48.299, 6.134),
    # Alsace
    "Strasbourg": (48.573, 7.752), "Colmar": (48.079, 7.358), "Mulhouse": (47.750, 7.335),
    "Guebwiller": (47.910, 7.210), "Saverne": (48.741, 7.362), "Haguenau": (48.815, 7.790),
    "Sélestat": (48.259, 7.454),
    # Champagne-Ardenne
    "Reims": (49.258, 4.032), "Châlons-en-Champagne": (48.957, 4.363), "Epernay": (49.040, 3.960),
    "Charleville-Mézières": (49.762, 4.726), "Sedan": (49.702, 4.940), "Troyes": (48.297, 4.074),
    "Chaumont": (48.111, 5.139), "Langres": (47.863, 5.333), "Saint-Dizier": (48.638, 4.949),
    "Vitry-le-François": (48.725, 4.585),
    # Franche-Comté, Bourgogne
    "Besançon": (47.238, 6.024), "Belfort": (47.638, 6.863), "Vesoul": (47.620, 6.155),
    "Montbéliard": (47.510, 6.798), "Dijon": (47.322, 5.041),
    # Pays voisins
    "Luxembourg": (49.611, 6.130), "Esch-sur-Alzette": (49.496, 5.980), "Sarrebruck": (49.234, 6.997),
    "Trèves": (49.750, 6.637), "Bâle": (47.560, 7.588), "Bruxelles": (50.850, 4.350),
    "Berlin": (52.520, 13.405),
    # Plus loin
    "Paris": (48.857, 2.352), "Boulogne-Billancourt": (48.835, 2.240), "Lagny-sur-Marne": (48.873, 2.709),
    "Compiègne": (49.418, 2.826), "Lyon": (45.764, 4.836), "Annecy": (45.899, 6.129),
    "Marseille": (43.296, 5.370), "Antibes": (43.580, 7.125), "Nice": (43.710, 7.262),
    "Granville": (48.837, -1.597),
}


def _key(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\bst\b", "saint", s)
    return re.sub(r"[^a-z]", "", s)


_BY_KEY = {_key(name): coords for name, coords in ORIENT_COORDS.items()}


def orient_coords(name: str) -> tuple[float, float] | None:
    """Coordonnées d'un orient, quelle que soit la graphie (casse, accents, tirets)."""
    return _BY_KEY.get(_key(name))
