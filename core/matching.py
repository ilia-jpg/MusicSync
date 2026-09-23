import logging
import sqlite3

from infrastructure.config import ConfigManager
from infrastructure.database import DatabaseManager
from infrastructure.models import AcquisitionCandidate, MediaItem
from infrastructure.plugin_contracts import ResolverPlugin

logger = logging.getLogger(__name__)

class MatchingEngine:
    def __init__(self, config: ConfigManager, db: DatabaseManager, resolvers: list[ResolverPlugin] | None = None):
        self.config = config
        self.db = db
        self.resolvers = resolvers or []
        self.auto_accept = self.config.get("matching.auto_accept_threshold", 0.95)
        self.variance = self.config.get("matching.similarity_variance", 0.05)
        self.max_cands = self.config.get("matching.max_review_candidates", 3)

    def process_discovered_tracks(self, job_context=None):
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM songs WHERE status = 'DISCOVERED'")
            songs = cursor.fetchall()

        if not songs:
            if not job_context: logger.info("No new songs to process.")
            return

        if not job_context: logger.info(f"\nSearching YouTube for {len(songs)} discovered items...")
        
        for i, song in enumerate(songs, 1):
            if job_context and job_context.is_cancelled:
                logger.info("Matching job cancelled cooperatively.")
                break
            if job_context:
                job_context.wait_if_paused()
                
            if job_context:
                job_context.update_progress(i, len(songs))
                job_context.update_current_item(f"Matching: {song['title']} - {song['artist']}")

            self._process_single_track(song)

    def process_single_track_by_id(self, song_id: str, job_context=None, force_review: bool = False) -> None:
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT * FROM songs WHERE song_id = ?", (song_id,)).fetchone()
        if not row:
            raise RuntimeError("Media item not found.")
        if job_context:
            job_context.update_progress(1, 1)
            job_context.update_current_item(f"Matching: {row['title']} - {row['artist']}")
            job_context.wait_if_paused()
        if not (job_context and job_context.is_cancelled):
            self._process_single_track(row, force_review=force_review)

    def _process_single_track(self, song: sqlite3.Row, force_review: bool = False):
        song_id = song['song_id']
        item = self._media_item_from_row(song)

        candidates = self._resolve_candidates(item)
        candidates = self._dedupe_candidates(candidates)
        candidates.sort(key=lambda candidate: candidate.confidence, reverse=True)
        self._route_match(song, candidates, force_review=force_review)

    def _media_item_from_row(self, song: sqlite3.Row) -> MediaItem:
        return MediaItem(
            title=song["title"],
            artist=song["artist"],
            album=song["album"],
            duration_ms=song["duration_ms"] or 0,
            media_type=song["media_type"] or "music",
            external_id=song["spotify_id"],
        )

    def _resolve_candidates(self, item: MediaItem) -> list[AcquisitionCandidate]:
        candidates: list[AcquisitionCandidate] = []
        for resolver in self.resolvers:
            try:
                candidates.extend(resolver.resolve(item))
            except Exception as exc:
                provider = getattr(resolver, "provider", resolver.__class__.__name__)
                logger.debug(f"{provider} resolver failed for {item.title}: {exc}")
        return candidates

    def _route_match(self, song: sqlite3.Row, candidates: list[AcquisitionCandidate], force_review: bool = False):
        song_id = song['song_id']
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            
            top_score = candidates[0].confidence if candidates else 0.0
            cursor.execute("DELETE FROM matches WHERE song_id = ? AND approved = 0", (song_id,))

            if candidates and top_score >= self.auto_accept and not force_review:
                cursor.execute("UPDATE matches SET approved = 0 WHERE song_id = ?", (song_id,))
                cursor.execute("DELETE FROM matches WHERE song_id = ? AND approved = 0", (song_id,))
                self._insert_match(cursor, song_id, candidates[0], 1)
                self._update_song_status(song_id, "MATCHED", cursor)
            else:
                if force_review:
                    cursor.execute("UPDATE matches SET approved = 0 WHERE song_id = ?", (song_id,))
                    cursor.execute("UPDATE songs SET acquisition_info = NULL WHERE song_id = ?", (song_id,))
                    cursor.execute("DELETE FROM download_failures WHERE song_id = ?", (song_id,))
                for c in candidates[:self.max_cands]:
                    self._insert_match(cursor, song_id, c, 0)

                cursor.execute(
                    """
                    INSERT INTO review_queue (song_id, created_at, status, reason, source_url, source_error)
                    VALUES (?, datetime('now'), 'PENDING', ?, NULL, NULL)
                    ON CONFLICT(song_id) DO UPDATE SET
                        created_at = excluded.created_at,
                        status = 'PENDING',
                        reason = excluded.reason,
                        source_url = NULL,
                        source_error = NULL
                    """,
                    (song_id, "Forced rematch for review." if force_review else None),
                )
                self._update_song_status(song_id, "REVIEW", cursor)
                
            conn.commit()

    def _queue_review(self, song_id: str) -> None:
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO review_queue (song_id, created_at, status, reason, source_url, source_error)
                VALUES (?, datetime('now'), 'PENDING', NULL, NULL, NULL)
                ON CONFLICT(song_id) DO UPDATE SET
                    created_at = excluded.created_at,
                    status = 'PENDING',
                    reason = NULL,
                    source_url = NULL,
                    source_error = NULL
                """,
                (song_id,),
            )
            conn.execute("UPDATE songs SET status = 'REVIEW' WHERE song_id = ?", (song_id,))
            conn.commit()

    def _insert_match(self, cursor, song_id, candidate, approved):
        acquisition_info = candidate.acquisition_info or {}
        youtube_id = acquisition_info.get("youtube_id") or candidate.external_id
        cursor.execute("INSERT OR REPLACE INTO matches (song_id, youtube_id, yt_title, yt_duration, confidence, approved) VALUES (?, ?, ?, ?, ?, ?)", 
                       (song_id, youtube_id, candidate.title, candidate.duration_label, candidate.confidence, approved))

    def _dedupe_candidates(self, candidates: list[AcquisitionCandidate]) -> list[AcquisitionCandidate]:
        best: dict[str, AcquisitionCandidate] = {}
        for candidate in candidates:
            key = self._candidate_key(candidate)
            previous = best.get(key)
            if previous is None or candidate.confidence > previous.confidence:
                best[key] = candidate
        return list(best.values())

    def _candidate_key(self, candidate: AcquisitionCandidate) -> str:
        acquisition_info = candidate.acquisition_info or {}
        external_id = str(acquisition_info.get("youtube_id") or candidate.external_id or "").strip()
        if external_id:
            return f"id:{external_id}"
        title = " ".join(str(candidate.title or "").lower().split())
        return f"title:{title}|duration:{candidate.duration_label}"

    def _update_song_status(self, song_id, status, cursor=None):
        query = "UPDATE songs SET status = ? WHERE song_id = ?"
        if cursor: cursor.execute(query, (status, song_id))
        else:
            with self.db.get_connection() as conn:
                conn.execute(query, (status, song_id))
                conn.commit()
