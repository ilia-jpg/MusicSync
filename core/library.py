import logging
from pathlib import Path
import re

from infrastructure.config import ConfigManager
from infrastructure.database import DatabaseManager

logger = logging.getLogger(__name__)

class LibraryManager:
    def __init__(self, config: ConfigManager, db: DatabaseManager):
        self.config = config
        self.db = db
        self.library_root = Path(self.config.get("library_root", "./library")).resolve()

    def _sanitize_filename(self, name: str) -> str:
        """Removes illegal OS characters from strings."""
        if not name: return "Unknown"
        return re.sub(r'[<>:"/\\|?*]', '', str(name)).strip()

    def get_expected_path(self, media_type: str, artist: str, title: str) -> Path:
        """Calculates exactly where a file should live in the physical library."""
        artist_clean = self._sanitize_filename(artist.split(',')[0])
        title_clean = self._sanitize_filename(title)
        
        folder_type = "Podcasts" if media_type == 'podcast' else "Audiobooks" if media_type == 'audiobook' else "Music"
        target_dir = self.library_root / folder_type / artist_clean
        target_dir.mkdir(parents=True, exist_ok=True)
        
        return target_dir / f"{title_clean}.mp3"

    def register_file(self, song_id: str, filepath: str):
        """Records a successfully acquired physical file in the database."""
        with self.db.get_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO files (song_id, filepath, downloaded, synced) VALUES (?, ?, 1, 0)", (song_id, filepath))
            conn.execute("UPDATE songs SET status = 'DOWNLOADED' WHERE song_id = ? AND status != 'ARCHIVED'", (song_id,))
            conn.execute("DELETE FROM download_failures WHERE song_id = ?", (song_id,))
            conn.commit()

    def verify_library(self, job_context=None):
        """Checks if downloaded files still exist on disk. Resets missing files to MATCHED."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            # Fetch all files the database THINKS are downloaded
            cursor.execute("""
                SELECT f.song_id, f.filepath, s.title 
                FROM files f
                JOIN songs s ON f.song_id = s.song_id
            """)
            records = cursor.fetchall()
            
        missing_count = 0
        for i, rec in enumerate(records, 1):
            if job_context and job_context.is_cancelled:
                break
            if job_context:
                job_context.wait_if_paused()
                
            if job_context:
                job_context.update_progress(i, len(records))
                job_context.update_current_item(f"Checking {rec['title']}")
                
            path = Path(rec['filepath'])
            if not path.exists():
                missing_count += 1
                with self.db.get_connection() as conn:
                    # Remove the file record and revert the song status to MATCHED
                    conn.execute("DELETE FROM files WHERE song_id = ?", (rec['song_id'],))
                    conn.execute("UPDATE songs SET status = 'MATCHED' WHERE song_id = ? AND status != 'ARCHIVED'", (rec['song_id'],))
                    conn.commit()
        
        # We print here just in case you ever run it synchronously, but the background job will suppress it
        if not job_context:
            print(f"Library Verification Complete. Found {missing_count} missing files.")
