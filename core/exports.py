import logging
import sqlite3
import uuid
import json
import datetime
import shutil
import os
import re
import random
from pathlib import Path
from typing import Optional, List

from infrastructure.database import DatabaseManager
from infrastructure.config import ConfigManager

logger = logging.getLogger(__name__)

class ExportManager:
    def __init__(self, config: ConfigManager, db: DatabaseManager):
        self.config = config
        self.db = db
        raw_roots = self.config.get("exports.roots", ["./exports_root"])
        if isinstance(raw_roots, str): raw_roots = [raw_roots]
        self.export_roots = [Path(p).resolve() for p in raw_roots if p]

    def create_export(self, collection_id: str, target_path: str, mode: str = 'STATIC', job_context=None) -> Optional[str]:
        export_dir = Path(target_path).resolve()
        export_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        if mode == 'MANAGED':
            if job_context and job_context.is_cancelled:
                logger.info("Managed export creation cancelled cooperatively.")
                return None
            export_id = str(uuid.uuid4())
            self._write_manifest(export_dir, export_id, collection_id, "", 0)
            with self.db.get_connection() as conn:
                conn.execute("""
                    INSERT INTO exports (export_id, collection_id, target_path, mode, last_updated, last_operation, shuffle_limit)
                    VALUES (?, ?, ?, 'MANAGED', ?, 'create', NULL)
                """, (export_id, collection_id, str(export_dir), now))
                conn.commit()
            logger.info(f"Created empty managed export at {export_dir}")
            return export_id
        
        tracks = self._exportable_tracks(collection_id)

        if not tracks:
            logger.warning("No downloaded tracks available to export for this collection.")
            return None

        copied_count = 0
        for i, track in enumerate(tracks, 1):
            if job_context and job_context.is_cancelled:
                logger.info("Export cancelled cooperatively.")
                break
            if job_context:
                job_context.wait_if_paused()
                
            if job_context:
                job_context.update_progress(i, len(tracks))
                job_context.update_current_item(f"{track['artist']} - {track['title']}")

            src_file = Path(track['filepath'])
            if not src_file.exists(): continue
                
            safe_title = re.sub(r'[<>:"/\\|?*]', '', track['title']).strip()
            filename = f"{track['position']:03d} - {safe_title}.mp3"
            dst_file = export_dir / filename
            
            if not dst_file.exists():
                shutil.copy2(src_file, dst_file)
                copied_count += 1
                
        logger.info(f"Exported {copied_count} tracks to {export_dir}")
            
        return None

    def update_managed_export(self, export_id: str, job_context=None):
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM exports WHERE export_id = ?", (export_id,))
            export_record = cursor.fetchone()
            
        if not export_record:
            raise ValueError(f"Managed export {export_id} was not found.")

        if export_record['mode'] != 'MANAGED':
            raise ValueError(f"Export {export_id} is not a managed export.")

        target_dir = Path(export_record['target_path'])
        collection_id = export_record['collection_id']

        if not target_dir.exists():
            raise FileNotFoundError(f"Managed export folder does not exist: {target_dir}")

        for file in target_dir.glob("*.mp3"):
            file.unlink()
            
        self.create_export(collection_id, str(target_dir), mode='STATIC', job_context=job_context)
        
        if not (job_context and job_context.is_cancelled):
            tracks = self._exportable_tracks(collection_id)
            self._write_manifest(target_dir, export_id, collection_id, self._track_signature(tracks), len(tracks))
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            with self.db.get_connection() as conn:
                conn.execute(
                    "UPDATE exports SET last_updated = ?, last_operation = 'update', shuffle_limit = NULL WHERE export_id = ?",
                    (now, export_id),
                )
                conn.commit()

    def shuffle_managed_export(self, export_id: str, limit: int | None = None, job_context=None):
        export_record = self._get_managed_export_record(export_id)
        target_dir = Path(export_record['target_path'])
        collection_id = export_record['collection_id']

        if not target_dir.exists():
            raise FileNotFoundError(f"Managed export folder does not exist: {target_dir}")

        tracks = [dict(track) for track in self._exportable_tracks(collection_id)]
        if not tracks:
            logger.warning("No downloaded tracks available to shuffle for this export.")
            return

        if limit is not None:
            limit = max(1, min(int(limit), len(tracks)))
            tracks = random.sample(tracks, limit)
        random.shuffle(tracks)

        for file in target_dir.glob("*.mp3"):
            file.unlink()

        copied_count = self._copy_tracks_to_directory(tracks, target_dir, job_context=job_context)

        if not (job_context and job_context.is_cancelled):
            self._write_manifest(target_dir, export_id, collection_id, self._track_signature(tracks), len(tracks))
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            with self.db.get_connection() as conn:
                conn.execute(
                    """
                    UPDATE exports
                    SET last_updated = ?, last_operation = 'shuffle', shuffle_limit = ?
                    WHERE export_id = ?
                    """,
                    (now, limit, export_id),
                )
                conn.commit()
        logger.info(f"Shuffled {copied_count} tracks to {target_dir}")

    def clear_managed_export(self, export_id: str, job_context=None) -> int:
        export_record = self._get_managed_export_record(export_id)
        target_dir = Path(export_record['target_path'])
        collection_id = export_record['collection_id']

        if not target_dir.exists():
            raise FileNotFoundError(f"Managed export folder does not exist: {target_dir}")

        removed_count = 0
        files = list(target_dir.glob("*.mp3"))
        total = len(files)
        for i, file in enumerate(files, 1):
            if job_context and job_context.is_cancelled:
                logger.info("Export clear cancelled cooperatively.")
                break
            if job_context:
                job_context.wait_if_paused()
                job_context.update_progress(i, total)
                job_context.update_current_item(file.name)
            file.unlink()
            removed_count += 1

        if not (job_context and job_context.is_cancelled):
            self._write_manifest(target_dir, export_id, collection_id, "", 0)
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            with self.db.get_connection() as conn:
                conn.execute(
                    """
                    UPDATE exports
                    SET last_updated = ?, last_operation = 'clear', shuffle_limit = NULL
                    WHERE export_id = ?
                    """,
                    (now, export_id),
                )
                conn.commit()
        logger.info(f"Cleared {removed_count} tracks from {target_dir}")
        return removed_count

    def _get_managed_export_record(self, export_id: str):
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM exports WHERE export_id = ?", (export_id,))
            export_record = cursor.fetchone()

        if not export_record:
            raise ValueError(f"Managed export {export_id} was not found.")

        if export_record['mode'] != 'MANAGED':
            raise ValueError(f"Export {export_id} is not a managed export.")

        return export_record

    def export_preflight(self, collection_id: str) -> dict:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(DISTINCT s.song_id) as total
                FROM collection_songs cs
                JOIN songs s ON cs.song_id = s.song_id
                WHERE cs.collection_id = ?
                  AND cs.status = 'ACTIVE'
                  AND s.status != 'ARCHIVED'
            """, (collection_id,))
            total = int(cursor.fetchone()["total"] or 0)
            cursor.execute("""
                SELECT COUNT(DISTINCT s.song_id) as downloaded
                FROM collection_songs cs
                JOIN songs s ON cs.song_id = s.song_id
                JOIN files f ON s.song_id = f.song_id
                WHERE cs.collection_id = ?
                  AND cs.status = 'ACTIVE'
                  AND s.status != 'ARCHIVED'
                  AND f.downloaded = 1
            """, (collection_id,))
            downloaded = int(cursor.fetchone()["downloaded"] or 0)
        return {
            "total": total,
            "downloaded": downloaded,
            "missing": max(0, total - downloaded),
        }

    def delete_managed_export(self, export_id: str, delete_folder: bool = False, job_context=None):
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT target_path FROM exports WHERE export_id = ?", (export_id,))
            record = cursor.fetchone()
            
        if not record: return
        target_dir = Path(record['target_path'])

        if job_context:
            job_context.update_progress(0, 1)
            job_context.update_current_item(str(target_dir))
            job_context.wait_if_paused()
        if job_context and job_context.is_cancelled:
            return

        with self.db.get_connection() as conn:
            conn.execute("DELETE FROM exports WHERE export_id = ?", (export_id,))
            conn.commit()

        if not target_dir.exists(): return

        if delete_folder:
            shutil.rmtree(target_dir, ignore_errors=True)
        else:
            manifest_path = target_dir / ".musicsync.json"
            if manifest_path.exists():
                self._clear_windows_file_attributes(manifest_path)
                manifest_path.unlink(missing_ok=True)
        if job_context:
            job_context.update_progress(1, 1)

    def scan_and_recover(self, job_context=None):
        found_count = 0
        
        # First gather all manifests
        manifests = []
        for root in self.export_roots:
            if not root.exists(): continue
            for manifest_path in root.rglob(".musicsync.json"):
                if '$Recycle.Bin' in manifest_path.parts: continue
                manifests.append(manifest_path)
                
        # Then process them (allows for progress tracking)
        for i, manifest_path in enumerate(manifests, 1):
            if job_context and job_context.is_cancelled: break
            if job_context:
                job_context.wait_if_paused()
            if job_context:
                job_context.update_progress(i, len(manifests))
                job_context.update_current_item(f"Checking {manifest_path.parent.name}")
                
            try:
                with open(manifest_path, 'r') as f: data = json.load(f)
                export_id = data.get("export_id")
                current_actual_path = str(manifest_path.parent.resolve())
                
                if export_id:
                    with self.db.get_connection() as conn:
                        conn.execute("UPDATE exports SET target_path = ? WHERE export_id = ?", (current_actual_path, export_id))
                        conn.commit()
                    found_count += 1
            except Exception as e:
                logger.error(f"Failed to read manifest {manifest_path}: {e}")

    def get_managed_exports(self) -> List[dict]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT e.export_id, e.collection_id, e.target_path, e.last_updated,
                       COALESCE(e.last_operation, 'update') as last_operation,
                       e.shuffle_limit,
                       c.name as collection_name,
                       c.updated_at as collection_updated_at,
                       cs.last_sync as collection_last_sync,
                       (
                           SELECT GROUP_CONCAT(song_id || ':' || position, '|')
                           FROM (
                               SELECT cs2.song_id as song_id, cs2.position as position
                               FROM collection_songs cs2
                               JOIN songs s2 ON cs2.song_id = s2.song_id
                               JOIN files f2 ON s2.song_id = f2.song_id
                               WHERE cs2.collection_id = e.collection_id
                                 AND cs2.status = 'ACTIVE'
                                 AND s2.status != 'ARCHIVED'
                                 AND f2.downloaded = 1
                               ORDER BY cs2.position ASC
                           )
                       ) as current_track_signature
                FROM exports e
                JOIN collections c ON e.collection_id = c.collection_id
                LEFT JOIN collection_sources cs ON e.collection_id = cs.collection_id
                WHERE e.mode = 'MANAGED'
            """)
            return [dict(row) for row in cursor.fetchall()]

    def _exportable_tracks(self, collection_id: str):
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT s.song_id, s.title, s.artist, cs.position, f.filepath
                FROM collection_songs cs
                JOIN songs s ON cs.song_id = s.song_id
                JOIN files f ON s.song_id = f.song_id
                WHERE cs.collection_id = ?
                  AND cs.status = 'ACTIVE'
                  AND s.status != 'ARCHIVED'
                  AND f.downloaded = 1
                ORDER BY cs.position ASC
            """, (collection_id,))
            return cursor.fetchall()

    def _track_signature(self, tracks) -> str:
        return "|".join(f"{track['song_id']}:{track['position']}" for track in tracks)

    def _copy_tracks_to_directory(self, tracks, export_dir: Path, job_context=None) -> int:
        copied_count = 0
        total = len(tracks)
        for i, track in enumerate(tracks, 1):
            if job_context and job_context.is_cancelled:
                logger.info("Export cancelled cooperatively.")
                break
            if job_context:
                job_context.wait_if_paused()
                job_context.update_progress(i, total)
                job_context.update_current_item(f"{track['artist']} - {track['title']}")

            src_file = Path(track['filepath'])
            if not src_file.exists():
                continue

            safe_title = re.sub(r'[<>:"/\\|?*]', '', track['title']).strip()
            filename = f"{i:03d} - {safe_title}.mp3"
            dst_file = export_dir / filename

            if not dst_file.exists():
                shutil.copy2(src_file, dst_file)
                copied_count += 1
        return copied_count

    def _write_manifest(self, directory: Path, export_id: str, collection_id: str, track_signature: str | None = None, track_count: int | None = None):
        manifest_data = {"version": 2, "export_id": export_id, "collection_id": collection_id}
        if track_signature is not None:
            manifest_data["track_signature"] = track_signature
        if track_count is not None:
            manifest_data["track_count"] = track_count
        manifest_path = directory / ".musicsync.json"
        temp_path = directory / f".musicsync.{uuid.uuid4().hex}.tmp"
        with open(temp_path, 'w') as f:
            json.dump(manifest_data, f, indent=4)
        try:
            os.replace(temp_path, manifest_path)
        except PermissionError:
            self._clear_windows_file_attributes(manifest_path)
            try:
                os.replace(temp_path, manifest_path)
            except PermissionError:
                manifest_path.unlink(missing_ok=True)
                os.replace(temp_path, manifest_path)
        finally:
            temp_path.unlink(missing_ok=True)
        if os.name == 'nt':
            self._set_windows_file_attributes(manifest_path, 0x02)

    def _clear_windows_file_attributes(self, path: Path) -> None:
        if os.name == 'nt' and path.exists():
            self._set_windows_file_attributes(path, 0x80)

    def _set_windows_file_attributes(self, path: Path, attributes: int) -> None:
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(str(path), attributes)

    def export_media(self, media_items: List[dict], target_path: str, job_context=None) -> int:
        """Exports an arbitrary result set of media items to a folder (STATIC ONLY)."""
        export_dir = Path(target_path).resolve()
        export_dir.mkdir(parents=True, exist_ok=True)
        
        copied_count = 0
        for i, track in enumerate(media_items, 1):
            if str(track.get('status') or track.get('song_status') or '').upper() == 'ARCHIVED':
                continue
            if job_context and job_context.is_cancelled: break
            if job_context:
                job_context.wait_if_paused()
                
            if job_context:
                job_context.update_progress(i, len(media_items))
                job_context.update_current_item(f"{track['artist']} - {track['title']}")

            if not track.get('filepath'): continue
            src_file = Path(track['filepath'])
            if not src_file.exists(): continue
                
            safe_title = re.sub(r'[<>:"/\\|?*]', '', track['title']).strip()
            # For arbitrary exports, we pad with zeros since there is no playlist 'position'
            filename = f"{i:03d} - {safe_title}.mp3" 
            dst_file = export_dir / filename
            
            if not dst_file.exists():
                shutil.copy2(src_file, dst_file)
                copied_count += 1
                
        logger.info(f"Exported {copied_count} arbitrary tracks to {export_dir}")
        return copied_count
