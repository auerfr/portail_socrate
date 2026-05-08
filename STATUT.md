# Portail Socrate — Statut de développement
> Dernière mise à jour : 2026-05-08

---

## Stack technique
- **Backend** : FastAPI + SQLAlchemy async (aiosqlite / SQLite local, PostgreSQL en prod)
- **Frontend** : Jinja2 + Tailwind CDN + Alpine.js + HTMX
- **PWA** : manifest.json + service worker + bottom nav mobile
- **PDF** : WeasyPrint (programmes) + PyMuPDF (import logo vecteur)
- **Auth** : session cookie HTTP-only, bcrypt
- **Email** : SMTP configurable dans les paramètres loge (service email.py opérationnel)

---

## Modules — état actuel

### ✅ TERMINÉ

| Module | Détail |
|--------|--------|
| **Auth** | Login/logout, session, admin flag, changement MDP utilisateur, reset MDP admin |
| **Membres** | CRUD complet, civilité F∴/S∴, grade, statut, office rituel dynamique, import CSV |
| **Offices rituels** | Table `lodge_offices` configurable (label éditable, Couvreur etc.) |
| **Tenues** | Création, gestion, types, agapes on/off, token public, tracé (Quill.js) |
| **Agapes** | Capacité max, fermeture automatique, inscription membre (PIN), compteur temps réel, export Excel, dashboard, page banquet |
| **Inscription visiteurs** | Formulaire public (F∴/S∴, grade, loge, V∴M∴ checkbox, commentaire, opt-in programme) |
| **Présence & Assiduité** | Émargement (Présent/Excusé/Absent), ajout/suppression passants, autocomplete loge/obédience, dashboard tri colonnes, bilan annuel enrichi, distribution anonymisée, top 5 visiteurs, export CSV |
| **Programmes PDF** | Généré depuis tenues, A4 recto/verso, portrait pleine page, OJ 2 colonnes, QR code, création rétrospective, édition après coup |
| **Finance / Trésorerie** | 5 tranches coefficients (0.4–1.6), budget lignes/catégories, transactions, statuts paiement (payé/attente/retard), export CSV, détail par membre, bilan annuel |
| **GED / Bibliothèque** | Espaces → Dossiers → Fichiers, upload, téléchargement, prévisualisation, liens externes, gestion admin |
| **Chat interne** | Canaux publics, groupes privés, messages directs, historique, pastilles non-lus |
| **Messagerie** | Messagerie ciblée par groupes, accusés de lecture |
| **Calendrier** | Vue mensuelle, agenda liste, création événements, export iCalendar (.ics) |
| **Groupes** | Gestion de groupes de membres, types, ciblage |
| **Annonces** | Module annonces interne |
| **Paramètres loge** | Identité, temple, VM, Secrétaire, officiers, SMTP, Google Maps, notifications |
| **PWA / Responsive** | Bottom nav mobile, sidebar desktop redesignée (style Linear), safe-area iOS |
| **Base de données** | SQLite local (`socrate_local.db`), migrations manuelles via scripts Python |

---

### 🟡 EN COURS / PARTIELLEMENT FAIT

#### 1. Communication — envoi email automatisé
L'infrastructure SMTP est en place (`app/services/email.py`), mais les workflows automatisés manquent :
- [ ] Envoi convocations par email avec PDF programme en pièce jointe
- [ ] Templates email HTML (convocation, rappel agapes, relance cotisation)
- [ ] Gestion opt-in / opt-out par membre (champ en base, pas de UI)
- [ ] Synchronisation listes email cPanel via API (token prévu dans `LodgeSettings`)

#### 2. Admin panel dédié
Les fonctions admin sont dispersées (settings, members, finance...) mais il n'y a pas de dashboard centralisé :
- [ ] Vue d'ensemble : stats d'utilisation, membres actifs, tenues à venir
- [ ] Gestion des comptes utilisateurs (droits, rôles, qui a accès à quoi)
- [ ] Backup / export BDD téléchargeable (SQLite ou dump SQL)
- [ ] Audit log : qui a fait quoi (créé/modifié tenue, programme, cotisation)
- [ ] Sécurité : tentatives de connexion échouées, sessions actives
- [ ] Design / branding (logo, couleurs thème) dans les settings

---

### 🔴 PLANIFIÉ

#### 3. Abonnements & comptabilité associative
- [ ] Abonnements membres (type, montant, fréquence)
- [ ] Paiements (date, mode, montant, lié à membre)
- [ ] Comptabilité associative (recettes/dépenses, catégories)
- [ ] Export comptable

#### 4. Visio
Les champs sont déjà en base (`visio_provider`, `visio_server_url`, `visio_room_prefix`) :
- [ ] Génération automatique lien par tenue (Jitsi / BBB)
- [ ] Lien dans convocation email et programme PDF

#### 5. Design & thème — refonte CSS / responsive
- [ ] Choix d'un thème cohérent adapté au contexte maçonnique (sobre, sérieux, lisible)
- [ ] Palette de couleurs unifiée (actuellement `loge-*` Tailwind custom — à affiner)
- [ ] Responsive mobile : audit page par page, corrections breakpoints manquants
- [ ] Typographie : cohérence tailles/poids entre toutes les pages
- [ ] Dark mode optionnel
- [ ] Animations / transitions douces (hover, modals, accordéons)
- [ ] Composants réutilisables : cartes, badges, tableaux, boutons — harmonisation

#### 6. PWA — finition
- [ ] Manifest complet (icônes toutes tailles)
- [ ] Install prompt (A2HS)
- [ ] Offline mode (service worker cache)
- [ ] Notifications push

---

## Ce qu'il reste vraiment à faire (synthèse)

| Priorité | Chantier | Effort |
|----------|----------|--------|
| 🔴 Haute | Admin panel centralisé (backup, stats, audit, sécurité) | Moyen |
| 🔴 Haute | Convocations email automatiques avec PDF | Moyen |
| 🟡 Moyenne | Templates email (convocation, relance cotisation) | Petit |
| 🟡 Moyenne | Sync listes cPanel | Petit |
| 🟡 Moyenne | Abonnements & comptabilité associative | Grand |
| 🟢 Basse | Visio (Jitsi/BBB) — champs déjà en base | Petit |
| 🟢 Basse | Design & thème (refonte CSS, responsive, palette) | Grand |
| 🟢 Basse | PWA finition (offline, push, install prompt) | Moyen |

---

## Architecture fichiers clés

```
app/
├── main.py                  # FastAPI app + lifespan + routers
├── database.py              # Engine SQLAlchemy + Base + get_db
├── config.py                # Settings (dotenv)
├── dependencies.py          # Auth, permissions, hash_password
├── services/
│   └── email.py             # Service SMTP async
├── models/
│   ├── identity.py          # Member, User, MasonicGrade, LodgeFunction, Group...
│   ├── lodge.py             # LodgeSettings, LodgeOffice, MasonicYear
│   ├── meetings.py          # Meeting, Attendance, Visitor, MeetingVisitor, TracingSection...
│   ├── documents.py         # DocumentSpace, Folder, File
│   ├── messaging.py         # Message, Channel, Thread
│   └── lodge_calendar.py    # CalendarEvent
├── routers/
│   ├── auth.py              # Login / logout / changement MDP
│   ├── members.py           # CRUD membres + offices + reset MDP admin
│   ├── meetings.py          # Tenues + agapes + inscription publique + tracé
│   ├── programs.py          # Génération PDF programmes (création + édition)
│   ├── finance.py           # Trésorerie complète (cotisations, budget, transactions)
│   ├── attendance.py        # Présence, émargement, bilan, visiteurs
│   ├── settings.py          # Paramètres loge + SMTP + officiers + notifications
│   ├── documents.py         # GED / Bibliothèque
│   ├── chat.py              # Chat interne (canaux, groupes, DMs)
│   ├── messages.py          # Messagerie ciblée
│   ├── calendar.py          # Calendrier + export iCal
│   ├── announcements.py     # Annonces
│   └── groups.py            # Groupes de membres
└── templates/
    ├── base.html            # Layout PWA (sidebar desktop + bottom nav mobile)
    └── pages/
        ├── dashboard.html
        ├── admin/           # ← VIDE (à construire)
        ├── members/         # list, detail, form
        ├── meetings/        # list, detail, form, register_public, banquet, trace
        ├── programs/        # detail (PDF), create, edit
        ├── finance/         # dashboard, cotisations, budget, transactions, bilan, membre_detail
        ├── attendance/      # dashboard, emargement, summary, member, visitors, bilan
        ├── documents/       # index, space, folder
        ├── chat/            # index
        ├── messages/        # compose, detail
        ├── calendar/        # index, list, compose, detail
        ├── auth/            # login, change_password
        └── settings/        # index, notifications

app/static/
├── img/
│   ├── sceau-socrate-transparent.png
│   └── sceau-socrate-blanc.png
├── manifest.json
└── sw.js

socrate_local.db             # Base SQLite locale
.env                         # Config locale (DATABASE_URL, SECRET_KEY, SMTP...)
seed.py                      # Données initiales (idempotent)
scripts/
└── init_ged_structure.py    # Initialisation structure GED
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
- **Async SQLAlchemy** : ne jamais accéder aux relations dans les templates Jinja2 — tout charger en amont (selectinload ou requêtes séparées)

---

## Pour reprendre

1. Lancer l'app : `python -m uvicorn app.main:app --reload`
2. Ouvrir : http://localhost:8000
3. Login : `admin` / `admin`
4. Prochain chantier : **Admin panel** (dashboard centralisé, backup, stats, audit)
