import logging
import sqlite3
import uuid
import json
import datetime
from typing import Optional
from pathlib import Path

from core.audio_analysis import FEATURE_GROUP_VERSION_COLUMNS, FEATURE_GROUP_VERSIONS
from core.collection_history import CollectionHistoryManager
from core.models import CollectionQuery, MediaQuery
from infrastructure.database import DatabaseManager
from infrastructure.models import Collection, MediaItem

logger = logging.getLogger(__name__)

AUDIO_FEATURE_SORT_COLUMNS = {
    "tempo_bpm": "af.tempo_bpm",
    "tempo_raw_bpm": "af.tempo_raw_bpm",
    "tempo_alt_bpm": "af.tempo_alt_bpm",
    "tempo_variability": "af.tempo_variability",
    "onset_density": "af.onset_density",
    "energy_mean": "af.energy_mean",
    "energy_p90": "af.energy_p90",
    "impact": "(af.energy_p90 / NULLIF(af.energy_mean, 0))",
    "spectral_brightness": "af.spectral_brightness",
    "spectral_flatness": "af.spectral_flatness",
    "instrumentalness": "af.instrumentalness",
    "normalized_peak": "af.normalized_peak",
    "key_confidence": "af.key_confidence",
    "harmonic_complexity": "af.harmonic_complexity",
}

AUDIO_FEATURE_FILTER_EXPRESSIONS = {
    "tempo_bpm": ("af.tempo_bpm", [75.0, 95.0, 115.0, 140.0]),
    "tempo_variability": ("af.tempo_variability", [8.0, 18.0]),
    "onset_density": ("af.onset_density", [1.5, 3.0, 5.0, 7.0]),
    "energy_mean": ("af.energy_mean", [0.150, 0.175, 0.195, 0.215]),
    "impact": ("(af.energy_p90 / NULLIF(af.energy_mean, 0))", [1.25, 1.5, 1.8, 2.2]),
    "spectral_brightness": ("af.spectral_brightness", [1500.0, 2500.0, 3500.0, 5000.0]),
    "spectral_flatness": ("af.spectral_flatness", [0.001, 0.005, 0.020, 0.080]),
    "instrumentalness": ("af.instrumentalness", [0.35, 0.75]),
}

class CollectionManager:
    def __init__(self, db: DatabaseManager):
        self.db = db
        self.history = CollectionHistoryManager(db)

    def get_all_collections(self, *, include_archived: bool = False, only_archived: bool = False) -> list[dict]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            status_filter = "c.status = 'ARCHIVED'" if only_archived else "c.status != 'DELETED' OR c.status IS NULL"
            if not include_archived and not only_archived:
                status_filter = "(c.status != 'DELETED' OR c.status IS NULL) AND (c.status != 'ARCHIVED' OR c.status IS NULL)"
            cursor.execute("""
                SELECT c.collection_id, c.name, c.collection_type, c.status, cs_src.source_type,
                       cs_src.external_id, cs_src.external_url, cs_src.last_sync,
                       c.updated_at, c.group_id, cg.name as group_name,
                       COUNT(CASE WHEN cs.status = 'ACTIVE' THEN 1 END) as track_count
                FROM collections c
                JOIN collection_sources cs_src ON c.collection_id = cs_src.collection_id
                LEFT JOIN collection_groups cg ON c.group_id = cg.group_id
                LEFT JOIN collection_songs cs ON c.collection_id = cs.collection_id
                WHERE """ + status_filter + """
                GROUP BY c.collection_id
                ORDER BY COALESCE(cg.sort_order, 0), COALESCE(cg.name, ''), c.name
            """)
            return [dict(row) for row in cursor.fetchall()]

    def get_collection_groups(self) -> list[dict]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT group_id, name, sort_order, collapsed, created_at, updated_at
                FROM collection_groups
                WHERE group_id != '__ungrouped__'
                ORDER BY sort_order, name
            """)
            return [dict(row) for row in cursor.fetchall()]

    def get_ungrouped_collapsed(self) -> bool:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT collapsed FROM collection_groups WHERE group_id = '__ungrouped__'")
            row = cursor.fetchone()
            return bool(row and row["collapsed"])

    def create_collection_group(self, name: str) -> str:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Group name cannot be empty.")
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        group_id = str(uuid.uuid4())
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT group_id FROM collection_groups WHERE lower(name) = lower(?)", (clean_name,))
            existing = cursor.fetchone()
            if existing:
                return existing["group_id"]
            cursor.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_order FROM collection_groups")
            sort_order = int(cursor.fetchone()["next_order"] or 1)
            cursor.execute(
                """
                INSERT INTO collection_groups (group_id, name, sort_order, collapsed, created_at, updated_at)
                VALUES (?, ?, ?, 0, ?, ?)
                """,
                (group_id, clean_name, sort_order, now, now),
            )
            conn.commit()
        return group_id

    def delete_collection_group(self, group_id: str) -> None:
        if not group_id or group_id == "__ungrouped__":
            return
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE collections SET group_id = NULL, updated_at = ? WHERE group_id = ?", (now, group_id))
            cursor.execute("DELETE FROM collection_groups WHERE group_id = ?", (group_id,))
            conn.commit()

    def set_collection_group(self, collection_id: str, group_id: str | None) -> None:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE collections SET group_id = ?, updated_at = ? WHERE collection_id = ?",
                (group_id, now, collection_id),
            )
            conn.commit()

    def set_group_collapsed(self, group_id: str | None, collapsed: bool) -> None:
        group_id = group_id or "__ungrouped__"
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE collection_groups SET collapsed = ?, updated_at = ? WHERE group_id = ?",
                (1 if collapsed else 0, now, group_id),
            )
            conn.commit()

    def save_collection(self, collection: Collection, job_context=None) -> str:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            collection_id = self._resolve_collection_id(cursor, collection.source_type, collection.external_id)
            
            if not collection_id:
                collection_id = str(uuid.uuid4())
                # FIX: Explicitly inject 'ACTIVE' into the status column on creation
                cursor.execute("INSERT INTO collections (collection_id, name, collection_type, status, created_at, updated_at) VALUES (?, ?, 'external', 'ACTIVE', ?, ?)", (collection_id, collection.name, now, now))
                cursor.execute("INSERT INTO collection_sources (collection_id, source_type, external_id, external_url, last_sync) VALUES (?, ?, ?, ?, ?)", (collection_id, collection.source_type, collection.external_id, collection.external_url, now))
            else:
                cursor.execute("UPDATE collections SET name = ?, status = 'ACTIVE', updated_at = ? WHERE collection_id = ?", (collection.name, now, collection_id))
                cursor.execute("UPDATE collection_sources SET last_sync = ? WHERE collection_id = ?", (now, collection_id))

            incoming_song_ids = set()

            for i, item in enumerate(collection.items, 1):
                self._normalize_media_item(item, collection.source_type)
                if job_context and job_context.is_cancelled:
                    logger.info("Collection refresh cancelled cooperatively.")
                    break
                if job_context:
                    job_context.wait_if_paused()
                    
                if job_context:
                    job_context.update_progress(i, len(collection.items))
                    job_context.update_current_item(f"{item.artist} - {item.title}")

                song_id = self._resolve_media_id(cursor, item)
                if not song_id:
                    song_id = str(uuid.uuid4())
                    acq_info = json.dumps(item.acquisition_info) if item.has_acquisition_info() else None
                    initial_status = 'MATCHED' if item.has_acquisition_info() else 'DISCOVERED'
                    cursor.execute("""
                        INSERT INTO songs (song_id, media_type, title, artist, album, duration_ms, status, acquisition_info, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (song_id, item.media_type, item.title, item.artist, item.album, item.duration_ms, initial_status, acq_info, now))
                self._register_external_id(cursor, song_id, item, collection.source_type, now)
                
                incoming_song_ids.add(song_id)
                
                cursor.execute("""
                    INSERT INTO collection_songs (collection_id, song_id, position, added_at, status)
                    VALUES (?, ?, ?, ?, 'ACTIVE')
                    ON CONFLICT(collection_id, song_id) DO UPDATE SET 
                        position = excluded.position,
                        status = 'ACTIVE',
                        archived_at = NULL
                """, (collection_id, song_id, i, now))

            if incoming_song_ids:
                placeholders = ",".join("?" for _ in incoming_song_ids)
                cursor.execute(
                    f"DELETE FROM collection_songs WHERE collection_id = ? AND song_id NOT IN ({placeholders})",
                    [collection_id, *incoming_song_ids],
                )
            else:
                cursor.execute("DELETE FROM collection_songs WHERE collection_id = ?", (collection_id,))

            self.history.create_committed_revision(cursor, collection_id, "refresh", now)
                
            conn.commit()
            
        logger.info(f"Refreshed '{collection.name}': {len(incoming_song_ids)} active tracks.")
        return collection_id

    def save_media_item(self, item: MediaItem) -> str:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self._normalize_media_item(item, item.external_source)
            song_id = self._resolve_media_id(cursor, item)
            
            if not song_id:
                song_id = str(uuid.uuid4())
                acq_info = json.dumps(item.acquisition_info) if item.has_acquisition_info() else None
                initial_status = 'MATCHED' if item.has_acquisition_info() else 'DISCOVERED'
                
                cursor.execute("""
                    INSERT INTO songs (song_id, media_type, title, artist, album, duration_ms, status, acquisition_info, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (song_id, item.media_type, item.title, item.artist, item.album, item.duration_ms, initial_status, acq_info, now))
            self._register_external_id(cursor, song_id, item, item.external_source, now)
            conn.commit()
            
        return song_id

    def _normalize_media_item(self, item: MediaItem, fallback_source: str | None = None) -> None:
        title = self._clean_required_text(item.title)
        artist = self._clean_required_text(item.artist)

        if not title:
            title = self._fallback_title(item)
            logger.warning(
                "Media item missing title; using fallback title=%r source=%r external_id=%r external_url=%r",
                title,
                item.external_source or fallback_source,
                item.external_id,
                item.external_url,
            )
        if not artist:
            artist = "Unknown Artist"
            logger.warning(
                "Media item missing artist; using fallback artist=%r title=%r source=%r external_id=%r external_url=%r",
                artist,
                title,
                item.external_source or fallback_source,
                item.external_id,
                item.external_url,
            )

        item.title = title
        item.artist = artist
        item.media_type = self._clean_required_text(item.media_type) or "music"

    def _clean_required_text(self, value) -> str:
        return str(value).strip() if value is not None else ""

    def _fallback_title(self, item: MediaItem) -> str:
        if item.external_source == "youtube" or item.acquisition_info.get("youtube_id"):
            youtube_id = item.acquisition_info.get("youtube_id") or item.external_id
            return f"YouTube Video {youtube_id}" if youtube_id else "Unknown YouTube Title"
        if item.external_id:
            return f"Untitled {item.external_source or 'media'} item {item.external_id}"
        return "Unknown Title"

    def create_custom_collection(self, name: str, group_id: str | None = None) -> str:
        col_id = str(uuid.uuid4())
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            # FIX: Explicitly inject 'ACTIVE' into custom collections
            conn.execute("INSERT OR IGNORE INTO collections (collection_id, name, collection_type, group_id, status, created_at, updated_at) VALUES (?, ?, 'custom', ?, 'ACTIVE', ?, ?)", (col_id, name, group_id, now, now))
            conn.execute("INSERT OR IGNORE INTO collection_sources (collection_id, source_type, last_sync) VALUES (?, 'manual', NULL)", (col_id,))
            conn.commit()
        return col_id

    def create_vibe_collection(self, name: str, rule: dict, group_id: str | None = None) -> str:
        col_id = str(uuid.uuid4())
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        matches = self._matching_song_ids_for_vibe_rule(rule)
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO collections (collection_id, name, collection_type, group_id, status, created_at, updated_at)
                VALUES (?, ?, 'vibe', ?, 'ACTIVE', ?, ?)
                """,
                (col_id, name, group_id, now, now),
            )
            cursor.execute(
                """
                INSERT INTO collection_sources (collection_id, source_type, external_id, external_url, last_sync)
                VALUES (?, 'vibe', ?, NULL, ?)
                """,
                (col_id, col_id, now),
            )
            cursor.execute(
                """
                INSERT INTO collection_rules (collection_id, rule_type, rule_json, created_at, updated_at)
                VALUES (?, 'vibe_v1', ?, ?, ?)
                """,
                (col_id, json.dumps(rule, sort_keys=True), now, now),
            )
            self._apply_vibe_collection_matches(cursor, col_id, matches, now, "create_vibe")
            conn.commit()
        return col_id

    def get_collection_rule(self, collection_id: str) -> dict | None:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT rule_json FROM collection_rules WHERE collection_id = ?", (collection_id,))
            row = cursor.fetchone()
            if not row:
                return None
            try:
                return json.loads(row["rule_json"])
            except (TypeError, json.JSONDecodeError):
                return None

    def refresh_vibe_collection(self, collection_id: str, job_context=None) -> int:
        rule = self.get_collection_rule(collection_id)
        if not rule:
            raise ValueError("Collection has no vibe rule.")
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        matches = self._matching_song_ids_for_vibe_rule(rule)
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            count = self._apply_vibe_collection_matches(cursor, collection_id, matches, now, "refresh_vibe", job_context=job_context)
            cursor.execute("UPDATE collection_sources SET last_sync = ? WHERE collection_id = ? AND source_type = 'vibe'", (now, collection_id))
            cursor.execute("UPDATE collections SET updated_at = ? WHERE collection_id = ?", (now, collection_id))
            conn.commit()
            return count

    def search_vibe_rule(self, rule: dict) -> list[dict]:
        song_ids = self._matching_song_ids_for_vibe_rule(rule)
        if not song_ids:
            return []
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            placeholders = ",".join("?" for _ in song_ids)
            cursor.execute(
                f"""
                SELECT DISTINCT s.*, m.youtube_id, f.filepath
                FROM songs s
                LEFT JOIN matches m ON s.song_id = m.song_id AND m.approved = 1
                LEFT JOIN files f ON s.song_id = f.song_id
                WHERE s.song_id IN ({placeholders})
                """,
                song_ids,
            )
            by_id = {row["song_id"]: dict(row) for row in cursor.fetchall()}
        return [by_id[song_id] for song_id in song_ids if song_id in by_id]

    def vibe_rule_counts(self, rule: dict) -> dict:
        required_filters = self._rules_to_feature_filters(rule.get("required", {}).get("rules", []))
        required_count = self._count_feature_filters(required_filters)
        group_counts = []
        total_seen: set[str] = set()
        groups = rule.get("groups") or []
        for group in groups:
            group_filters = self._rules_to_feature_filters(group.get("rules", []))
            merged = self._merge_feature_filters(required_filters, group_filters)
            ids = self._song_ids_for_feature_filters(merged)
            group_counts.append(len(ids))
            total_seen.update(ids)
        if not groups:
            total_seen.update(self._song_ids_for_feature_filters(required_filters))
        return {
            "required": required_count,
            "groups": group_counts,
            "total": len(total_seen),
        }

    def _apply_vibe_collection_matches(self, cursor, collection_id: str, matches: list[str], now: str, reason: str, job_context=None) -> int:
        for position, song_id in enumerate(matches, 1):
            if job_context and job_context.is_cancelled:
                break
            if job_context:
                job_context.wait_if_paused()
                job_context.update_progress(position, len(matches))
                job_context.update_current_item(f"Vibe match {position} of {len(matches)}")
            cursor.execute(
                """
                INSERT INTO collection_songs (collection_id, song_id, position, added_at, status)
                VALUES (?, ?, ?, ?, 'ACTIVE')
                ON CONFLICT(collection_id, song_id) DO UPDATE SET
                    position = excluded.position,
                    status = 'ACTIVE',
                    archived_at = NULL
                """,
                (collection_id, song_id, position, now),
            )
        if matches:
            placeholders = ",".join("?" for _ in matches)
            cursor.execute(
                f"DELETE FROM collection_songs WHERE collection_id = ? AND song_id NOT IN ({placeholders})",
                [collection_id, *matches],
            )
        else:
            cursor.execute("DELETE FROM collection_songs WHERE collection_id = ?", (collection_id,))
        self.history.create_committed_revision(cursor, collection_id, reason, now)
        return len(matches)

    def _matching_song_ids_for_vibe_rule(self, rule: dict) -> list[str]:
        conjunctions = self._vibe_rule_conjunctions(rule)
        seen: set[str] = set()
        ordered: list[str] = []
        for filters in conjunctions:
            for song_id in self._song_ids_for_feature_filters(filters):
                if song_id not in seen:
                    seen.add(song_id)
                    ordered.append(song_id)
        return ordered

    def _song_ids_for_feature_filters(self, filters: dict[str, list[int]] | None) -> list[str]:
        if filters is None:
            return []
        query = MediaQuery(audio_feature_filters=filters, archived=False)
        return [item.get("song_id") for item in self.search_media(query) if item.get("song_id")]

    def _count_feature_filters(self, filters: dict[str, list[int]] | None) -> int:
        if filters is None:
            return 0
        return self.count_media(MediaQuery(audio_feature_filters=filters, archived=False))

    def _vibe_rule_conjunctions(self, rule: dict) -> list[dict[str, list[int]] | None]:
        required = self._rules_to_feature_filters(rule.get("required", {}).get("rules", []))
        groups = rule.get("groups") or []
        if not groups:
            return [required]
        conjunctions = []
        for group in groups:
            group_filters = self._rules_to_feature_filters(group.get("rules", []))
            conjunctions.append(self._merge_feature_filters(required, group_filters))
        return conjunctions

    def _rules_to_feature_filters(self, rules: list[dict]) -> dict[str, list[int]] | None:
        filters: dict[str, set[int]] = {}
        for rule in rules or []:
            field = rule.get("field")
            if field not in AUDIO_FEATURE_FILTER_EXPRESSIONS:
                continue
            scores = set()
            for value in rule.get("values") or []:
                try:
                    scores.add(int(value))
                except (TypeError, ValueError):
                    continue
            if not scores:
                continue
            existing = filters.get(field)
            filters[field] = scores if existing is None else existing.intersection(scores)
            if not filters[field]:
                return None
        return {field: sorted(scores) for field, scores in filters.items()}

    def _merge_feature_filters(self, left: dict[str, list[int]] | None, right: dict[str, list[int]] | None) -> dict[str, list[int]] | None:
        if left is None or right is None:
            return None
        merged = {field: set(scores) for field, scores in left.items()}
        for field, scores in right.items():
            score_set = set(scores)
            if field in merged:
                merged[field] = merged[field].intersection(score_set)
                if not merged[field]:
                    return None
            else:
                merged[field] = score_set
        return {field: sorted(scores) for field, scores in merged.items()}

    def _audio_feature_filters_sql(self, filters: dict[str, list[int]] | None, params: list) -> str:
        clauses = []
        for field, scores in (filters or {}).items():
            clause = self._audio_feature_score_sql(field, scores, params)
            if clause:
                clauses.append(clause)
        return "(" + " AND ".join(clauses) + ")" if clauses else ""

    def _audio_feature_score_sql(self, field: str, scores: list[int], params: list) -> str:
        expression_info = AUDIO_FEATURE_FILTER_EXPRESSIONS.get(field)
        if not expression_info:
            return ""
        expression, thresholds = expression_info
        score_clauses = []
        for raw_score in scores:
            try:
                score = int(raw_score)
            except (TypeError, ValueError):
                continue
            if score < 1 or score > len(thresholds) + 1:
                continue
            if score == 1:
                score_clauses.append(f"({expression} IS NOT NULL AND {expression} < ?)")
                params.append(thresholds[0])
            elif score == len(thresholds) + 1:
                score_clauses.append(f"({expression} >= ?)")
                params.append(thresholds[-1])
            else:
                score_clauses.append(f"({expression} >= ? AND {expression} < ?)")
                params.extend([thresholds[score - 2], thresholds[score - 1]])
        return "(" + " OR ".join(score_clauses) + ")" if score_clauses else ""

    def _audio_feature_rule_sql(self, rule: dict | None, params: list) -> str:
        rule = rule or {}
        has_rules = bool(rule.get("required", {}).get("rules")) or any(group.get("rules") for group in rule.get("groups") or [])
        conjunctions = self._vibe_rule_conjunctions(rule)
        clauses = []
        for filters in conjunctions:
            clause = self._audio_feature_filters_sql(filters, params)
            if clause:
                clauses.append(clause)
        if clauses:
            return "(" + " OR ".join(clauses) + ")"
        return "0=1" if has_rules else ""

    def copy_collection(self, source_collection_id: str, name: str, revision_id: str | None = None, group_id: str | None = None) -> str:
        new_collection_id = str(uuid.uuid4())
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            if group_id is None:
                cursor.execute("SELECT group_id FROM collections WHERE collection_id = ?", (source_collection_id,))
                row = cursor.fetchone()
                group_id = row["group_id"] if row else None
            cursor.execute(
                """
                INSERT INTO collections (collection_id, name, collection_type, group_id, status, created_at, updated_at)
                VALUES (?, ?, 'custom', ?, 'ACTIVE', ?, ?)
                """,
                (new_collection_id, name, group_id, now, now),
            )
            cursor.execute(
                "INSERT INTO collection_sources (collection_id, source_type, last_sync) VALUES (?, 'manual', NULL)",
                (new_collection_id,),
            )
            if revision_id:
                cursor.execute(
                    """
                    INSERT INTO collection_songs (collection_id, song_id, position, added_at, status)
                    SELECT ?, song_id, position, ?, 'ACTIVE'
                    FROM collection_revision_items
                    WHERE revision_id = ?
                    ORDER BY position ASC
                    """,
                    (new_collection_id, now, revision_id),
                )
                reason = "copy_revision"
            else:
                cursor.execute(
                    """
                    INSERT INTO collection_songs (collection_id, song_id, position, added_at, status)
                    SELECT ?, song_id, position, ?, 'ACTIVE'
                    FROM collection_songs
                    WHERE collection_id = ? AND status = 'ACTIVE'
                    ORDER BY position ASC
                    """,
                    (new_collection_id, now, source_collection_id),
                )
                reason = "copy_collection"
            self.history.create_committed_revision(cursor, new_collection_id, reason, now)
            conn.commit()
        return new_collection_id

    def _resolve_collection_id(self, cursor, source_type: str, external_id: Optional[str]) -> Optional[str]:
        if not external_id: return None
        cursor.execute("SELECT collection_id FROM collection_sources WHERE source_type = ? AND external_id = ?", (source_type, external_id))
        row = cursor.fetchone()
        return row['collection_id'] if row else None

    def _resolve_media_id(self, cursor, item: MediaItem) -> Optional[str]:
        source_type = item.external_source
        if source_type and item.external_id:
            cursor.execute(
                "SELECT song_id FROM song_external_ids WHERE source_type = ? AND external_id = ?",
                (source_type, item.external_id),
            )
            row = cursor.fetchone()
            if row:
                return row['song_id']

        if item.external_id:
            cursor.execute("SELECT song_id FROM songs WHERE spotify_id = ?", (item.external_id,))
            row = cursor.fetchone()
            if row:
                return row['song_id']

        cursor.execute("SELECT song_id FROM songs WHERE title = ? AND artist = ?", (item.title, item.artist))
        row = cursor.fetchone()
        return row['song_id'] if row else None

    def _register_external_id(self, cursor, song_id: str, item: MediaItem, fallback_source: str | None, now: str) -> None:
        source_type = item.external_source or fallback_source
        if not source_type or not item.external_id:
            return
        cursor.execute(
            """
            INSERT INTO song_external_ids (song_id, source_type, external_id, external_url, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_type, external_id) DO UPDATE SET
                song_id = excluded.song_id,
                external_url = COALESCE(excluded.external_url, song_external_ids.external_url),
                last_seen_at = excluded.last_seen_at
            """,
            (song_id, source_type, item.external_id, item.external_url, now, now),
        )
        if source_type == "spotify":
            cursor.execute("UPDATE songs SET spotify_id = COALESCE(spotify_id, ?) WHERE song_id = ?", (item.external_id, song_id))

    def archive_collection(self, collection_id: str):
        with self.db.get_connection() as conn:
            conn.execute("UPDATE collections SET status = 'ARCHIVED', updated_at = ? WHERE collection_id = ?", (datetime.datetime.now(datetime.timezone.utc).isoformat(), collection_id))
            conn.commit()

    def restore_collection(self, collection_id: str):
        with self.db.get_connection() as conn:
            conn.execute("UPDATE collections SET status = 'ACTIVE', updated_at = ? WHERE collection_id = ?", (datetime.datetime.now(datetime.timezone.utc).isoformat(), collection_id))
            conn.commit()

    def delete_collection(self, collection_id: str):
        self.archive_collection(collection_id)

    def get_all_media_items(self) -> list[dict]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT song_id, title, artist, media_type, status FROM songs ORDER BY artist, title")
            return [dict(row) for row in cursor.fetchall()]

    def add_standalone_item(self, title: str, artist: str, media_type: str = 'music', url: str = None) -> str:
        song_id = str(uuid.uuid4())
        acq_info = json.dumps({"direct_url": url}) if url else None
        initial_status = 'MATCHED' if url else 'DISCOVERED'
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            conn.execute("INSERT INTO songs (song_id, media_type, title, artist, status, acquisition_info, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (song_id, media_type, title, artist, initial_status, acq_info, now))
            conn.commit()
        return song_id

    def add_item_to_collection(self, collection_id: str, song_id: str):
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(position) as max_pos FROM collection_songs WHERE collection_id = ? AND status = 'ACTIVE'", (collection_id,))
            next_pos = (cursor.fetchone()['max_pos'] or 0) + 1
            cursor.execute("""
                INSERT INTO collection_songs (collection_id, song_id, position, added_at, status)
                VALUES (?, ?, ?, ?, 'ACTIVE')
                ON CONFLICT(collection_id, song_id) DO UPDATE SET position = excluded.position, status = 'ACTIVE', archived_at = NULL
            """, (collection_id, song_id, next_pos, now))
            self.history.sync_draft_revision(cursor, collection_id, "manual_edit", now)
            conn.commit()

    def get_collection_tracks(self, collection_id: str) -> list[dict]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT s.song_id, s.title, s.artist, s.status as song_status, 
                       cs.position, cs.status as membership_status, cs.added_at, cs.archived_at
                FROM songs s
                JOIN collection_songs cs ON s.song_id = cs.song_id
                WHERE cs.collection_id = ?
                  AND cs.status = 'ACTIVE'
                ORDER BY cs.status ASC, cs.position ASC
            """, (collection_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_track_details(self, song_id: str) -> dict:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    s.*, 
                    m.youtube_id,
                    f.filepath,
                    rq.reason AS review_reason,
                    rq.source_url AS review_source_url,
                    rq.source_error AS review_source_error
                FROM songs s
                LEFT JOIN (
                    SELECT song_id, youtube_id
                    FROM matches
                    WHERE approved = 1
                    ORDER BY match_id DESC
                ) m ON s.song_id = m.song_id
                LEFT JOIN files f ON s.song_id = f.song_id
                LEFT JOIN review_queue rq ON s.song_id = rq.song_id AND rq.status = 'PENDING'
                WHERE s.song_id = ?
            """, (song_id,))
            song = dict(cursor.fetchone() or {})
            if not song:
                return {}

            cursor.execute("""
                SELECT c.name
                FROM collections c
                JOIN collection_songs cs ON c.collection_id = cs.collection_id
                WHERE cs.song_id = ?
                  AND (c.status != 'DELETED' OR c.status IS NULL)
                  AND (c.status != 'ARCHIVED' OR c.status IS NULL)
                  AND cs.status = 'ACTIVE'
            """, (song_id,))
            song['collections'] = [row['name'] for row in cursor.fetchall()]
            return song

    def remove_item_from_collection(self, collection_id: str, song_id: str):
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM collection_songs WHERE collection_id = ? AND song_id = ?", (collection_id, song_id))
            self.history.sync_draft_revision(cursor, collection_id, "manual_edit", now)
            conn.commit()

    def delete_standalone_item(self, song_id: str):
        with self.db.get_connection() as conn:
            conn.execute("DELETE FROM songs WHERE song_id = ?", (song_id,))
            conn.commit()

    def recover_draft_revisions(self) -> int:
        return self.history.recover_draft_revisions()

    def commit_all_draft_revisions(self, reason: str = "app_close") -> int:
        return self.history.commit_all_draft_revisions(reason)

    def commit_collection_draft(self, collection_id: str, reason: str = "manual_edit") -> bool:
        return self.history.commit_collection_draft(collection_id, reason)

    def get_collection_history(self, collection_id: str) -> list[dict]:
        return self.history.get_collection_history(collection_id)

    def get_collection_revision_tracks(self, collection_id: str, revision_id: str | None = None) -> list[dict]:
        return self.history.get_collection_revision_tracks(collection_id, revision_id)

    def get_collection_source_url(self, collection_id: str, source_type: str) -> str | None:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT external_url FROM collection_sources WHERE collection_id = ? AND source_type = ?",
                (collection_id, source_type),
            )
            row = cursor.fetchone()
            return row["external_url"] if row else None

    def count_active_collection_tracks(self, collection_id: str) -> int:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS total FROM collection_songs WHERE collection_id = ? AND status = 'ACTIVE'",
                (collection_id,),
            )
            return int(cursor.fetchone()["total"] or 0)

    def archive_media_item(self, song_id: str, collection_id: str | None = None) -> None:
        with self.db.get_connection() as conn:
            conn.execute("UPDATE songs SET status = 'ARCHIVED' WHERE song_id = ?", (song_id,))
            conn.commit()

    def unarchive_media_item(self, song_id: str) -> None:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT
                    s.acquisition_info,
                    EXISTS(SELECT 1 FROM matches m WHERE m.song_id = s.song_id AND m.approved = 1) AS has_match,
                    EXISTS(SELECT 1 FROM review_queue rq WHERE rq.song_id = s.song_id AND rq.status = 'PENDING') AS in_review,
                    EXISTS(SELECT 1 FROM download_failures df WHERE df.song_id = s.song_id AND df.promoted_at IS NOT NULL) AS has_failed_match
                FROM songs s
                WHERE s.song_id = ?
                """,
                (song_id,),
            )
            row = cursor.fetchone()
            if not row:
                return
            if row["has_failed_match"]:
                status = "MATCH_FAILED"
            elif row["in_review"]:
                status = "REVIEW"
            elif row["has_match"] or row["acquisition_info"]:
                status = "MATCHED"
            else:
                status = "DISCOVERED"
            conn.execute("UPDATE songs SET status = ? WHERE song_id = ?", (status, song_id))
            conn.commit()

    def _build_media_query(self, query: MediaQuery, count_only: bool = False):
        """Dynamically constructs the SQL based on the MediaQuery parameters."""
        if count_only:
            select_clause = "SELECT COUNT(DISTINCT s.song_id) as total"
        elif query.collection_id:
            select_clause = "SELECT DISTINCT s.*, m.youtube_id, f.filepath, COALESCE(sf.rating, 'neutral') AS feedback_rating, cs.position as collection_position"
        else:
            select_clause = "SELECT DISTINCT s.*, m.youtube_id, f.filepath, COALESCE(sf.rating, 'neutral') AS feedback_rating"
        from_clause = "FROM songs s"
        joins = [
            "LEFT JOIN matches m ON s.song_id = m.song_id AND m.approved = 1",
            "LEFT JOIN files f ON s.song_id = f.song_id",
            "LEFT JOIN song_feedback sf ON s.song_id = sf.song_id"
        ]
        where = ["1=1"]
        params = []

        if query.collection_id:
            joins.append("LEFT JOIN collection_songs cs ON s.song_id = cs.song_id")

        sort_by_audio_feature = query.sort_by in AUDIO_FEATURE_SORT_COLUMNS
        filter_by_audio_analysis = query.audio_analysis_statuses is not None
        filter_by_audio_feature = bool(query.audio_feature_filters) or bool(getattr(query, "audio_feature_rule", None))
        if sort_by_audio_feature or filter_by_audio_analysis or filter_by_audio_feature:
            joins.append("LEFT JOIN song_audio_features af ON s.song_id = af.song_id")

        if query.text:
            where.append("(s.title LIKE ? OR s.artist LIKE ?)")
            params.extend([f"%{query.text}%", f"%{query.text}%"])

        if query.collection_id:
            where.append("cs.collection_id = ?")
            params.append(query.collection_id)
            where.append("cs.status = 'ACTIVE'")

        if query.media_type:
            where.append("s.media_type = ?")
            params.append(query.media_type)

        if query.media_types is not None:
            if query.media_types:
                placeholders = ", ".join("?" for _ in query.media_types)
                where.append(f"s.media_type IN ({placeholders})")
                params.extend(query.media_types)
            else:
                where.append("0=1")

        if query.status_groups is not None:
            groups = set(query.status_groups)
            status_clauses = []
            if "downloaded" in groups:
                status_clauses.append("(s.status != 'ARCHIVED' AND f.filepath IS NOT NULL)")
            if "matched" in groups:
                status_clauses.append("(s.status NOT IN ('ARCHIVED', 'MATCH_FAILED') AND f.filepath IS NULL AND (s.status = 'MATCHED' OR m.youtube_id IS NOT NULL))")
            if "failed" in groups:
                status_clauses.append("(s.status = 'MATCH_FAILED')")
            if "review" in groups:
                status_clauses.append("(s.status != 'ARCHIVED' AND s.status = 'REVIEW')")
            if "discovered" in groups:
                status_clauses.append("(s.status != 'ARCHIVED' AND s.status = 'DISCOVERED')")
            if "archived" in groups:
                status_clauses.append("s.status = 'ARCHIVED'")
            where.append("(" + " OR ".join(status_clauses) + ")" if status_clauses else "0=1")
        else:
            if query.downloaded is True:
                where.append("f.filepath IS NOT NULL")
            elif query.downloaded is False:
                where.append("f.filepath IS NULL")

            if query.matched is True:
                where.append("(s.status != 'MATCH_FAILED' AND (s.status = 'MATCHED' OR m.youtube_id IS NOT NULL))")
            elif query.matched is False:
                where.append("s.status != 'MATCHED'")

            if query.in_review_queue is True:
                where.append("s.status = 'REVIEW'")
            elif query.in_review_queue is False:
                where.append("s.status != 'REVIEW'")

            if query.archived is True:
                where.append("s.status = 'ARCHIVED'")
            elif query.archived is False:
                where.append("s.status != 'ARCHIVED'")

        if query.orphaned is True:
            where.append("NOT EXISTS (SELECT 1 FROM collection_songs cs2 WHERE cs2.song_id = s.song_id AND cs2.status = 'ACTIVE')")
        elif query.orphaned is False:
            where.append("EXISTS (SELECT 1 FROM collection_songs cs2 WHERE cs2.song_id = s.song_id AND cs2.status = 'ACTIVE')")

        if query.audio_analysis_statuses is not None:
            status_clauses = []
            statuses = {str(status).lower() for status in query.audio_analysis_statuses}
            if "analyzed" in statuses:
                checks = [
                    f"af.{FEATURE_GROUP_VERSION_COLUMNS[group]} = ?"
                    for group in FEATURE_GROUP_VERSIONS
                ]
                status_clauses.append("(af.analysis_status = 'COMPLETE' AND " + " AND ".join(checks) + ")")
                params.extend(FEATURE_GROUP_VERSIONS.values())
            if "failed" in statuses:
                status_clauses.append("af.analysis_status = 'FAILED'")
            if "missing" in statuses:
                status_clauses.append("af.song_id IS NULL")
            if "stale" in statuses:
                checks = [
                    f"af.{FEATURE_GROUP_VERSION_COLUMNS[group]} IS NULL OR af.{FEATURE_GROUP_VERSION_COLUMNS[group]} != ?"
                    for group in FEATURE_GROUP_VERSIONS
                ]
                status_clauses.append("(af.analysis_status = 'COMPLETE' AND (" + " OR ".join(checks) + "))")
                params.extend(FEATURE_GROUP_VERSIONS.values())
            where.append("(" + " OR ".join(status_clauses) + ")" if status_clauses else "0=1")
            where.append("f.downloaded = 1")
            where.append("s.status != 'ARCHIVED'")

        if query.audio_feature_filters:
            feature_clause = self._audio_feature_filters_sql(query.audio_feature_filters, params)
            if feature_clause:
                where.append(feature_clause)

        if getattr(query, "audio_feature_rule", None):
            rule_clause = self._audio_feature_rule_sql(query.audio_feature_rule, params)
            if rule_clause:
                where.append(rule_clause)

        if query.feedback_ratings is not None:
            ratings = [str(rating).lower() for rating in query.feedback_ratings if str(rating).lower() in ("liked", "neutral", "disliked")]
            if ratings:
                placeholders = ", ".join("?" for _ in ratings)
                where.append(f"COALESCE(sf.rating, 'neutral') IN ({placeholders})")
                params.extend(ratings)
            else:
                where.append("0=1")

        sql = f"{select_clause} {from_clause} {' '.join(joins)} WHERE {' AND '.join(where)}"

        if not count_only:
            alpha_trim_chars = " \t\r\n0123456789!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
            alpha_trim_sql = "'" + alpha_trim_chars.replace("'", "''") + "'"
            alpha_title = f"LOWER(LTRIM(s.title, {alpha_trim_sql}))"
            alpha_artist = f"LOWER(LTRIM(s.artist, {alpha_trim_sql}))"
            sort_columns = {
                "title": alpha_title,
                "artist": alpha_artist,
                "album": "s.album",
                "duration_ms": "s.duration_ms",
                "media_type": "s.media_type",
                "status": "s.status",
                "created_at": "s.created_at",
                "downloaded": "f.filepath",
            }
            if query.collection_id:
                sort_columns["collection_order"] = "cs.position"
            order = "ASC" if query.sort_order.lower() == "asc" else "DESC"
            if query.sort_by == "preference":
                preference_order = "CASE COALESCE(sf.rating, 'neutral') WHEN 'liked' THEN 0 WHEN 'neutral' THEN 1 WHEN 'disliked' THEN 2 ELSE 1 END"
                sql += f" ORDER BY {preference_order} {order}, {alpha_title} ASC"
            elif sort_by_audio_feature:
                sort_col = AUDIO_FEATURE_SORT_COLUMNS[query.sort_by]
                sql += f" ORDER BY CASE WHEN {sort_col} IS NULL THEN 1 ELSE 0 END ASC, {sort_col} {order}, s.title ASC"
            else:
                sort_col = sort_columns.get(query.sort_by, alpha_title)
                sql += f" ORDER BY {sort_col} {order}, s.title ASC"
            if query.limit:
                sql += f" LIMIT {query.limit} OFFSET {query.offset}"

        return sql, params

    def search_media(self, query: MediaQuery) -> list[dict]:
        """Returns a result set of media items matching the query."""
        sql, params = self._build_media_query(query)
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            results = [dict(row) for row in cursor.fetchall()]

        # Post-process for physical file verification if requested
        if query.file_missing is not None:
            filtered = []
            for r in results:
                path_exists = bool(r.get('filepath')) and Path(r['filepath']).exists()
                is_missing = bool(r.get('filepath')) and not path_exists
                if query.file_missing == is_missing:
                    filtered.append(r)
            return filtered

        return results

    def count_media(self, query: MediaQuery) -> int:
        """Returns a fast integer count of matching media without pulling data."""
        sql, params = self._build_media_query(query, count_only=True)
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            return cursor.fetchone()['total']
        
    def query(self, query_params: CollectionQuery) -> list[Collection]:
        """
        Retrieves a filtered list of Collection domain objects.
        Hides all database joins and aggregation logic from the UI.
        """
        # Base query: Join collections, sources, and count the ACTIVE tracks
        sql = """
            SELECT 
                c.collection_id, 
                c.name, 
                c.collection_type,
                c.status, 
                cs_src.source_type, 
                cs_src.external_url,
                cs_src.external_id,
                cs_src.last_sync,
                c.updated_at,
                COUNT(CASE WHEN cs.status = 'ACTIVE' THEN 1 END) as item_count
            FROM collections c
            LEFT JOIN collection_sources cs_src ON c.collection_id = cs_src.collection_id
            LEFT JOIN collection_songs cs ON c.collection_id = cs.collection_id
            WHERE 1=1
        """
        
        where_clauses = []
        params = []

        # 1. Archive Status
        if query_params.archived is True:
            where_clauses.append("c.status = 'ARCHIVED'")
        elif query_params.archived is False:
            where_clauses.append("(c.status != 'DELETED' OR c.status IS NULL)")
            where_clauses.append("(c.status != 'ARCHIVED' OR c.status IS NULL)")

        # 2. Source Type Filter
        if query_params.source_type:
            where_clauses.append("cs_src.source_type = ?")
            params.append(query_params.source_type)

        # 3. Name Search (Fuzzy)
        if query_params.name_contains:
            where_clauses.append("c.name LIKE ?")
            params.append(f"%{query_params.name_contains}%")

        if where_clauses:
            sql += " AND " + " AND ".join(where_clauses)

        # We must group by the collection to execute the COUNT() aggregate
        sql += " GROUP BY c.collection_id"

        # 4. Item Count Filters (Must use HAVING because it's an aggregated column)
        having_clauses = []
        if query_params.min_items is not None:
            having_clauses.append("item_count >= ?")
            params.append(query_params.min_items)
            
        if query_params.max_items is not None:
            having_clauses.append("item_count <= ?")
            params.append(query_params.max_items)

        if having_clauses:
            sql += " HAVING " + " AND ".join(having_clauses)

        sql += " ORDER BY c.name ASC"

        # Execute and map to Domain Objects
        collections_result = []
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            
            for row in cursor.fetchall():
                collections_result.append(Collection(
                    id=row['collection_id'],
                    name=row['name'],
                    source_type=row['source_type'] or 'manual',
                    external_id=row['external_id'],
                    external_url=row['external_url'],
                    archived=(row['status'] == 'ARCHIVED'),
                    item_count=row['item_count'],
                    items=[] # We don't load the massive track list into memory for summary queries
                ))

        return collections_result
