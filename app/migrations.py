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

    # ── external_contacts : colonnes supplémentaires ────────────────────────
    async with engine.begin() as conn:
        r_ec = await conn.exec_driver_sql("PRAGMA table_info(external_contacts)")
        cols_ec = [row[1] for row in r_ec.fetchall()]
        if cols_ec:
            for col, ddl in [
                ("last_confirmed_at",    "DATETIME"),
                ("obedience",            "VARCHAR(100)"),
                ("removal_requested_at", "DATETIME"),
            ]:
                if col not in cols_ec:
                    await conn.exec_driver_sql(
                        f"ALTER TABLE external_contacts ADD COLUMN {col} {ddl}"
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
        if cols_ev and "visibility_member_ids" not in cols_ev:
            await conn.exec_driver_sql("ALTER TABLE lodge_events ADD COLUMN visibility_member_ids TEXT")

    # ── chat_channels.lodge_group_id + chat_channel_members.is_admin ─────────
    async with engine.begin() as conn:
        r_cc = await conn.exec_driver_sql("PRAGMA table_info(chat_channels)")
        cols_cc = [row[1] for row in r_cc.fetchall()]
        if cols_cc and "lodge_group_id" not in cols_cc:
            await conn.exec_driver_sql(
                "ALTER TABLE chat_channels ADD COLUMN lodge_group_id INTEGER REFERENCES lodge_groups(id)"
            )
        r_ccm = await conn.exec_driver_sql("PRAGMA table_info(chat_channel_members)")
        cols_ccm = [row[1] for row in r_ccm.fetchall()]
        if cols_ccm and "is_admin" not in cols_ccm:
            await conn.exec_driver_sql(
                "ALTER TABLE chat_channel_members ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0"
            )

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
    # On vérifie l'existence de chaque table avant de créer son index
    # (certaines tables peuvent manquer si create_all n'a jamais tourné en prod).
    async with engine.begin() as conn:
        _tables_r = await conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        _existing_tables = {r[0] for r in _tables_r.fetchall()}

        _indexes = [
            # Finance
            ("ix_member_contributions_member_id",    "member_contributions", "member_id"),
            ("ix_member_contributions_masonic_year_id", "member_contributions", "masonic_year_id"),
            ("ix_member_contributions_tier_id",      "member_contributions", "tier_id"),
            ("ix_payments_member_contribution_id",   "payments",             "member_contribution_id"),
            ("ix_quitus_member_id",                  "quitus",               "member_id"),
            ("ix_quitus_masonic_year_id",            "quitus",               "masonic_year_id"),
            ("ix_budget_categories_masonic_year_id", "budget_categories",    "masonic_year_id"),
            ("ix_transactions_masonic_year_id",      "transactions",         "masonic_year_id"),
            ("ix_transactions_category_id",          "transactions",         "category_id"),
            # Tenues
            ("ix_attendance_meeting_id",             "attendance",           "meeting_id"),
            ("ix_attendance_member_id",              "attendance",           "member_id"),
            ("ix_meeting_visitors_meeting_id",       "meeting_visitors",     "meeting_id"),
            ("ix_meeting_visitors_visitor_id",       "meeting_visitors",     "visitor_id"),
            ("ix_meeting_guests_meeting_id",         "meeting_guests",       "meeting_id"),
            ("ix_meeting_waitlist_meeting_id",       "meeting_waitlist",     "meeting_id"),
            ("ix_meeting_waitlist_member_id",        "meeting_waitlist",     "member_id"),
            # Forum
            ("ix_forum_subjects_theme_id",           "forum_subjects",       "theme_id"),
            ("ix_forum_messages_subject_id",         "forum_messages",       "subject_id"),
            # Notifications & Push
            ("ix_notifications_member_id",           "notifications",        "member_id"),
            ("ix_push_subscriptions_member_id",      "push_subscriptions",   "member_id"),
        ]
        for idx_name, table_name, col_name in _indexes:
            if table_name in _existing_tables:
                await conn.exec_driver_sql(
                    f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table_name}({col_name})"
                )

    # ── Import loges partenaires (contacts externes type LOGE) ────────────────
    async with engine.begin() as conn:
        LOGES_PARTENAIRES = [
            ("3p.venerable@gmail.com", "Les Trois Piliers"),
            ("alainjm.faivre@orange.fr", "Socrate"),
            ("alainschmidt@orange.fr", "Abbe Gregoire - Luneville - GODF"),
            ("andre.wenner@wanadoo.fr", "François Rabelais - Agora"),
            ("annemarie.delles@gmail.com", "STOA - Metz - DH"),
            ("anthonykuhn@aol.com", "PAIS HUMA - NANCY DH"),
            ("arbre.pierre@gmail.com", "L'Arbre et la Pierre"),
            ("baudoin.luc@wanadoo.fr", "le TRAVAIL - REMIREMONT - GODF"),
            ("Baueliane@hotmail.com", "Ouroboros VM"),
            ("benedicte.perrin1@hotmail.fr", "AGORA - METZ - DH"),
            ("boyer_gregory73@orange.fr", "NOBLE AMITIE - METZ - DH"),
            ("bruno.coffion@orange.fr", "FRANCOIS RABELAIS - ST AVOLD GODF"),
            ("bullinger@kanzlei-kb.de", "Am ROTTENBERG - STUTTGART - GODF"),
            ("candidadis@live.fr", "3 GLOBES - BERLIN - GODF"),
            ("catherine.wertheimer@orange.fr", "Catherine Wertheimer"),
            ("ceckert56@me.com", "AMI DU JEUNE HENRI - BRIEY - GODF"),
            ("cedric.esve@proton.me", "SIRIUS ET VEGA - NANCY - GODF"),
            ("chantal.loiselot01@gmail.com", "l'ARBRE d'ESPERANCE - NANCY - DH"),
            ("christianchalon@orange.fr", "EDLDU - THIONVILLE - GODF"),
            ("christophe.supper@gmail.com", "GUTENBERG - STRASBOURG - GODF"),
            ("christopheostolani@outlook.com", "Pierre PERRAT - METZ - GODF"),
            ("claude.houssemand@uni.lu", "LA VRAIE LUMIERE - NANCY - GODF"),
            ("concordia.venerable@gmail.com", "CONCORDIA - METZ - GLFF"),
            ("dalstein.gilbert@wanadoo.fr", "AMOUR ET LIBERTE - THIONVILLE - GODF"),
            ("danielenevels@gmail.com", "PORTEUR DE LUMIERE - METZ - DH"),
            ("denis.kochems57@gmail.com", "FLAMME de ZOROA - ST AVOLD - GODF"),
            ("denise.wender@orange.fr", "JEAN LAMOUR - NANCY - DH"),
            ("djhouty@gmail.com", "Djhouty"),
            ("edmondabout@hotmail.fr", "EDMONT ABOUT - NANCY - GODF"),
            ("emilieduchatelet1706@yahoo.com", "Progrès et Diversité Émilie du Châtelet – 1706"),
            ("enfantsdeladoubleunion@gmail.com", "Les Enfants de la Double Union"),
            ("evelyne.nowak@sfr.fr", "L'Arbre sur le Terril - GLMU"),
            ("f.tamalt@orange.fr", "VITRUVE - METZ - GODF"),
            ("fab.pageot@orange.fr", "ST JEAN de JERUSALEM NANCY - GODF"),
            ("gallcatherine@hotmail.com", "ICI et MAINTENANT - METZ DH"),
            ("garciavali70@gmail.com", "HELIOPOLIS - METZ - GODF"),
            ("hubert.ehlinger@gmail.com", "EDMONT ABOUT - NANCY GODF"),
            ("jb.rousse.0@gmail.com", "AMIS DE LA VERITE - METZ - GODF"),
            ("jc_257@hotmail.fr", "Jean-Claude Stablo"),
            ("jcmennuni@gmail.com", "FRANCOIS DE LORRAINE - NANCY - GODF"),
            ("jeanluc.barthel@orange.fr", "Georges Jacques Danton"),
            ("jeff.fritsch@neuf.fr", "DE LA REUNION PHILANTROPIQUE - LONGWY - GODF"),
            ("jmjansem@orange.fr", "Logos - Thélème"),
            ("jmmmathieu@orange.fr", "SAEDAR - PAM - GODF"),
            ("josephvaleri@mac.com", "REGELE et l'INFINI - METZ - GODF"),
            ("jougletreinelde@gmail.com", "KETHER - METZ - GLFF"),
            ("klein@cerigo.net", "ST JEAN au TEMPLE DE LA PAIX - METZ - GODF"),
            ("larbresurleterril@gmail.com", "L'Arbre sur le Terril - GLMF"),
            ("laurent.gingembre@wanadoo.fr", "Frat EUROPEENNE - SARREBRUCK - GODF"),
            ("lesamisdujeunehenry@gmail.com", "Les Amis du Jeune Henry"),
            ("louis.maillard0207@gmail.com", "LA PARRESIA - NANCY - GODF"),
            ("mailliot.gilles@orange.fr", "Joseph CARREZ - TOUL - GODF"),
            ("mallory.koenig@gmail.com", "Progres et Diversite EdC - NANCY - GODF"),
            ("margaux.mondin@gmail.com", "Margaux Mondin"),
            ("martibc@protonmail.com", "AMIS DE LA LIBERTE 57 - Sarreguemines - GODF"),
            ("mceugnie@club-internet.fr", "Victor HUGO - REDING - GODF"),
            ("michel.naymark@numericable.fr", "Arbre et la Pierre - METZ - GODF"),
            ("michelepierson4@gmail.com", "Delta de l'Europe"),
            ("mj.ruff@orange.fr", "Nicolas Henry Jacobi"),
            ("monikarno@yahoo.fr", "MEDIATION - METZ - DH"),
            ("mstoinon@gmail.com", "Marie Antoinette Meichelbeck"),
            ("n.woerner@wanadoo.fr", "Devoir et Liberte - Longwy - GODF"),
            ("nathik57@hotmail.fr", "Virginie Massia Djhouty"),
            ("ndu5966@proton.me", "CAIRN et ACACIA - NANCY GODF"),
            ("nicolashenry2009@hotmail.fr", "Nicolas Henri Jacobi"),
            ("ogrsgrg@protonmail.com", "Jacques CALLOT NANCY - GODF"),
            ("olivier.defretin@gmail.com", "REF - METZ - GODF"),
            ("olivierjulien.lahaye@gmail.com", "les 3 P - METZ - GODF"),
            ("paspor@hotmail.fr", "Logos"),
            ("petit.valerie65@orange.fr", "SIRONA - THIONVILLE - GLFF"),
            ("philippe.gasparella@orange.fr", "UNION et SERENITE - THIONVILLE - DH"),
            ("piechnik@pm.me", "MVH - METZ - GODF"),
            ("pierre.s.a@wanadoo.fr", "UTOPIA NANCY GLFF"),
            ("president1617@mailo.fr", "Rite et RAISON - NANCY - DH"),
            ("secretaire.amouretliberte@gmail.com", "Amour et Liberté Secretariat"),
            ("secretaire.francoisrabelais@gmail.com", "François Rabelais VM"),
            ("secretaire.us@gmail.com", "Union et Sérénité"),
            ("secretaire@horus-haroeris.fr", "Horus-Haoeris"),
            ("SecretaireDLRP@ik.me", "SecretaireDLRP"),
            ("Secretairena@gmail.com", "Noble Amitié"),
            ("secretariat-adll57@protonmail.com", "Les Amis de la Liberté"),
            ("secretariat.aa1455@gmail.com", "L'Arche d'Alliance"),
            ("secretariat.logos@gmail.com", "Logos"),
            ("secretariat.mvh57@gmail.com", "Villard de Honnecourt"),
            ("secretariatref@gmail.com", "la République à l'Ecole de la Fraternité"),
            ("secretariatheliopolismetz@gmail.com", "Heliopolis Renaissante"),
            ("Secretariatouroboros@proton.me", "Ouroboros secrétaire"),
            ("secretariat@leschemins.eu", "Chemins de la Tradition Or Thionville-Yutz"),
            ("selliermariecatherine@gmail.com", "MOZART - NANCY - DH"),
            ("sg57340@gmail.com", "ARCHE d'ALLIANCE - METZ DH"),
            ("sornettepascal@gmail.com", "3 VERSANTS - REDING - GODF"),
            ("venerable@zoroastre.org", "GLDF Metz Zoroastre"),
            ("victor.phalsbourg@gmail.com", "Victor Hugo"),
            ("vitruve-metz@outlook.fr", "vitruve"),
            ("vm.sdAntigone@gmx.fr", "Les soeurs d'Antigone"),
            ("vm@saedar.info", "De St Antoine les amis réunis"),
            ("triangle@triangle-strasbourg.eu", "Triangle de Strasbourg"),
            ("triangledelest@gmail.com", "Triangle de l'Est"),
            ("w.weymeskirch@yahoo.com", "ABBE GREGOIRE - LUNEVILLE - GODF"),
            ("webmaster@theleme.eu", "Theleme.eu"),
        ]
        # Insérer les contacts manquants (idempotent : on vérifie par email)
        existing_emails_r = await conn.exec_driver_sql(
            "SELECT LOWER(email) FROM external_contacts WHERE contact_type = 'LOGE'"
        )
        existing_emails = {r[0] for r in existing_emails_r.fetchall()}
        now_str = "datetime('now')"
        for email, org_name in LOGES_PARTENAIRES:
            if email.lower() in existing_emails:
                continue
            await conn.exec_driver_sql(
                "INSERT INTO external_contacts (name, email, organization, contact_type, is_active, created_at) "
                f"VALUES (?, ?, ?, 'LOGE', 1, {now_str})",
                (org_name, email.lower(), org_name),
            )
        # Créer la liste de diffusion "Loges partenaires" si elle n'existe pas
        ml_r = await conn.exec_driver_sql(
            "SELECT id FROM mailing_lists WHERE name = 'Loges partenaires' LIMIT 1"
        )
        ml_row = ml_r.fetchone()
        if not ml_row:
            await conn.exec_driver_sql(
                "INSERT INTO mailing_lists (name, description, list_type, is_system, created_at, updated_at) "
                "VALUES ('Loges partenaires', 'VM et secrétaires des loges du réseau inter-obédientiel', "
                f"'STATIC', 1, {now_str}, {now_str})"
            )
            ml_r2 = await conn.exec_driver_sql(
                "SELECT id FROM mailing_lists WHERE name = 'Loges partenaires' LIMIT 1"
            )
            ml_row = ml_r2.fetchone()
        ml_id = ml_row[0]
        # Rattacher tous les contacts LOGE à cette liste (idempotent)
        all_loge_r = await conn.exec_driver_sql(
            "SELECT id FROM external_contacts WHERE contact_type = 'LOGE' AND is_active = 1"
        )
        already_in_r = await conn.exec_driver_sql(
            "SELECT external_id FROM mailing_list_externals WHERE list_id = ?", (ml_id,)
        )
        already_in = {r[0] for r in already_in_r.fetchall()}
        for (contact_id,) in all_loge_r.fetchall():
            if contact_id not in already_in:
                await conn.exec_driver_sql(
                    f"INSERT INTO mailing_list_externals (list_id, external_id, subscribed_at) "
                    f"VALUES (?, ?, {now_str})",
                    (ml_id, contact_id),
                )

    # ── Import réseau visiteurs (maçons passants et réseau habituel) ──────────
    async with engine.begin() as conn:
        RESEAU_VISITEURS = [
            ('alain.bisval@nordnet.fr', 'Alain Bisval', "LA république à l'école de la fraternité - Metz - GODF"),
            ('alain.marange@sfr.fr', 'Alain Marange', 'La rose et le sillon - Saint Malo - GODF'),
            ('alaindelhotal@gmail.com', 'Alain Delhotal', 'Le triangle de la Voge - Mirecourt - GODF'),
            ('alain-marchal57@orange.fr', 'Alain Marchal', 'Saint antoine et des amis reunis - Pont à Mousson - GODF'),
            ('alexandra.cardona@free.fr', 'Alexandra Cardona', ''),
            ('alinesophie.maire@gmail.com', 'Aline-Sophie Maire', ''),
            ('andre.forcard@sfr.fr', 'André Forcard', 'La régénération - Bar le duc - GODF'),
            ('angers-jean-paul@wanadoo.fr', 'Jean-Paul Angers', ''),
            ('antoine.chabidon@gmail.com', 'Antoine Chabidon', 'VITRUVE - METZ - GODF'),
            ('antoine.lesolleuz@gmail.com', 'Antoine Lesolleuz', ''),
            ('arnaud.vauthier@gmail.com', 'Arnaud Vauthier', 'La noble Amitié - METZ - GODF'),
            ('ascholler@hotmail.fr', 'A. Scholler', ''),
            ('atokofai@orange.fr', 'Anatole Tokofai', 'Amour et Liberte - Thionville - GODF'),
            ('aurelie_foucher@hotmail.com', 'Aurélie Foucher', 'HELIOPOLIS RENAISSANTE - METZ - GODF'),
            ('aureliealonso@yahoo.fr', 'Aurélie Alonso', ''),
            ('balise@netc.eu', 'balise', ''),
            ('bdru.sgi@gmail.com', 'bdru.sgi', ''),
            ('benedicte.perrin1@hotmail.fr', 'Bénédicte Perrin', 'AGORA - METZ - DH'),
            ('benoitdi@wanadoo.fr', 'Benoît Di...', ''),
            ('bernard.loesel@wanadoo.fr', 'Bernard Loesel', "LA république à l'école de la fraternité - Metz - GODF"),
            ('brigitte.albertus@free.fr', 'Brigitte Albertus', 'LES ENFANTS DE LA DOUBLE UNION - THIONVILLE - GODF'),
            ('bruno.deffains@gmail.com', 'Bruno Deffains', "LE CAIRN ET L'ACACIA - NANCY - GODF"),
            ('bruno.deffains@orange.fr', 'Bruno Deffains', "LE CAIRN ET L'ACACIA - NANCY - GODF"),
            ('bruno.martin@outlook.com', 'Bruno Martin', ''),
            ('cahenf@wanadoo.fr', 'F. Cahen', 'Saint antoine et des amis reunis - Pont à Mousson - GODF'),
            ('candidadis@live.fr', 'Margot Bouchard', 'Les 3 Globes - BERLIN - GODF'),
            ('cb57@orange.fr', 'Celine Bonneau', 'Saint antoine et des amis reunis - Pont à Mousson - GODF'),
            ('chacquar@gmail.com', 'Cedric Hacquard', 'GUTENBERG - STRASBOURG - GODF'),
            ('claude.richard.fdl@gmail.com', 'Claude Richard', 'FRANCOIS DE LORRAINE - NANCY - GODF'),
            ('claudegrauffel@yahoo.fr', 'Claude Grauffel', 'Saint antoine et des amis reunis - Pont à Mousson - GODF'),
            ('claudemekler@gmail.com', 'Claude Mekler', "L'ARBRE ET LA PIERRE - Metz - GODF"),
            ('daniel.dann@neuf.fr', 'Daniel Dann', 'LA FLAMME de ZOROASTRE - SAINT AVOLD - GODF'),
            ('davidlahalle@gmail.com', 'David Lahalle', ''),
            ('dcrncrt@orange.fr', 'dcrncrt', ''),
            ('demogorgone@free.fr', 'demogorgone', ''),
            ('denisgentit@aol.com', 'Denis Gentit', 'AMOUR ET LIBERTE - THIONVILLE - GODF'),
            ('dianemarchal54@gmail.com', 'Diane Marchal', 'RITE ET RAISON - NANCY - DH'),
            ('docpgerber@aol.com', 'P. Gerber', "LA république à l'école de la fraternité - Metz - GODF"),
            ('dominique.valentin5@wanadoo.fr', 'Dominique Valentin', ''),
            ('dominique.venter@wanadoo.fr', 'Dominique Venter', ''),
            ('domrol57@gmail.com', 'Dominique Rollin', "L'ARBRE ET LA PIERRE - Metz - GODF"),
            ('d-schmitt.perso@wanadoo.fr', 'D. Schmitt', ''),
            ('ducfrancois3@gmail.com', 'François Duc', 'AMOUR ET LIBERTE - THIONVILLE - GODF'),
            ('dzitella2@gmail.com', 'dzitella2', 'Le TRIANGLE DE LA VOGE - MIRECOURT - GODF'),
            ('einius.jacky@gmail.com', 'Jacky Einius', 'Saint antoine et des amis reunis - Pont à Mousson - GODF'),
            ('elaroubi.yassir@gmail.com', 'Yassir El Aroubi', 'LES ENFANTS DE LA DOUBLE UNION - THIONVILLE - GODF'),
            ('eltigro@club-internet.fr', 'eltigro', ''),
            ('eric.bony@yahoo.fr', 'Eric Bony', ''),
            ('eric.vivien@idelio.net', 'Eric Vivien', ''),
            ('f.schillio@gmail.com', 'F. Schillio', ''),
            ('fab.pageot@orange.fr', 'Fabrice Pageot', ''),
            ('fabrice.chassaigne@free.fr', 'Fabrice Chassaigne', "L'ARBRE ET LA PIERRE - Metz - GODF"),
            ('ferri@briquet.net', 'Ferri', 'FRANCOIS DE LORRAINE - NANCY - GODF'),
            ('fflamain@yahoo.fr', 'Fabrice Flamain', 'Saint Jean au Temple de la Paix - Metz - GODF'),
            ('framb.nums@orange.fr', 'framb.nums', ''),
            ('francine.friederich@orange.fr', 'Francine Friederich', ''),
            ('francine.vorms@orange.fr', 'Francine Vorms', ''),
            ('francis.stoffel@sfr.fr', 'Francis Stoffel', 'ERASMUS - BALE - GODF'),
            ('francis.vignola@orange.fr', 'Francis Vignola', 'LE TRAVAIL - REMIREMONT - GODF'),
            ('franck.boffo@boffo.fr', 'Franck Boffo', 'LES ENFANTS DE LA DOUBLE UNION - THIONVILLE - GODF'),
            ('francois.3@enius.fr', 'François Enius', 'ALMAS LES VERTUS REUNIS - VITRY LE FRANCOIS - GODF'),
            ('francois.battle@orange.fr', 'François Battle', "LA république à l'école de la fraternité - Metz - GODF"),
            ('francois.felten@sfr.fr', 'François Felten', 'AGORA - Metz - DH'),
            ('francoise@viry-babel.com', 'Françoise', ''),
            ('francoisejeanpert@yahoo.fr', 'Françoise Jeanpert', "LA république à l'école de la fraternité - Metz - GODF"),
            ('fred.guidoux@live.fr', 'Frédéric Guidoux', ''),
            ('fz57500@gmail.com', 'fz57500', 'LA FLAMME DE ZOROASTRE - SAINT AVOLD - GODF'),
            ('gandarp@wanadoo.fr', 'Pierre Gandar', "LA république à l'école de la fraternité - Metz - GODF"),
            ('gerard.cazobon@laposte.net', 'Gérard Cazobon', ''),
            ('gerard.voirin@wanadoo.fr', 'Gérard Voirin', ''),
            ('graf.xavier@orange.fr', 'Xavier Graf', ''),
            ('guidat2@wanadoo.fr', 'Jean Marc Guidat', 'Saint Jean au Temple de la Paix - Metz - GODF'),
            ('guy.schoumacker@urbame.com', 'Guy Schoumacker', ''),
            ('h.korsec@live.fr', 'H. Korsec', 'LA ROSE ET LE SILLON - SAINT MALO - GODF'),
            ('hel.mathis@gmail.com', 'Hélène Mathis', ''),
            ('helio3579@hotmail.com', 'helio3579', ''),
            ('herve.cortina@gmail.com', 'Hervé Cortina', 'Saint antoine et des amis reunis - Pont à Mousson - GODF'),
            ('huttin.lucien@neuf.fr', 'Lucien Huttin', ''),
            ('iphonedesab@yahoo.fr', 'iphonedesab', ''),
            ('isabelledelles@gmail.com', 'Isabelle Delles', "L'ARCHE D'ALLIANCE - METZ - DH"),
            ('iza.auburtin@gmail.com', 'Isabelle Auburtin', 'LES ENFANTS DE LA DOUBLE UNION - THIONVILLE - GODF'),
            ('jackylimouzin@sfr.fr', 'Jacky Limouzin', 'AMOUR ET LIBERTE - THIONVILLE - GODF'),
            ('jackyste@wanadoo.fr', 'Jacky Ste.', ''),
            ('jacques.macarons@free.fr', 'Jacques Macarons', ''),
            ('janine.szudra@orange.fr', 'Janine Szudra', ''),
            ('jbthierry@gmail.com', 'J.B. Thierry', ''),
            ('jcdebelly@gmail.com', 'J.C. Debelly', ''),
            ('jchanesse@gmail.com', 'J. Chanesse', ''),
            ('jcperisset@gmail.com', 'J.C. Perisset', ''),
            ('jd.hamet@mchgestion.eu', 'J.D. Hamet', ''),
            ('jeanjacques.gangloff@orange.fr', 'Jean-Jacques Gangloff', 'GUTENBERG - STRASBOURG - GODF'),
            ('jean-louis.roselli@orange.fr', 'Jean-Louis Roselli', 'LA FLAMME DE ZOROASTRE - SAINT AVOLD - GODF'),
            ('jeanlouis@piechnik.fr', 'Jean-Louis Piechnik', 'MAITRE VILLARD DE HONECOURT - METZ - GODF'),
            ('jeanluc.burgain@orange.fr', 'Jean-Luc Burgain', 'LA NOBLE AMITIE - METZ - GODF'),
            ('jean-michel.buchler@orange.fr', 'Jean-Michel Buchler', 'RABELAIS - SAINT AVOLD - GODF'),
            ('jfcha24@gmail.com', 'J.F. Cha.', ''),
            ('jlszkud@orange.fr', 'J.L. Szkud.', ''),
            ('jmmmathieu@orange.fr', 'J.M. Mathieu', 'Saint antoine et des amis reunis - Pont à Mousson - GODF'),
            ('jp.puton@gmail.com', 'J.P. Puton', ''),
            ('jpplouis@gmail.com', 'J.P. Plouis', ''),
            ('juhemar88@gmail.com', 'juhemar88', ''),
            ('julien.mk@protonmail.com', 'Julien M.K.', ''),
            ('kahn.didier2@orange.fr', 'Didier Kahn', ''),
            ('karine.touati@kosmo.lu', 'Karine Touati', "L'ARCHE D'ALLIANCE - METZ - DH"),
            ('katesch86@gmail.com', 'Kate Schneider', 'UNION ET DIVERSITE - THIONVILLE - DH'),
            ('kawka.serge@club-internet.fr', 'Serge Kawka', ''),
            ('l.dap@wanadoo.fr', 'L. Dap', ''),
            ('laetitia.philippon@icloud.com', 'Laetitia Philippon', 'LA TRIPLE EQUERRE - ANNECY - GODF'),
            ('laurence.lebreton57@gmail.com', 'Laurence Lebreton', 'CONCORDIA - METZ - GLFF'),
            ('levillain.d@gmail.com', 'D. Levillain', ''),
            ('luc.mittelbronn@wanadoo.fr', 'Luc Mittelbronn', ''),
            ('malik.chaalal@gmail.com', 'Malik Chaalal', ''),
            ('marc.bouillaguet108@gmail.com', 'Marc Bouillaguet', ''),
            ('marc1054@proton.me', 'Marc', ''),
            ('marcotth@protonmail.com', 'Thierry Marcot', "CAIRN ET L'ACACIA - NANCY - GODF"),
            ('marie-pierre.martin@orange.fr', 'Marie-Pierre Martin', 'STOA - METZ - DH'),
            ('martine.berns-coquillat@orange.fr', 'Martine Berns-Coquillat', "L'ARCHE D'ALLIANCE - METZ - DH"),
            ('martine.crane@gmail.com', 'Martine Crane', 'AGORA - METZ - DH'),
            ('martine_reithinger@hotmail.com', 'Martine Reithinger', ''),
            ('mbarek.irrazi@gmail.com', "M'Barek Irrazi", ''),
            ('mcmconseil@wanadoo.fr', 'mcmconseil', ''),
            ('mcroller@pt.lu', 'M. Croller', ''),
            ('michel.christian.schmitt@gmail.com', 'Michel-Christian Schmitt', ''),
            ('michel.hirschhorn@orange.fr', 'Michel Hirschhorn', 'AMIS DE LA VERITE - METZ - GODF'),
            ('michel.zaccaria@sfr.fr', 'Michel Zaccaria', "LA république à l'école de la fraternité - Metz - GODF"),
            ('moraly@david.as', 'Moraly', ''),
            ('mpb57245@gmail.com', 'mpb57245', ''),
            ('nicolas.eschenbrenner@web.de', 'Nicolas Eschenbrenner', 'LES ENFANTS DE LA DOUBLE UNION - THIONVILLE - GODF'),
            ('olivier.benoit.avocat@orange.fr', 'Olivier Benoit', ''),
            ('p.salvino@groupesalvino.fr', 'P. Salvino', ''),
            ('pascal.pellenz54@gmail.com', 'Pascal Pellenz', 'Saint antoine et des amis reunis - Pont à Mousson - GODF'),
            ('pascal.poncet.1562@wanadoo.fr', 'Pascal Poncet', ''),
            ('pascal.rougel@free.fr', 'Pascal Rougel', ''),
            ('pascal.wuttke@wanadoo.fr', 'Pascal Wuttke', ''),
            ('pascalboulard@yahoo.fr', 'Pascal Boulard', 'AMOUR ET LIBERTE - THIONVILLE - GODF'),
            ('pecheur.beatrice@orange.fr', 'Béatrice Pêcheur', 'AGORA - METZ - DH'),
            ('pierre.frank@everclean57.fr', 'Pierre Frank', 'LA FLAMME DE ZOROASTRE - SAINT AVOLD - GODF'),
            ('pierre.kratz@gmail.com', 'Pierre Kratz', ''),
            ('pierre.weitzel@laposte.net', 'Pierre Weitzel', 'HELIOPOLIS RENAISSANTE - METZ - GODF'),
            ('pierrebertinotti@yahoo.fr', 'Pierre Bertinotti', 'SAINT JEAN AU TEMPLE DE LA PAIX - METZ - GODF'),
            ('pigni54@hotmail.com', 'Christian Berteux', ''),
            ('pillot.jacques@gmail.com', 'Jacques Pillot', ''),
            ('pnicolle.perso@gmail.com', 'P. Nicolle', ''),
            ('po.carreau@softmarketing.fr', 'P.O. Carreau', 'TOLERANCE - PARIS - GODF'),
            ('poirsonaline@yahoo.fr', 'Aline Poirson', ''),
            ('pose_792@hotmail.com', 'pose_792', ''),
            ('ppa.remy@hotmail.fr', 'Rémy P.P.A.', ''),
            ('r.billaude@outlook.fr', 'R. Billaude', 'Saint antoine et des amis reunis - Pont à Mousson - GODF'),
            ('r.pierronnet@gmail.com', 'R. Pierronnet', ''),
            ('raoulgottlich@yahoo.fr', 'Raoul Gottlich', 'LA VRAIE LUMIERE - NANCY - GODF'),
            ('rapp.patrick@wanadoo.fr', 'Patrick Rapp', ''),
            ('rauch.isabelle@yahoo.fr', 'Isabelle Rauch', "LA république à l'école de la fraternité - Metz - GODF"),
            ('robin.gllm@yahoo.com', 'Robin G.', ''),
            ('s.bernard5467@laposte.net', 'S. Bernard', ''),
            ('sandramonneau@yahoo.fr', 'Sandra Monneau', ''),
            ('sebastien.liarte@gmail.com', 'Sébastien Liarte', 'SAINT DE JERUSALEM - NANCY - GODF'),
            ('secretariat.aa1455@gmail.com', "Secrétariat Arche d'Alliance", "L'ARCHE D'ALLIANCE - METZ - DH"),
            ('secretariat@saedar.info', 'Secrétariat Saedar', 'Saint antoine et des amis reunis - Pont à Mousson - GODF'),
            ('secretariatref@gmail.com', 'Secrétariat REF', "LA République à l'Ecole de la Fraternité - Metz - GODF"),
            ('sg57340@gmail.com', 'sg57340', ''),
            ('skknecht@gmail.com', 'Knecht', "L'ARBRE ET LA PIERRE - METZ - GODF"),
            ('sreteg@wanadoo.fr', 'sreteg', ''),
            ('stephan.berard54@gmail.com', 'Stéphan Bérard', 'Saint antoine et des amis reunis - Pont à Mousson - GODF'),
            ('stephane.masse357@gmail.com', 'Stéphane Massé', 'Francois de Lorraine - Nancy - GODF'),
            ('stephane.nassoy@wanadoo.fr', 'Stéphane Nassoy', "L'ARBRE ET LA PIERRE - METZ - GODF"),
            ('stephanielemaitre@hotmail.fr', 'Stéphanie Lemaître', ''),
            ('stephz750@aol.com', 'stephz750', ''),
            ('susunierpro@gmail.com', 'susunierpro', 'LE TRAVAIL - REMIREMONT - GODF'),
            ('thews.mathieu@gmail.com', 'Mathieu Thews', 'Saint antoine et des amis reunis - Pont à Mousson - GODF'),
            ('thierry.delles@crea-diffusion.com', 'Thierry Delles', "PIERRE PERRAT à l'Etoile Flamboyante - METZ - GODF"),
            ('uneviedesregards@gmail.com', 'uneviedesregards', ''),
            ('vm.sda@gmx.fr', 'vm.sda', ''),
            ('xophe.baudot@laposte.net', 'Christophe Baudot', 'Francois de Lorraine - Nancy - GODF'),
        ]
        now_str = "datetime('now')"
        # Pour chaque email : créer le contact s'il n'existe pas déjà (peu importe le type)
        existing_r = await conn.exec_driver_sql(
            "SELECT LOWER(email), id FROM external_contacts"
        )
        existing_by_email = {r[0]: r[1] for r in existing_r.fetchall()}
        for email, name, org in RESEAU_VISITEURS:
            if email.lower() not in existing_by_email:
                await conn.exec_driver_sql(
                    "INSERT INTO external_contacts (name, email, organization, contact_type, is_active, created_at) "
                    f"VALUES (?, ?, ?, 'VISITOR', 1, {now_str})",
                    (name, email.lower(), org or None),
                )
        # Recharger le mapping email→id après les insertions
        existing_r2 = await conn.exec_driver_sql(
            "SELECT LOWER(email), id FROM external_contacts"
        )
        existing_by_email = {r[0]: r[1] for r in existing_r2.fetchall()}
        # Créer la liste "Réseau visiteurs" si elle n'existe pas
        rv_r = await conn.exec_driver_sql(
            "SELECT id FROM mailing_lists WHERE name = 'Réseau visiteurs' LIMIT 1"
        )
        rv_row = rv_r.fetchone()
        if not rv_row:
            await conn.exec_driver_sql(
                "INSERT INTO mailing_lists (name, description, list_type, is_system, created_at, updated_at) "
                "VALUES ('Réseau visiteurs', 'Maçons passants et réseau inter-obédientiel habituel', "
                f"'STATIC', 1, {now_str}, {now_str})"
            )
            rv_r2 = await conn.exec_driver_sql(
                "SELECT id FROM mailing_lists WHERE name = 'Réseau visiteurs' LIMIT 1"
            )
            rv_row = rv_r2.fetchone()
        rv_id = rv_row[0]
        # Rattacher tous les contacts du réseau à cette liste (idempotent)
        already_rv_r = await conn.exec_driver_sql(
            "SELECT external_id FROM mailing_list_externals WHERE list_id = ?", (rv_id,)
        )
        already_rv = {r[0] for r in already_rv_r.fetchall()}
        for email, _name, _org in RESEAU_VISITEURS:
            contact_id = existing_by_email.get(email.lower())
            if contact_id and contact_id not in already_rv:
                await conn.exec_driver_sql(
                    f"INSERT INTO mailing_list_externals (list_id, external_id, subscribed_at) "
                    f"VALUES (?, ?, {now_str})",
                    (rv_id, contact_id),
                )

