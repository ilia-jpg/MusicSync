import sys
import logging
import sqlite3
import yt_dlp
import re
import json
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List

from infrastructure.database import DatabaseManager
from infrastructure.config import ConfigManager
from core.library import LibraryManager
from plugins.local_files import LocalFileAcquisitionPlugin

logger = logging.getLogger(__name__)

class QuietLogger:
    def debug(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


@dataclass
class DownloadResult:
    success: bool
    error: str | None = None
    category: str | None = None


class AcquisitionManager:
    def __init__(self, config: ConfigManager, db: DatabaseManager, library: LibraryManager, acquisition_plugins: list | None = None):
        self.config = config
        self.db = db
        self.library = library
        self.acquisition_plugins = acquisition_plugins or [LocalFileAcquisitionPlugin(config)]

    def download_media(self, media_items: List[dict], job_context=None, force: bool = False):
        """Downloads a provided subset of media items."""
        if not media_items:
            logger.info("No items provided for download.")
            if not job_context: print("No items provided for download.")
            return

        if not job_context: print(f"\n--- Starting Downloads ({len(media_items)} items) ---")
        failures: list[str] = []
        consecutive_failure_category: str | None = None
        consecutive_failure_count = 0
        circuit_breaker_threshold = 3
        
        for i, song in enumerate(media_items, 1):
            if job_context and job_context.is_cancelled:
                logger.info("Download job cancelled cooperatively.")
                break
            if job_context:
                job_context.wait_if_paused()
                
            if job_context:
                job_context.update_progress(i, len(media_items))
                job_context.update_current_item(f"{song['artist']} - {song['title']}")

            result = self._download_item(song, i, len(media_items), job_context, force=force)
            if result.success:
                consecutive_failure_category = None
                consecutive_failure_count = 0
                continue

            failures.append(f"{song['artist']} - {song['title']}")
            category = result.category or "download_failed"
            if category == consecutive_failure_category:
                consecutive_failure_count += 1
            else:
                consecutive_failure_category = category
                consecutive_failure_count = 1

            if category in {"youtube_format_unavailable", "youtube_access_or_format"} and consecutive_failure_count >= circuit_breaker_threshold:
                message = (
                    f"YouTube downloads are failing consistently ({category}) after "
                    f"{consecutive_failure_count} consecutive attempts. Try again later, update yt-dlp, "
                    "or change youtube.browser_cookies."
                )
                logger.error(message)
                if job_context:
                    job_context.update_progress_message(message)
                raise RuntimeError(message)
            
        if not job_context: print("\n--- Downloads Complete ---")
        if failures and not (job_context and job_context.is_cancelled):
            preview = "; ".join(failures[:3])
            extra = f" and {len(failures) - 3} more" if len(failures) > 3 else ""
            raise RuntimeError(f"Failed to download {len(failures)} of {len(media_items)} item(s): {preview}{extra}")

    def _download_item(self, song: sqlite3.Row, current: int, total: int, job_context=None, force: bool = False) -> DownloadResult:
        song_id = song['song_id']
        media_type = song['media_type'] or 'music'
        status = str(song['status'] or '').upper()
        if status == "MATCH_FAILED" and not force:
            logger.info("Skipping failed match for '%s' by '%s' during normal download job.", song['title'], song['artist'])
            return DownloadResult(True, category="skipped_match_failed")
        
        acquisition_info = {}
        if song['acquisition_info']:
            try:
                acquisition_info = json.loads(song['acquisition_info'])
            except json.JSONDecodeError:
                acquisition_info = {}

        file_path = self.library.get_expected_path(media_type, song['artist'], song['title'])
        display_title = (song['title'][:30] + '..') if len(song['title']) > 30 else song['title'].ljust(32)
        
        if file_path.exists():
            self.library.register_file(song_id, str(file_path))
            logger.info("Registered existing downloaded file for '%s' by '%s': %s", song['title'], song['artist'], file_path)
            return DownloadResult(True)

        plugin = self._acquisition_plugin_for(acquisition_info)
        if plugin:
            try:
                if plugin.acquire({**dict(song), "acquisition_info": acquisition_info}, file_path, job_context=job_context):
                    self.library.register_file(song_id, str(file_path))
                    logger.info("Acquired '%s' by '%s' to %s using %s", song['title'], song['artist'], file_path, plugin.provider)
                    return DownloadResult(True)
                return DownloadResult(False, category=f"{plugin.provider}_failed")
            except Exception as exc:
                logger.error("Acquisition failed for '%s' by '%s' using %s: %s", song['title'], song['artist'], plugin.provider, exc)
                return DownloadResult(False, str(exc), f"{plugin.provider}_failed")

        # Resolve legacy URL targets.
        target_url = None
        if 'direct_url' in acquisition_info:
            target_url = acquisition_info['direct_url']
        elif 'youtube_id' in acquisition_info:
            target_url = f"https://www.youtube.com/watch?v={acquisition_info['youtube_id']}"
        if not target_url and song['youtube_id']:
            target_url = f"https://www.youtube.com/watch?v={song['youtube_id']}"

        if not target_url:
            logger.error("No acquisition target found for '%s' by '%s'.", song['title'], song['artist'])
            return DownloadResult(False, "No acquisition target found.", "missing_target")

        # UI Hook & yt-dlp logic
        def progress_hook(d):
            # Mid-download cancellation!
            if job_context and job_context.is_cancelled:
                raise Exception("Job cancelled by user.")
            if job_context:
                job_context.wait_if_paused()
                
            if d['status'] == 'downloading' and not job_context:
                raw_percent = d.get('_percent_str', '0.0%')
                clean_percent = re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', raw_percent).strip()
                sys.stdout.write(f"\r[{current}/{total}] {display_title} | Downloading: {clean_percent}".ljust(80))
                sys.stdout.flush()

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': str(file_path.with_suffix('.%(ext)s')),
            'writethumbnail': True,
            'quiet': True,
            'no_warnings': True, 
            'no_color': True,
            'ffmpeg_location': 'C:/ffmpeg/bin', 
            'progress_hooks': [progress_hook],
            'postprocessors': [
                {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'},
                {'key': 'EmbedThumbnail'},
                {'key': 'FFmpegMetadata'},
            ],
            'postprocessor_args': [
                '-metadata', f'title={song["title"]}',
                '-metadata', f'artist={song["artist"]}',
                '-metadata', f'album={song["album"] or ""}'
            ],
            'logger': QuietLogger()
        }
        browser = self.config.get("youtube.browser_cookies")
        return self._download_youtube_with_fallbacks(
            ydl_opts,
            target_url,
            song,
            file_path,
            browser=browser,
            current=current,
            total=total,
            display_title=display_title,
            job_context=job_context,
        )
        if browser:
            ydl_opts["cookiesfrombrowser"] = (browser,)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([target_url])
            self.library.register_file(song_id, str(file_path))
            logger.info("Downloaded '%s' by '%s' to %s", song['title'], song['artist'], file_path)
            if not job_context: print(f"\r[{current}/{total}] {display_title} | ✅ Done!{' '*15}".ljust(80))
            return True
        except Exception as e:
            error_msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e).split('\n')[0])
            logger.error("Download failed for '%s' by '%s': %s", song['title'], song['artist'], error_msg)
            if not job_context: print(f"\r[{current}/{total}] {display_title} | ❌ Failed! ({error_msg[:40]}...){' '*15}".ljust(80))
            return False

    def _download_youtube_with_fallbacks(
        self,
        base_ydl_opts: dict,
        target_url: str,
        song: sqlite3.Row,
        file_path: Path,
        *,
        browser: str | None,
        current: int,
        total: int,
        display_title: str,
        job_context=None,
    ) -> DownloadResult:
        format_selectors = [
            "bestaudio/best",
            "bestaudio[ext=m4a]/bestaudio/best[acodec!=none]/best",
            "best[acodec!=none]/best",
        ]
        cookie_modes = [browser] if browser else [None]
        if browser:
            cookie_modes.append(None)

        last_error = None
        last_category = None
        attempts = 0
        for cookie_browser in cookie_modes:
            for selector in format_selectors:
                attempts += 1
                ydl_opts = {**base_ydl_opts, "format": selector}
                if cookie_browser:
                    ydl_opts["cookiesfrombrowser"] = (cookie_browser,)
                try:
                    logger.info(
                        "Download attempt for '%s' by '%s' url=%s format=%r cookies=%s",
                        song["title"],
                        song["artist"],
                        target_url,
                        selector,
                        cookie_browser or "none",
                    )
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.download([target_url])
                    self.library.register_file(song["song_id"], str(file_path))
                    logger.info(
                        "Downloaded '%s' by '%s' to %s url=%s format=%r cookies=%s",
                        song["title"],
                        song["artist"],
                        file_path,
                        target_url,
                        selector,
                        cookie_browser or "none",
                    )
                    if not job_context:
                        print(f"\r[{current}/{total}] {display_title} | Done!{' '*15}".ljust(80))
                    self._clear_download_failure(song["song_id"], target_url)
                    return DownloadResult(True)
                except Exception as exc:
                    error_msg = self._clean_ytdlp_error(exc)
                    category = self._categorize_ytdlp_error(error_msg)
                    last_error = error_msg
                    last_category = category
                    logger.warning(
                        "Download attempt failed for '%s' by '%s' url=%s format=%r cookies=%s category=%s error=%s",
                        song["title"],
                        song["artist"],
                        target_url,
                        selector,
                        cookie_browser or "none",
                        category,
                        error_msg,
                    )
                    if category not in {"youtube_format_unavailable", "youtube_access_or_format"}:
                        break

        logger.error(
            "Download failed for '%s' by '%s' url=%s attempts=%s cookies_tried=%s last_category=%s last_error=%s",
            song["title"],
            song["artist"],
            target_url,
            attempts,
            ",".join(mode or "none" for mode in cookie_modes),
            last_category or "unknown",
            last_error or "unknown error",
        )
        if self._should_count_source_failure(last_category, last_error):
            promoted = self._record_download_failure(song, target_url, last_category, last_error, job_context)
            if promoted:
                self._send_permanent_youtube_failure_to_review(song, target_url, last_error)
        if not job_context:
            print(f"\r[{current}/{total}] {display_title} | Failed! ({(last_error or 'unknown')[:40]}...){' '*15}".ljust(80))
        return DownloadResult(False, last_error, last_category)

    def _clean_ytdlp_error(self, exc: Exception) -> str:
        return re.sub(r"\x1b\[[0-9;]*m", "", str(exc).split("\n")[0])

    def _categorize_ytdlp_error(self, error_msg: str | None) -> str:
        text = (error_msg or "").lower()
        if "requested format is not available" in text:
            return "youtube_format_unavailable"
        if "private video" in text or "video unavailable" in text or "has been removed" in text:
            return "youtube_unavailable"
        if "sign in" in text or "confirm" in text or "not a bot" in text or "http error 403" in text or "http error 429" in text:
            return "youtube_access_or_format"
        if "job cancelled" in text:
            return "cancelled"
        return "download_failed"

    def _should_count_source_failure(self, category: str | None, error_msg: str | None) -> bool:
        if category in {"cancelled", "youtube_access_or_format"}:
            return False
        if category in {"youtube_unavailable", "youtube_format_unavailable"}:
            return True
        text = (error_msg or "").lower()
        if category == "download_failed" and (
            "not available" in text
            or "unavailable" in text
            or "has been removed" in text
            or "private video" in text
        ):
            return True
        return False

    def _record_download_failure(
        self,
        song: sqlite3.Row,
        target_url: str,
        category: str | None,
        error_msg: str | None,
        job_context=None,
    ) -> bool:
        threshold = 3
        source_key = self._download_failure_source_key(target_url)
        job_id = getattr(job_context, "job_id", None) or "manual"
        with self.db.get_connection() as conn:
            row = conn.execute(
                """
                SELECT failure_count, last_job_id, promoted_at
                FROM download_failures
                WHERE song_id = ? AND source_key = ?
                """,
                (song["song_id"], source_key),
            ).fetchone()
            now_expr = "datetime('now')"
            if row:
                already_counted = row["last_job_id"] == job_id
                failure_count = int(row["failure_count"] or 0) + (0 if already_counted else 1)
                conn.execute(
                    f"""
                    UPDATE download_failures
                    SET source_url = ?,
                        failure_count = ?,
                        last_failed_at = {now_expr},
                        last_job_id = ?,
                        last_category = ?,
                        last_error = ?
                    WHERE song_id = ? AND source_key = ?
                    """,
                    (target_url, failure_count, job_id, category, error_msg, song["song_id"], source_key),
                )
                was_promoted = bool(row["promoted_at"])
            else:
                failure_count = 1
                was_promoted = False
                conn.execute(
                    f"""
                    INSERT INTO download_failures (
                        song_id, source_key, source_url, failure_count, first_failed_at,
                        last_failed_at, last_job_id, last_category, last_error
                    )
                    VALUES (?, ?, ?, 1, {now_expr}, {now_expr}, ?, ?, ?)
                    """,
                    (song["song_id"], source_key, target_url, job_id, category, error_msg),
                )

            should_promote = failure_count >= threshold and not was_promoted
            if should_promote:
                conn.execute(
                    f"""
                    UPDATE download_failures
                    SET promoted_at = COALESCE(promoted_at, {now_expr})
                    WHERE song_id = ? AND source_key = ?
                    """,
                    (song["song_id"], source_key),
                )
            conn.commit()

        logger.info(
            "Recorded download failure for '%s' by '%s' source=%s count=%s category=%s promoted=%s",
            song["title"],
            song["artist"],
            source_key,
            failure_count,
            category,
            should_promote,
        )
        return should_promote

    def _clear_download_failure(self, song_id: str, target_url: str | None) -> None:
        if not target_url:
            return
        source_key = self._download_failure_source_key(target_url)
        with self.db.get_connection() as conn:
            conn.execute("DELETE FROM download_failures WHERE song_id = ? AND source_key = ?", (song_id, source_key))
            conn.commit()

    def _download_failure_source_key(self, target_url: str | None) -> str:
        youtube_id = self._youtube_id_from_url(target_url)
        if youtube_id:
            return f"youtube:{youtube_id}"
        return f"url:{target_url or ''}"

    def _send_permanent_youtube_failure_to_review(self, song: sqlite3.Row, target_url: str, error_msg: str | None) -> None:
        reason = "Previous YouTube source failed repeatedly."
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO review_queue (song_id, created_at, status, reason, source_url, source_error)
                VALUES (?, datetime('now'), 'PENDING', ?, ?, ?)
                ON CONFLICT(song_id) DO UPDATE SET
                    created_at = excluded.created_at,
                    status = 'PENDING',
                    reason = excluded.reason,
                    source_url = excluded.source_url,
                    source_error = excluded.source_error
                """,
                (song["song_id"], reason, target_url, error_msg),
            )
            conn.execute("UPDATE songs SET status = 'MATCH_FAILED' WHERE song_id = ?", (song["song_id"],))
            conn.commit()
        logger.info(
            "Marked '%s' by '%s' as MATCH_FAILED after repeated source failure url=%s error=%s",
            song["title"],
            song["artist"],
            target_url,
            error_msg,
        )

    def _youtube_id_from_url(self, url: str | None) -> str | None:
        if not url:
            return None
        match = re.search(r"(?:v=|youtu\.be/)([^&?]+)", url)
        return match.group(1) if match else None

    def _acquisition_plugin_for(self, acquisition_info: dict):
        provider = acquisition_info.get("provider")
        for plugin in self.acquisition_plugins:
            if provider and getattr(plugin, "provider", None) != provider:
                continue
            if plugin.can_acquire(acquisition_info):
                return plugin
        if provider:
            logger.error("No acquisition plugin registered for provider: %s", provider)
        return None
