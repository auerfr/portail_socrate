"""Migrations legeres de schema (ajout de colonnes / tables manquantes).

Extrait du lifespan FastAPI (app/main.py) pour pouvoir etre execute de
maniere autonome via scripts/migrate.py — utile quand le cycle de vie ASGI
(lifespan) ne se declenche pas sur l'hebergement (ex: PythonAnywhere en
WSGI classique), ce qui laissait des colonnes manquantes en prod malgre
un redemarrage de l'app.
"""
from sqlalchemy.ext.asyncio import AsyncEngine


async def ensure_wal_mode(engine: AsyncEngine) -> None:
    """Active le mode WAL (lectures simultanées même pendant une écriture) et
    un busy_timeout généreux. Réglage persistant (stocké dans le fichier
    SQLite lui-même), mais ne s'applique jamais si le lifespan ASGI qui
    l'exécutait ne se déclenche pas sur l'hébergement — d'où son extraction
    ici pour pouvoir être rejoué explicitement via scripts/migrate.py."""
    async with engine.begin() as conn:
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.exec_driver_sql("PRAGMA busy_timeout=30000")  # 30s


async def run_lightweight_migrations(engine: AsyncEngine) -> None:
    """Applique toutes les migrations legeres (idempotent, sans risque a rejouer)."""
    # ── Migrations légères (ajout de colonnes manquantes) ──────────────────
    async with engine.begin() as conn:
        # members.email_notifications
        r_mem = await conn.exec_driver_sql("PRAGMA table_info(members)")
        cols_mem = [row[1] for row in r_mem.fetchall()]
        if "email_notifications" not in cols_mem:
            await conn.exec_driver_sql(
                "ALTER TABLE members ADD COLUMN email_notifications BOOLEAN NOT NULL DEFAULT 1"
            )
        if "membership_type" not in cols_mem:
            await conn.exec_driver_sql(
                "ALTER TABLE members ADD COLUMN membership_type VARCHAR(20) NOT NULL DEFAULT 'APPARTENANCE'"
            )
        if "membership_start_date" not in cols_mem:
            await conn.exec_driver_sql(
                "ALTER TABLE members ADD COLUMN membership_start_date DATE"
            )

        # budget_lines.category_label
        r = await conn.exec_driver_sql("PRAGMA table_info(budget_lines)")
        cols = [row[1] for row in r.fetchall()]
        if "category_label" not in cols:
            await conn.exec_driver_sql(
                "ALTER TABLE budget_lines ADD COLUMN category_label VARCHAR(200)"
            )

        # contribution_configs.initial_treasury
        r2 = await conn.exec_driver_sql("PRAGMA table_info(contribution_configs)")
        cols2 = [row[1] for row in r2.fetchall()]
        if "initial_treasury" not in cols2:
            await conn.exec_driver_sql(
                "ALTER TABLE contribution_configs ADD COLUMN initial_treasury NUMERIC(10,2) DEFAULT 0"
            )
        if "tier_selection_open" not in cols2:
            await conn.exec_driver_sql(
                "ALTER TABLE contribution_configs ADD COLUMN tier_selection_open BOOLEAN DEFAULT 0"
            )
        if "fiscal_year_label" not in cols2:
            await conn.exec_driver_sql(
                "ALTER TABLE contribution_configs ADD COLUMN fiscal_year_label VARCHAR(20)"
            )
        if "capitations_published_at" not in cols2:
            await conn.exec_driver_sql(
                "ALTER TABLE contribution_configs ADD COLUMN capitations_published_at DATE"
            )
        if "tier_selection_opens_at" not in cols2:
            await conn.exec_driver_sql(
                "ALTER TABLE contribution_configs ADD COLUMN tier_selection_opens_at DATE"
            )
        if "tier_selection_closes_at" not in cols2:
            await conn.exec_driver_sql(
                "ALTER TABLE contribution_configs ADD COLUMN tier_selection_closes_at DATE"
            )
        if "tier_selection_closed_at" not in cols2:
            await conn.exec_driver_sql(
                "ALTER TABLE contribution_configs ADD COLUMN tier_selection_closed_at DATETIME"
            )

        # ── Messagerie interne ──────────────────────────────────────────────
        r_msg = await conn.exec_driver_sql("PRAGMA table_info(messages)")
        cols_msg = [row[1] for row in r_msg.fetchall()]
        if "parent_id" not in cols_msg:
            await conn.exec_driver_sql(
                "ALTER TABLE messages ADD COLUMN parent_id INTEGER REFERENCES messages(id)"
            )
        if "visio_url" not in cols_msg:
            await conn.exec_driver_sql(
                "ALTER TABLE messages ADD COLUMN visio_url VARCHAR(500)"
            )
        if "sender_deleted_at" not in cols_msg:
            await conn.exec_driver_sql(
                "ALTER TABLE messages ADD COLUMN sender_deleted_at DATETIME"
            )
        if "body_html" not in cols_msg:
            await conn.exec_driver_sql(
                "ALTER TABLE messages ADD COLUMN body_html TEXT"
            )
        # message_attachments : créée par Base.metadata.create_all (nouveau modèle)

        r_mr = await conn.exec_driver_sql("PRAGMA table_info(message_recipients)")
        cols_mr = [row[1] for row in r_mr.fetchall()]
        if cols_mr and "deleted_at" not in cols_mr:
            await conn.exec_driver_sql(
                "ALTER TABLE message_recipients ADD COLUMN deleted_at DATETIME"
            )
        if cols_mr and "label" not in cols_mr:
            await conn.exec_driver_sql(
                "ALTER TABLE message_recipients ADD COLUMN label VARCHAR(50)"
            )

        # ── Agenda ─────────────────────────────────────────────────────────
        # La table lodge_events est créée par Base.metadata.create_all
        r_ev = await conn.exec_driver_sql("PRAGMA table_info(lodge_events)")
        cols_ev = [row[1] for row in r_ev.fetchall()]
        if "visibility_group_id" not in cols_ev:
            await conn.exec_driver_sql(
                "ALTER TABLE lodge_events ADD COLUMN visibility_group_id INTEGER REFERENCES lodge_groups(id)"
            )
        if "meeting_url" not in cols_ev:
            await conn.exec_driver_sql(
                "ALTER TABLE lodge_events ADD COLUMN meeting_url VARCHAR(500)"
            )
        if "is_personal" not in cols_ev:
            await conn.exec_driver_sql(
                "ALTER TABLE lodge_events ADD COLUMN is_personal BOOLEAN DEFAULT 0"
            )

        # ── Tracé de tenue — corps narratif ────────────────────────────────
        r_mtg = await conn.exec_driver_sql("PRAGMA table_info(meetings)")
        cols_mtg = [row[1] for row in r_mtg.fetchall()]
        if "compte_rendu_html" not in cols_mtg:
            await conn.exec_driver_sql(
                "ALTER TABLE meetings ADD COLUMN compte_rendu_html TEXT"
            )

        # ── GED — group_id sur doc_spaces et doc_folders ───────────────────
        r_ds = await conn.exec_driver_sql("PRAGMA table_info(doc_spaces)")
        cols_ds = [row[1] for row in r_ds.fetchall()]
        if "group_id" not in cols_ds:
            await conn.exec_driver_sql(
                "ALTER TABLE doc_spaces ADD COLUMN group_id INTEGER REFERENCES lodge_groups(id)"
            )

        r_df = await conn.exec_driver_sql("PRAGMA table_info(doc_folders)")
        cols_df = [row[1] for row in r_df.fetchall()]
        if "group_id" not in cols_df:
            await conn.exec_driver_sql(
                "ALTER TABLE doc_folders ADD COLUMN group_id INTEGER REFERENCES lodge_groups(id)"
            )
        if "personal_owner_id" not in cols_df:
            await conn.exec_driver_sql(
                "ALTER TABLE doc_folders ADD COLUMN personal_owner_id INTEGER REFERENCES members(id) ON DELETE CASCADE"
            )
        # Permissions granulaires (téléchargement + écriture)
        if "allow_download" not in cols_df:
            await conn.exec_driver_sql(
                "ALTER TABLE doc_folders ADD COLUMN allow_download BOOLEAN NOT NULL DEFAULT 1"
            )
        if "download_group_id" not in cols_df:
            await conn.exec_driver_sql(
                "ALTER TABLE doc_folders ADD COLUMN download_group_id INTEGER REFERENCES lodge_groups(id) ON DELETE SET NULL"
            )
        if "write_group_id" not in cols_df:
            await conn.exec_driver_sql(
                "ALTER TABLE doc_folders ADD COLUMN write_group_id INTEGER REFERENCES lodge_groups(id) ON DELETE SET NULL"
            )
        if "write_min_grade" not in cols_df:
            await conn.exec_driver_sql(
                "ALTER TABLE doc_folders ADD COLUMN write_min_grade VARCHAR(20) NOT NULL DEFAULT 'ALL'"
            )

        # ── GED — table doc_shares (partage externe) ──────────────────────────
        r_ds2 = await conn.exec_driver_sql("PRAGMA table_info(doc_shares)")
        cols_ds2 = [row[1] for row in r_ds2.fetchall()]
        if not cols_ds2:
            # La table sera créée par Base.metadata.create_all au prochain démarrage
            # mais on la crée immédiatement si elle manque
            await conn.exec_driver_sql("""
                CREATE TABLE IF NOT EXISTS doc_shares (
                    id INTEGER PRIMARY KEY,
                    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    token VARCHAR(64) NOT NULL UNIQUE,
                    label VARCHAR(200),
                    expires_at DATETIME,
                    max_uses INTEGER,
                    use_count INTEGER NOT NULL DEFAULT 0,
                    password_hash VARCHAR(200),
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    created_by_id INTEGER REFERENCES members(id),
                    created_at DATETIME DEFAULT (datetime('now'))
                )
            """)
            await conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_doc_shares_token ON doc_shares(token)"
            )

        # ── GED — link_url sur documents + original_filename nullable ───────
        r_doc = await conn.exec_driver_sql("PRAGMA table_info(documents)")
        cols_doc_info = r_doc.fetchall()
        cols_doc = [row[1] for row in cols_doc_info]

        if "link_url" not in cols_doc:
            await conn.exec_driver_sql(
                "ALTER TABLE documents ADD COLUMN link_url VARCHAR(2000)"
            )

        # Rendre original_filename nullable (NOT NULL → NULL) via recréation SQLite
        orig_col = next((row for row in cols_doc_info if row[1] == "original_filename"), None)
        if orig_col and orig_col[3] == 1:  # notnull == 1
            await conn.exec_driver_sql("""
                CREATE TABLE IF NOT EXISTS documents_new (
                    id INTEGER PRIMARY KEY,
                    folder_id INTEGER NOT NULL REFERENCES doc_folders(id) ON DELETE CASCADE,
                    name VARCHAR(300) NOT NULL,
                    description TEXT,
                    original_filename VARCHAR(300),
                    mime_type VARCHAR(100),
                    file_size INTEGER,
                    storage_path VARCHAR(500),
                    link_url VARCHAR(2000),
                    download_count INTEGER NOT NULL DEFAULT 0,
                    status VARCHAR(20) NOT NULL,
                    author_id INTEGER REFERENCES members(id),
                    validated_by_id INTEGER REFERENCES members(id),
                    validated_at DATETIME,
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """)
            await conn.exec_driver_sql(
                "INSERT OR IGNORE INTO documents_new SELECT * FROM documents"
            )
            await conn.exec_driver_sql("DROP TABLE documents")
            await conn.exec_driver_sql("ALTER TABLE documents_new RENAME TO documents")

        # ── Correction logo blanc → transparent dans lodge_settings ──────────
        await conn.exec_driver_sql(
            "UPDATE lodge_settings SET logo_url = '/static/img/sceau-socrate-transparent.png' "
            "WHERE logo_url = '/static/img/sceau-socrate-blanc.png'"
        )

        # ── Seuils assiduité dans lodge_settings ──────────────────────────────
        r_ls = await conn.exec_driver_sql("PRAGMA table_info(lodge_settings)")
        ls_cols = {row[1] for row in r_ls.fetchall()}
        if "attendance_threshold_warn" not in ls_cols:
            await conn.exec_driver_sql(
                "ALTER TABLE lodge_settings ADD COLUMN attendance_threshold_warn INTEGER DEFAULT 70"
            )
        if "attendance_threshold_danger" not in ls_cols:
            await conn.exec_driver_sql(
                "ALTER TABLE lodge_settings ADD COLUMN attendance_threshold_danger INTEGER DEFAULT 50"
            )
        if "visio_provider" not in ls_cols:
            await conn.exec_driver_sql(
                "ALTER TABLE lodge_settings ADD COLUMN visio_provider VARCHAR(50)"
            )
        if "visio_server_url" not in ls_cols:
            await conn.exec_driver_sql(
                "ALTER TABLE lodge_settings ADD COLUMN visio_server_url VARCHAR(500)"
            )
        if "visio_room_prefix" not in ls_cols:
            await conn.exec_driver_sql(
                "ALTER TABLE lodge_settings ADD COLUMN visio_room_prefix VARCHAR(100)"
            )
        if "admin_email" not in ls_cols:
            await conn.exec_driver_sql(
                "ALTER TABLE lodge_settings ADD COLUMN admin_email VARCHAR(200)"
            )

        # ── Actualités & Sondages — target_group_id ───────────────────────────
        r_na = await conn.exec_driver_sql("PRAGMA table_info(news_articles)")
        cols_na = [row[1] for row in r_na.fetchall()]
        if "target_group_id" not in cols_na:
            await conn.exec_driver_sql(
                "ALTER TABLE news_articles ADD COLUMN target_group_id INTEGER REFERENCES lodge_groups(id)"
            )

        r_pl = await conn.exec_driver_sql("PRAGMA table_info(polls)")
        cols_pl = [row[1] for row in r_pl.fetchall()]
        if "target_group_id" not in cols_pl:
            await conn.exec_driver_sql(
                "ALTER TABLE polls ADD COLUMN target_group_id INTEGER REFERENCES lodge_groups(id)"
            )

        # Table correspondants externes
        await conn.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS external_contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(200) NOT NULL,
                email VARCHAR(200) NOT NULL,
                organization VARCHAR(200),
                contact_type VARCHAR(20) NOT NULL DEFAULT 'EXTERNAL',
                is_active BOOLEAN NOT NULL DEFAULT 1,
                notes TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ── PV de tenues ──────────────────────────────────────────────────────
        await conn.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS meeting_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meeting_id INTEGER NOT NULL UNIQUE REFERENCES meetings(id) ON DELETE CASCADE,
                content TEXT,
                status VARCHAR(20) NOT NULL DEFAULT 'BROUILLON',
                author_id INTEGER REFERENCES members(id),
                created_at DATETIME DEFAULT (datetime('now')),
                updated_at DATETIME,
                submitted_at DATETIME,
                approved_by_id INTEGER REFERENCES members(id),
                approved_at DATETIME,
                archived_doc_id INTEGER REFERENCES documents(id) ON DELETE SET NULL
            )
        """)
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_meeting_reports_meeting_id ON meeting_reports(meeting_id)"
        )

        # ── Planches & travaux ────────────────────────────────────────────────
        await conn.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS planches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title VARCHAR(300) NOT NULL,
                content TEXT,
                file_path VARCHAR(500),
                original_filename VARCHAR(300),
                mime_type VARCHAR(100),
                file_size INTEGER,
                status VARCHAR(20) NOT NULL DEFAULT 'BROUILLON',
                grade VARCHAR(20) NOT NULL DEFAULT 'TOUS',
                author_id INTEGER REFERENCES members(id),
                meeting_id INTEGER REFERENCES meetings(id) ON DELETE SET NULL,
                created_at DATETIME DEFAULT (datetime('now')),
                updated_at DATETIME,
                published_at DATETIME
            )
        """)
        await conn.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS planche_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                planche_id INTEGER NOT NULL REFERENCES planches(id) ON DELETE CASCADE,
                author_id INTEGER REFERENCES members(id),
                content TEXT NOT NULL,
                created_at DATETIME DEFAULT (datetime('now'))
            )
        """)
        # archived_doc_id ajouté après coup
        r_pl = await conn.exec_driver_sql("PRAGMA table_info(planches)")
        cols_pl = [row[1] for row in r_pl.fetchall()]
        if "archived_doc_id" not in cols_pl:
            await conn.exec_driver_sql(
                "ALTER TABLE planches ADD COLUMN archived_doc_id INTEGER REFERENCES documents(id) ON DELETE SET NULL"
            )

        r_us = await conn.exec_driver_sql("PRAGMA table_info(users)")
        cols_us = [row[1] for row in r_us.fetchall()]
        if "reset_token" not in cols_us:
            await conn.exec_driver_sql("ALTER TABLE users ADD COLUMN reset_token VARCHAR(100)")
        if "reset_token_expires" not in cols_us:
            await conn.exec_driver_sql("ALTER TABLE users ADD COLUMN reset_token_expires DATETIME")
        if "totp_secret" not in cols_us:
            await conn.exec_driver_sql("ALTER TABLE users ADD COLUMN totp_secret VARCHAR(64)")
        if "totp_enabled" not in cols_us:
            await conn.exec_driver_sql("ALTER TABLE users ADD COLUMN totp_enabled BOOLEAN NOT NULL DEFAULT 0")

        # ── Projets & Tâches — ajout colonnes (couleur projet, groupe, gantt) ──
        r_pr = await conn.exec_driver_sql("PRAGMA table_info(projects)")
        cols_pr = [row[1] for row in r_pr.fetchall()]
        if cols_pr and "color" not in cols_pr:
            await conn.exec_driver_sql("ALTER TABLE projects ADD COLUMN color VARCHAR(10)")

        r_tk = await conn.exec_driver_sql("PRAGMA table_info(tasks)")
        cols_tk = [row[1] for row in r_tk.fetchall()]
        if cols_tk:
            if "assigned_to_group_id" not in cols_tk:
                await conn.exec_driver_sql(
                    "ALTER TABLE tasks ADD COLUMN assigned_to_group_id INTEGER REFERENCES lodge_groups(id) ON DELETE SET NULL"
                )
            if "progress" not in cols_tk:
                await conn.exec_driver_sql("ALTER TABLE tasks ADD COLUMN progress INTEGER NOT NULL DEFAULT 0")
            if "start_date" not in cols_tk:
                await conn.exec_driver_sql("ALTER TABLE tasks ADD COLUMN start_date DATE")
            if "order_position" not in cols_tk:
                await conn.exec_driver_sql("ALTER TABLE tasks ADD COLUMN order_position INTEGER NOT NULL DEFAULT 0")
            if "forum_subject_id" not in cols_tk:
                await conn.exec_driver_sql(
                    "ALTER TABLE tasks ADD COLUMN forum_subject_id INTEGER REFERENCES forum_subjects(id) ON DELETE SET NULL"
                )
            if "is_milestone" not in cols_tk:
                await conn.exec_driver_sql("ALTER TABLE tasks ADD COLUMN is_milestone INTEGER NOT NULL DEFAULT 0")
            if "reminded_at" not in cols_tk:
                await conn.exec_driver_sql("ALTER TABLE tasks ADD COLUMN reminded_at DATETIME")
            if "parent_task_id" not in cols_tk:
                await conn.exec_driver_sql(
                    "ALTER TABLE tasks ADD COLUMN parent_task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE"
                )

    # ── external_contacts : colonnes first_name, last_name, lodge_name, orient
    async with engine.begin() as conn:
        r_ec = await conn.exec_driver_sql("PRAGMA table_info(external_contacts)")
        cols_ec = [row[1] for row in r_ec.fetchall()]
        if cols_ec:
            for col, ddl in [
                ("first_name", "VARCHAR(100)"),
                ("last_name",  "VARCHAR(100)"),
                ("lodge_name", "VARCHAR(200)"),
                ("orient",     "VARCHAR(100)"),
            ]:
                if col not in cols_ec:
                    await conn.exec_driver_sql(
                        f"ALTER TABLE external_contacts ADD COLUMN {col} {ddl}"
                    )

    # ── mailing : colonnes tracking ────────────────────────────────────────
    async with engine.begin() as conn:
        r_mc = await conn.exec_driver_sql("PRAGMA table_info(mailing_campaigns)")
        cols_mc = [row[1] for row in r_mc.fetchall()]
        for col, ddl in [
            ("opened_count",  "INTEGER NOT NULL DEFAULT 0"),
            ("clicked_count", "INTEGER NOT NULL DEFAULT 0"),
            ("scheduled_at",  "DATETIME"),
        ]:
            if col not in cols_mc:
                await conn.exec_driver_sql(f"ALTER TABLE mailing_campaigns ADD COLUMN {col} {ddl}")

        r_md2 = await conn.exec_driver_sql("PRAGMA table_info(mailing_deliveries)")
        cols_md2 = [row[1] for row in r_md2.fetchall()]
        for col, ddl in [
            ("opened_at",   "DATETIME"),
            ("clicked_at",  "DATETIME"),
            ("click_count", "INTEGER NOT NULL DEFAULT 0"),
            ("external_id", "INTEGER REFERENCES external_contacts(id) ON DELETE SET NULL"),
        ]:
            if col not in cols_md2:
                await conn.exec_driver_sql(f"ALTER TABLE mailing_deliveries ADD COLUMN {col} {ddl}")

    # ── email_logs : colonnes tracking (accès membres) ──────────────────────
    async with engine.begin() as conn:
        r_el = await conn.exec_driver_sql("PRAGMA table_info(email_logs)")
        cols_el = [row[1] for row in r_el.fetchall()]
        for col, ddl in [
            ("opened_at",  "DATETIME"),
            ("clicked_at", "DATETIME"),
        ]:
            if col not in cols_el:
                await conn.exec_driver_sql(f"ALTER TABLE email_logs ADD COLUMN {col} {ddl}")

    # ── members.last_activity_at : présence en ligne ────────────────────────
    async with engine.begin() as conn:
        r_pres = await conn.exec_driver_sql("PRAGMA table_info(members)")
        cols_pres = [row[1] for row in r_pres.fetchall()]
        if "last_activity_at" not in cols_pres:
            await conn.exec_driver_sql(
                "ALTER TABLE members ADD COLUMN last_activity_at DATETIME"
            )

    # ── audit_logs : nouvelles colonnes (target_label, user_agent) ─────────
    async with engine.begin() as conn:
        r_al = await conn.exec_driver_sql("PRAGMA table_info(audit_logs)")
        cols_al = [row[1] for row in r_al.fetchall()]
        if cols_al:
            if "target_label" not in cols_al:
                await conn.exec_driver_sql("ALTER TABLE audit_logs ADD COLUMN target_label VARCHAR(300)")
            if "user_agent" not in cols_al:
                await conn.exec_driver_sql("ALTER TABLE audit_logs ADD COLUMN user_agent VARCHAR(300)")

    # ── external_contacts.last_confirmed_at : confirmation annuelle ─────────
    async with engine.begin() as conn:
        r_ec = await conn.exec_driver_sql("PRAGMA table_info(external_contacts)")
        cols_ec = [row[1] for row in r_ec.fetchall()]
        if cols_ec and "last_confirmed_at" not in cols_ec:
            await conn.exec_driver_sql(
                "ALTER TABLE external_contacts ADD COLUMN last_confirmed_at DATETIME"
            )

    # ── members.notifications_seen_at : centre de notifications ────────────
    async with engine.begin() as conn:
        r_notif = await conn.exec_driver_sql("PRAGMA table_info(members)")
        cols_notif = [row[1] for row in r_notif.fetchall()]
        if cols_notif and "notifications_seen_at" not in cols_notif:
            await conn.exec_driver_sql(
                "ALTER TABLE members ADD COLUMN notifications_seen_at DATETIME"
            )
        if cols_notif:
            for col in ("notif_messages", "notif_planches", "notif_polls", "notif_forum"):
                if col not in cols_notif:
                    await conn.exec_driver_sql(
                        f"ALTER TABLE members ADD COLUMN {col} BOOLEAN NOT NULL DEFAULT 1"
                    )

    # ── Fusion Tracé/PV : reporte l'ancien contenu des PV (jamais fusionné
    # avec le tracé de la tenue) vers meetings.compte_rendu_html, avant que
    # l'ancienne UI dédiée (app/routers/reports.py) ne soit retirée. Idempotent
    # : ne touche que les tracés encore vides.
    async with engine.begin() as conn:
        await conn.exec_driver_sql("""
            UPDATE meetings
            SET compte_rendu_html = (
                SELECT mr.content FROM meeting_reports mr
                WHERE mr.meeting_id = meetings.id AND mr.content IS NOT NULL AND mr.content != ''
            )
            WHERE (compte_rendu_html IS NULL OR compte_rendu_html = '')
              AND EXISTS (
                SELECT 1 FROM meeting_reports mr
                WHERE mr.meeting_id = meetings.id AND mr.content IS NOT NULL AND mr.content != ''
              )
        """)

    # ── lodge_events.recurrence_* : récurrence des événements de l'agenda ──
    async with engine.begin() as conn:
        r_ev = await conn.exec_driver_sql("PRAGMA table_info(lodge_events)")
        cols_ev = [row[1] for row in r_ev.fetchall()]
        if cols_ev and "recurrence_group_id" not in cols_ev:
            await conn.exec_driver_sql("ALTER TABLE lodge_events ADD COLUMN recurrence_group_id VARCHAR(32)")
            await conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_lodge_events_recurrence_group_id ON lodge_events(recurrence_group_id)"
            )
        if cols_ev and "recurrence_label" not in cols_ev:
            await conn.exec_driver_sql("ALTER TABLE lodge_events ADD COLUMN recurrence_label VARCHAR(200)")

    # ── page_views : analytique interne (pages vues, provenance, appareil) ──
    async with engine.begin() as conn:
        await conn.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS page_views (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path VARCHAR(300) NOT NULL,
                referrer_host VARCHAR(200) NOT NULL DEFAULT 'direct',
                device VARCHAR(20) NOT NULL DEFAULT 'inconnu',
                member_id INTEGER REFERENCES members(id) ON DELETE SET NULL,
                session_id VARCHAR(64),
                created_at DATETIME DEFAULT (datetime('now'))
            )
        """)
        await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_page_views_path ON page_views(path)")
        await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_page_views_referrer_host ON page_views(referrer_host)")
        await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_page_views_member_id ON page_views(member_id)")
        await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_page_views_session_id ON page_views(session_id)")
        await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_page_views_created_at ON page_views(created_at)")

    # ── Index de performance (FK fréquemment filtrées) ─────────────────────
    async with engine.begin() as conn:
        # Finance
        await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_member_contributions_member_id ON member_contributions(member_id)")
        await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_member_contributions_masonic_year_id ON member_contributions(masonic_year_id)")
        await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_member_contributions_tier_id ON member_contributions(tier_id)")
        await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_payments_member_contribution_id ON payments(member_contribution_id)")
        await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_quitus_member_id ON quitus(member_id)")
        await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_quitus_masonic_year_id ON quitus(masonic_year_id)")
        await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_budget_categories_masonic_year_id ON budget_categories(masonic_year_id)")
        await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_transactions_masonic_year_id ON transactions(masonic_year_id)")
        await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_transactions_category_id ON transactions(category_id)")
        # Tenues
        await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_attendance_meeting_id ON attendance(meeting_id)")
        await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_attendance_member_id ON attendance(member_id)")
        await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_meeting_visitors_meeting_id ON meeting_visitors(meeting_id)")
        await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_meeting_visitors_visitor_id ON meeting_visitors(visitor_id)")
        await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_meeting_guests_meeting_id ON meeting_guests(meeting_id)")
        await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_meeting_waitlist_meeting_id ON meeting_waitlist(meeting_id)")
        await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_meeting_waitlist_member_id ON meeting_waitlist(member_id)")
        # Forum
        await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_forum_subjects_theme_id ON forum_subjects(theme_id)")
        await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_forum_messages_subject_id ON forum_messages(subject_id)")
        # Notifications & Push
        await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_notifications_member_id ON notifications(member_id)")
        await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_push_subscriptions_member_id ON push_subscriptions(member_id)")

