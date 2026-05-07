# Portail Socrate — Statut de développement
> Dernière mise à jour : 2026-05-06

---

## Stack technique
- **Backend** : FastAPI + SQLAlchemy async (aiosqlite / SQLite local, PostgreSQL en prod)
- **Frontend** : Jinja2 + Tailwind CDN + Alpine.js + HTMX
- **PWA** : manifest.json + service worker + bottom nav mobile
- **PDF** : WeasyPrint (programmes) + PyMuPDF (import logo vecteur)
- **Auth** : session cookie HTTP-only, bcrypt
- **Email** : SMTP configurable dans les paramètres loge

---

## Modules — état actuel

### ✅ TERMINÉ

| Module | Détail |
|--------|--------|
| Auth | Login/logout, session, admin flag |
| Membres | CRUD complet, civilité F∴/S∴, grade, statut, office rituel dynamique |
| Offices rituels | Table `lodge_offices` configurable (label éditable, Couvreur etc.) |
| Tenues | Création, gestion, types, agapes on/off, token public |
| Inscription visiteurs | Formulaire public (F∴/S∴, grade, loge, V∴M∴ checkbox, commentaire) |
| Programmes PDF | Généré depuis tenues, A4 recto/verso, portrait pleine page, OJ 2 colonnes |
| Finance (base) | Structure de base |
| Paramètres loge | Identité, temple, VM, Secrétaire, officiers, Google Maps |
| PWA / Responsive | Bottom nav mobile, sidebar desktop redesignée (style Linear), safe-area iOS |
| Base de données | SQLite local (`socrate_local.db`), migrations manuelles via scripts Python |

---

### 🔴 EN COURS / PROCHAIN SPRINT

#### 1. Module Agapes — complétion
**Objectif** : remplacer `agapes.amisdesocrate.fr` intégralement

- [ ] Capacité max par tenue (champ `capacity` sur `Meeting`) + compteur temps réel
- [ ] Fermeture automatique des inscriptions J-1 à 8h (ou manuellement)
- [ ] Inscription membre par dropdown + PIN (pas de login requis pour s'inscrire aux agapes)
- [ ] Logique conditionnelle : si pas tenue → pas agapes
- [ ] Case "recevoir les programmes de la loge" sur le formulaire visiteur (déjà en base `program_optin`)
- [ ] Lien agapes généré automatiquement dans le PDF programme
- [ ] Dashboard agapes : liste inscrits, compteur places, export liste

#### 2. Module Présence & Assiduité
**Objectif** : remplacer `/presence/dashboard/`

- [ ] Émargement le soir de la tenue (Présent / Excusé / Absent) — interface rapide
- [ ] Modèle `Attendance` déjà en place, à brancher sur l'UI
- [ ] Dashboard assiduité : taux par membre, par année maçonnique
- [ ] Signalement absences répétées (>3 sans excuse)
- [ ] Vue par tenue : qui était là

---

### 🟡 PLANIFIÉ

#### 3. Module Trésorerie complet
**Objectif** : remplacer `/cotisations`

- [ ] 5 tranches avec coefficients (0.4 / 0.6 / 0.8 / 1.2 / 1.6) autour montant référence
- [ ] Capitation nationale (183.5€) + régionale (4€) séparées du montant loge
- [ ] Affectation tranche par membre
- [ ] Budget total, répartition catégories, distribution membres/tranche
- [ ] Statut cotisation : payé / en attente / en retard
- [ ] Relance email automatique
- [ ] Export PDF dashboard trésorier

#### 4. Module Membres & Comptabilité
**Objectif** : remplacer `/asso/`

- [ ] Import CSV membres (seuls / abonnements seuls / combiné)
- [ ] Abonnements (type, montant, fréquence)
- [ ] Paiements (date, mode, montant, lié à membre)
- [ ] Comptabilité associative (recettes/dépenses, catégories)
- [ ] Export comptable

#### 5. Module Communication
**Objectif** : remplacer les listes cPanel manuelles

- [ ] Synchronisation listes email cPanel via API cPanel (token déjà prévu dans `LodgeSettings`)
- [ ] Envoi convocations par email (SMTP configuré) avec PDF en pièce jointe
- [ ] Gestion opt-in / opt-out par membre
- [ ] Templates email (convocation, rappel, relance cotisation)

#### 6. Module Visio
**Objectif** : intégrer une solution de visio (Jitsi / BBB)

- [ ] Champ `visio_provider` + `visio_server_url` + `visio_room_prefix` déjà dans `LodgeSettings`
- [ ] Génération automatique d'un lien de salle par tenue
- [ ] Lien visio dans la convocation email et dans le programme PDF
- [ ] Support Jitsi Meet (self-hosted ou meet.jit.si) et BigBlueButton

---

### 🟢 FUTUR

#### 7. Module Agora — Espace collaboratif
**Objectif** : remplacer `www.amisdesocrate.fr`

- [ ] Calendrier (vue mensuelle, agenda, export `.ics`)
- [ ] Bibliothèque de planches (upload PDF, accès par grade)
- [ ] Import backup Agora existant (à étudier selon format dispo)
- [ ] Forum interne (topics, réponses, grade-gate)
- [ ] Tâches / Commissions (assignation, suivi)
- [ ] Contacts maçonniques (carnet visiteurs, loges amies)
- [ ] Espace "Cloud Visiteurs et Maçons Passant"

#### 8. Module Messagerie interne
**Objectif** : remplacer Telegram

- [ ] Messages directs entre membres
- [ ] Canaux par thème (chantiers, commissions, annonces)
- [ ] Restriction de contenu par grade (rituels, etc.)
- [ ] Notifications push PWA
- [ ] Historique et recherche

---

## Architecture fichiers clés

```
app/
├── main.py                  # FastAPI app + lifespan + routers
├── database.py              # Engine SQLAlchemy + Base + get_db
├── config.py                # Settings (dotenv)
├── dependencies.py          # Auth, permissions, hash_password
├── models/
│   ├── identity.py          # Member, User, MasonicGrade, LodgeFunction, Group...
│   ├── lodge.py             # LodgeSettings, LodgeOffice, MasonicYear
│   └── meetings.py          # Meeting, Attendance, Visitor, MeetingVisitor...
├── routers/
│   ├── auth.py              # Login / logout
│   ├── members.py           # CRUD membres + offices
│   ├── meetings.py          # Tenues + inscription publique
│   ├── programs.py          # Génération PDF programmes
│   ├── finance.py           # Trésorerie (base)
│   └── settings.py          # Paramètres loge + officiers
└── templates/
    ├── base.html            # Layout PWA (sidebar desktop + bottom nav mobile)
    └── pages/
        ├── dashboard.html
        ├── members/         # list.html, detail.html, form.html
        ├── meetings/        # list, detail, form, register_public
        ├── programs/        # detail.html (PDF A4)
        ├── finance/
        └── settings/        # index.html

app/static/
├── img/
│   ├── sceau-socrate-transparent.png   # Logo noir sur transparent (sidebar, programmes)
│   └── sceau-socrate-blanc.png         # Logo blanc sur transparent
├── manifest.json
└── sw.js

socrate_local.db             # Base SQLite locale
.env                         # Config locale (DATABASE_URL, SECRET_KEY, SMTP...)
seed.py                      # Données initiales (idempotent)
fix_lodge_offices.py         # Migration offices (à garder pour référence)
```

---

## Points d'attention techniques

- **Migrations** : pas d'Alembic, migrations manuelles via scripts Python (`ALTER TABLE` ou drop/recreate)
- **SQLite → PostgreSQL** : prévu pour la prod, modèles compatibles
- **Nommage contraintes** : `naming_convention` dans `database.py` — toujours utiliser le même `Base`
- **Logo** : `sceau-socrate-transparent.png` généré depuis PDF vecteur via PyMuPDF à 4x
- **Impression PDF** : Marges=Aucune, Échelle=100% dans le navigateur — avertissement affiché
- **Civilité** : `F` = Frère, `S` = Sœur (loge mixte) — pas de TF∴ ni TS∴
- **Grades** : masculin uniquement au Rite Français Philosophique (Apprenti, Compagnon, Maître)
- **Offices** : table `lodge_offices` (configurable) — plus d'enum `LodgeFunction` dans les forms

---

## Pour reprendre demain

1. Lancer l'app : `python -m uvicorn app.main:app --reload`
2. Ouvrir : http://localhost:8000
3. Login : `admin` / `admin`
4. Prochain chantier : **Module Agapes — complétion** (voir checklist ci-dessus)

### Ordre d'attaque recommandé pour Agapes :
1. Ajouter `capacity` + `registration_closes_at` sur `Meeting` (migration)
2. Formulaire paramétrage agapes dans la fiche tenue (admin)
3. Compteur places en temps réel sur la page d'inscription publique
4. Inscription membre par dropdown + PIN
5. Fermeture automatique (tâche de fond ou vérification au chargement)
6. Dashboard agapes (liste inscrits, export)
