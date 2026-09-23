import sqlite3
from pathlib import Path
import logging
import json

logger = logging.getLogger(__name__)

class DatabaseManager:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path.resolve())
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                # --- COLLECTIONS (Strictly Internal) ---
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS collections (
                        collection_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        collection_type TEXT,
                        group_id TEXT,
                        mutable INTEGER DEFAULT 1,
                        status TEXT DEFAULT 'ACTIVE',
                        created_at TEXT,
                        updated_at TEXT,
                        FOREIGN KEY(group_id) REFERENCES collection_groups(group_id) ON DELETE SET NULL
                    );
                """)

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS collection_groups (
                        group_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL UNIQUE,
                        sort_order INTEGER DEFAULT 0,
                        collapsed INTEGER DEFAULT 0,
                        created_at TEXT,
                        updated_at TEXT
                    );
                """)
                cursor.execute("""
                    INSERT OR IGNORE INTO collection_groups (group_id, name, sort_order, collapsed, created_at, updated_at)
                    VALUES ('__ungrouped__', 'Ungrouped', -1, 0, datetime('now'), datetime('now'))
                """)
                cursor.execute("PRAGMA table_info(collections)")
                collection_columns = {row[1] for row in cursor.fetchall()}
                if "group_id" not in collection_columns:
                    cursor.execute("ALTER TABLE collections ADD COLUMN group_id TEXT REFERENCES collection_groups(group_id) ON DELETE SET NULL")
                
                # --- COLLECTION SOURCES (External Platform Mapping) ---
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS collection_sources (
                        collection_id TEXT,
                        source_type TEXT NOT NULL,
                        external_id TEXT,
                        external_url TEXT,
                        last_sync TEXT,
                        PRIMARY KEY (collection_id, source_type),
                        FOREIGN KEY(collection_id) REFERENCES collections(collection_id) ON DELETE CASCADE
                    );
                """)
                
                # --- MEDIA CATALOG (Legacy name: 'songs') ---
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS songs (
                        song_id TEXT PRIMARY KEY,
                        spotify_id TEXT,  -- Retained for backward compatibility
                        media_type TEXT DEFAULT 'music',
                        title TEXT NOT NULL,
                        artist TEXT NOT NULL,
                        album TEXT,
                        duration_ms INTEGER,
                        status TEXT NOT NULL,
                        acquisition_info TEXT, -- NEW: JSON string for direct media routing
                        created_at TEXT
                    );
                """)

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS song_external_ids (
                        song_id TEXT NOT NULL,
                        source_type TEXT NOT NULL,
                        external_id TEXT NOT NULL,
                        external_url TEXT,
                        first_seen_at TEXT,
                        last_seen_at TEXT,
                        PRIMARY KEY (source_type, external_id),
                        FOREIGN KEY(song_id) REFERENCES songs(song_id) ON DELETE CASCADE
                    );
                """)
                
                # --- UPDATED: Junction Table with Archival State ---
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS collection_songs (
                        collection_id TEXT,
                        song_id TEXT,
                        position INTEGER,
                        added_at TEXT,
                        status TEXT DEFAULT 'ACTIVE', -- NEW: 'ACTIVE' or 'ARCHIVED'
                        archived_at TEXT,             -- NEW: Timestamp when it went missing
                        PRIMARY KEY(collection_id, song_id),
                        FOREIGN KEY(collection_id) REFERENCES collections(collection_id) ON DELETE CASCADE,
                        FOREIGN KEY(song_id) REFERENCES songs(song_id) ON DELETE CASCADE
                    );
                """)
                
                # --- ACQUISITION TABLES ---
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS matches (
                        match_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        song_id TEXT NOT NULL,
                        youtube_id TEXT NOT NULL,
                        yt_title TEXT,
                        yt_duration TEXT,
                        confidence REAL,
                        approved INTEGER DEFAULT 0,
                        FOREIGN KEY (song_id) REFERENCES songs(song_id)
                    )
                """)
                cursor.execute("""
                    DELETE FROM matches
                    WHERE match_id NOT IN (
                        SELECT keep_id
                        FROM (
                            SELECT (
                                SELECT m2.match_id
                                FROM matches m2
                                WHERE m2.song_id = m.song_id
                                  AND m2.youtube_id = m.youtube_id
                                ORDER BY m2.approved DESC, m2.confidence DESC, m2.match_id DESC
                                LIMIT 1
                            ) AS keep_id
                            FROM matches m
                            GROUP BY m.song_id, m.youtube_id
                        )
                    )
                """)
                cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_matches_song_youtube ON matches(song_id, youtube_id)")
                
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS files (
                        song_id TEXT PRIMARY KEY,
                        filepath TEXT NOT NULL,
                        downloaded INTEGER DEFAULT 0,
                        synced INTEGER DEFAULT 0,
                        last_verified TEXT,
                        FOREIGN KEY(song_id) REFERENCES songs(song_id) ON DELETE CASCADE
                    );
                """)
                
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS review_queue (
                        song_id TEXT PRIMARY KEY,
                        created_at TEXT,
                        status TEXT,
                        reason TEXT,
                        source_url TEXT,
                        source_error TEXT,
                        FOREIGN KEY(song_id) REFERENCES songs(song_id) ON DELETE CASCADE
                    );
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS download_failures (
                        song_id TEXT NOT NULL,
                        source_key TEXT NOT NULL,
                        source_url TEXT,
                        failure_count INTEGER DEFAULT 0,
                        first_failed_at TEXT,
                        last_failed_at TEXT,
                        last_job_id TEXT,
                        last_category TEXT,
                        last_error TEXT,
                        promoted_at TEXT,
                        PRIMARY KEY(song_id, source_key),
                        FOREIGN KEY(song_id) REFERENCES songs(song_id) ON DELETE CASCADE
                    );
                """)

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS song_audio_features (
                        song_id TEXT PRIMARY KEY,
                        analysis_status TEXT NOT NULL DEFAULT 'PENDING',
                        analysis_error TEXT,
                        analyzed_at TEXT,
                        analyzer_version TEXT,
                        loudness_version TEXT,
                        rhythm_version TEXT,
                        dynamics_version TEXT,
                        tone_version TEXT,
                        harmony_version TEXT,
                        vocals_version TEXT,
                        tempo_bpm REAL,
                        tempo_raw_bpm REAL,
                        tempo_alt_bpm REAL,
                        tempo_variability REAL,
                        onset_density REAL,
                        energy_mean REAL,
                        energy_p90 REAL,
                        spectral_brightness REAL,
                        spectral_flatness REAL,
                        instrumentalness REAL,
                        key_root TEXT,
                        key_scale TEXT,
                        key_confidence REAL,
                        harmonic_complexity REAL,
                        normalization_method TEXT,
                        target_lufs REAL,
                        integrated_loudness_lufs REAL,
                        normalization_gain_db REAL,
                        normalized_peak REAL,
                        would_clip INTEGER DEFAULT 0,
                        source_filepath TEXT,
                        source_file_mtime REAL,
                        source_file_size INTEGER,
                        FOREIGN KEY(song_id) REFERENCES songs(song_id) ON DELETE CASCADE
                    );
                """)
                cursor.execute("PRAGMA table_info(song_audio_features)")
                audio_feature_columns = {row[1] for row in cursor.fetchall()}
                audio_feature_migrations = {
                    "tempo_raw_bpm": "ALTER TABLE song_audio_features ADD COLUMN tempo_raw_bpm REAL",
                    "tempo_alt_bpm": "ALTER TABLE song_audio_features ADD COLUMN tempo_alt_bpm REAL",
                    "onset_density": "ALTER TABLE song_audio_features ADD COLUMN onset_density REAL",
                    "key_root": "ALTER TABLE song_audio_features ADD COLUMN key_root TEXT",
                    "key_scale": "ALTER TABLE song_audio_features ADD COLUMN key_scale TEXT",
                    "key_confidence": "ALTER TABLE song_audio_features ADD COLUMN key_confidence REAL",
                    "harmonic_complexity": "ALTER TABLE song_audio_features ADD COLUMN harmonic_complexity REAL",
                    "loudness_version": "ALTER TABLE song_audio_features ADD COLUMN loudness_version TEXT",
                    "rhythm_version": "ALTER TABLE song_audio_features ADD COLUMN rhythm_version TEXT",
                    "dynamics_version": "ALTER TABLE song_audio_features ADD COLUMN dynamics_version TEXT",
                    "tone_version": "ALTER TABLE song_audio_features ADD COLUMN tone_version TEXT",
                    "harmony_version": "ALTER TABLE song_audio_features ADD COLUMN harmony_version TEXT",
                    "vocals_version": "ALTER TABLE song_audio_features ADD COLUMN vocals_version TEXT",
                }
                for column_name, statement in audio_feature_migrations.items():
                    if column_name not in audio_feature_columns:
                        cursor.execute(statement)
                for column_name in (
                    "loudness_version",
                    "rhythm_version",
                    "dynamics_version",
                    "tone_version",
                    "harmony_version",
                    "vocals_version",
                ):
                    cursor.execute(
                        f"""
                        UPDATE song_audio_features
                        SET {column_name} = analyzer_version
                        WHERE {column_name} IS NULL
                          AND analyzer_version IS NOT NULL
                          AND analysis_status = 'COMPLETE'
                        """
                    )
                cursor.execute("PRAGMA table_info(review_queue)")
                review_columns = {row[1] for row in cursor.fetchall()}
                if "reason" not in review_columns:
                    cursor.execute("ALTER TABLE review_queue ADD COLUMN reason TEXT")
                if "source_url" not in review_columns:
                    cursor.execute("ALTER TABLE review_queue ADD COLUMN source_url TEXT")
                if "source_error" not in review_columns:
                    cursor.execute("ALTER TABLE review_queue ADD COLUMN source_error TEXT")

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS collection_revisions (
                        revision_id TEXT PRIMARY KEY,
                        collection_id TEXT NOT NULL,
                        state TEXT NOT NULL,
                        reason TEXT,
                        started_at TEXT,
                        committed_at TEXT,
                        base_revision_id TEXT,
                        FOREIGN KEY(collection_id) REFERENCES collections(collection_id) ON DELETE CASCADE,
                        FOREIGN KEY(base_revision_id) REFERENCES collection_revisions(revision_id)
                    );
                """)

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS collection_revision_items (
                        revision_id TEXT NOT NULL,
                        song_id TEXT NOT NULL,
                        position INTEGER,
                        PRIMARY KEY (revision_id, song_id),
                        FOREIGN KEY(revision_id) REFERENCES collection_revisions(revision_id) ON DELETE CASCADE,
                        FOREIGN KEY(song_id) REFERENCES songs(song_id) ON DELETE CASCADE
                    );
                """)

                # --- EXPORTS TABLE ---
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS exports (
                        export_id TEXT PRIMARY KEY,
                        collection_id TEXT,
                        target_path TEXT NOT NULL,
                        mode TEXT NOT NULL, -- 'STATIC' or 'MANAGED'
                        last_updated TEXT,
                        last_operation TEXT DEFAULT 'update',
                        shuffle_limit INTEGER,
                        FOREIGN KEY(collection_id) REFERENCES collections(collection_id) ON DELETE CASCADE
                    );
                """)

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS collection_rules (
                        collection_id TEXT PRIMARY KEY,
                        rule_type TEXT NOT NULL,
                        rule_json TEXT NOT NULL,
                        created_at TEXT,
                        updated_at TEXT,
                        FOREIGN KEY(collection_id) REFERENCES collections(collection_id) ON DELETE CASCADE
                    );
                """)

                # --- CIRCUITS TABLES ---
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS circuits (
                        circuit_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        target_path TEXT NOT NULL,
                        source_config_json TEXT NOT NULL,
                        fill_policy_json TEXT,
                        status TEXT DEFAULT 'ACTIVE',
                        created_at TEXT,
                        updated_at TEXT,
                        last_sent_at TEXT,
                        last_scanned_at TEXT
                    );
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS circuit_sends (
                        send_id TEXT PRIMARY KEY,
                        circuit_id TEXT NOT NULL,
                        sent_at TEXT NOT NULL,
                        operation TEXT NOT NULL,
                        target_path TEXT NOT NULL,
                        manifest_path TEXT,
                        track_count INTEGER DEFAULT 0,
                        stop_marker_song_id TEXT,
                        stop_marker_position INTEGER,
                        scanned_at TEXT,
                        FOREIGN KEY(circuit_id) REFERENCES circuits(circuit_id) ON DELETE CASCADE,
                        FOREIGN KEY(stop_marker_song_id) REFERENCES songs(song_id) ON DELETE SET NULL
                    );
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS circuit_send_items (
                        send_id TEXT NOT NULL,
                        song_id TEXT NOT NULL,
                        position INTEGER NOT NULL,
                        filename TEXT NOT NULL,
                        returned_state TEXT DEFAULT 'unknown',
                        PRIMARY KEY (send_id, song_id),
                        FOREIGN KEY(send_id) REFERENCES circuit_sends(send_id) ON DELETE CASCADE,
                        FOREIGN KEY(song_id) REFERENCES songs(song_id) ON DELETE CASCADE
                    );
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS circuit_song_state (
                        circuit_id TEXT NOT NULL,
                        song_id TEXT NOT NULL,
                        last_sent_at TEXT,
                        sent_count INTEGER DEFAULT 0,
                        last_returned_at TEXT,
                        last_missing_at TEXT,
                        last_stop_marker_at TEXT,
                        last_heard_at TEXT,
                        PRIMARY KEY (circuit_id, song_id),
                        FOREIGN KEY(circuit_id) REFERENCES circuits(circuit_id) ON DELETE CASCADE,
                        FOREIGN KEY(song_id) REFERENCES songs(song_id) ON DELETE CASCADE
                    );
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS song_feedback (
                        song_id TEXT PRIMARY KEY,
                        rating TEXT DEFAULT 'neutral',
                        last_liked_at TEXT,
                        last_disliked_at TEXT,
                        updated_at TEXT,
                        FOREIGN KEY(song_id) REFERENCES songs(song_id) ON DELETE CASCADE
                    );
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS circuit_song_preferences (
                        circuit_id TEXT NOT NULL,
                        song_id TEXT NOT NULL,
                        rating TEXT DEFAULT 'neutral',
                        last_liked_at TEXT,
                        last_disliked_at TEXT,
                        updated_at TEXT,
                        PRIMARY KEY (circuit_id, song_id),
                        FOREIGN KEY(circuit_id) REFERENCES circuits(circuit_id) ON DELETE CASCADE,
                        FOREIGN KEY(song_id) REFERENCES songs(song_id) ON DELETE CASCADE
                    );
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS song_feedback_events (
                        event_id TEXT PRIMARY KEY,
                        circuit_id TEXT,
                        song_id TEXT NOT NULL,
                        scope TEXT NOT NULL,
                        rating TEXT NOT NULL,
                        source TEXT NOT NULL,
                        event_at TEXT NOT NULL,
                        FOREIGN KEY(circuit_id) REFERENCES circuits(circuit_id) ON DELETE SET NULL,
                        FOREIGN KEY(song_id) REFERENCES songs(song_id) ON DELETE CASCADE
                    );
                """)
                cursor.execute("PRAGMA table_info(exports)")
                export_columns = {row[1] for row in cursor.fetchall()}
                if "last_operation" not in export_columns:
                    cursor.execute("ALTER TABLE exports ADD COLUMN last_operation TEXT DEFAULT 'update'")
                if "shuffle_limit" not in export_columns:
                    cursor.execute("ALTER TABLE exports ADD COLUMN shuffle_limit INTEGER")
                cursor.execute("UPDATE exports SET last_operation = 'update' WHERE last_operation IS NULL OR last_operation = ''")

                cursor.execute("""
                    INSERT OR IGNORE INTO review_queue (song_id, created_at, status)
                    SELECT song_id, datetime('now'), 'PENDING'
                    FROM songs
                    WHERE status = 'SKIPPED'
                """)
                cursor.execute("UPDATE songs SET status = 'REVIEW' WHERE status = 'SKIPPED'")
                cursor.execute("""
                    INSERT OR IGNORE INTO song_external_ids (song_id, source_type, external_id, first_seen_at, last_seen_at)
                    SELECT song_id, 'spotify', spotify_id, created_at, datetime('now')
                    FROM songs
                    WHERE spotify_id IS NOT NULL AND spotify_id != ''
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_song_external_ids_song ON song_external_ids(song_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_matches_song ON matches(song_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_collection_revisions_collection_time ON collection_revisions(collection_id, committed_at)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_collection_revisions_state ON collection_revisions(state)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_collection_revision_items_revision ON collection_revision_items(revision_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_collection_revision_items_song ON collection_revision_items(song_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_collections_group ON collections(group_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_collection_groups_sort ON collection_groups(sort_order, name)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_collection_sources_type ON collection_sources(source_type)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_download_failures_song ON download_failures(song_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_circuit_sends_circuit_time ON circuit_sends(circuit_id, sent_at)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_circuit_send_items_send_position ON circuit_send_items(send_id, position)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_circuit_song_state_song ON circuit_song_state(song_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_circuit_song_preferences_song ON circuit_song_preferences(song_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_song_feedback_events_song_time ON song_feedback_events(song_id, event_at)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_song_feedback_events_circuit_time ON song_feedback_events(circuit_id, event_at)")
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Database initialization failed: {e}")
            raise
