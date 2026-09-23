import logging
import json
import re
from infrastructure.database import DatabaseManager

logger = logging.getLogger(__name__)

class ReviewManager:
    def __init__(self, db: DatabaseManager):
        self.db = db

    def get_match_candidates(self, song_id: str) -> list[dict]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT match_id, youtube_id, yt_title, yt_duration, confidence
                FROM matches
                WHERE song_id = ? AND approved = 0
                ORDER BY confidence DESC
            """, (song_id,))
            return self._dedupe_candidates([dict(row) for row in cursor.fetchall()])

    def get_review_context(self, song_id: str) -> dict:
        with self.db.get_connection() as conn:
            row = conn.execute(
                """
                SELECT reason, source_url, source_error
                FROM review_queue
                WHERE song_id = ? AND status = 'PENDING'
                """,
                (song_id,),
            ).fetchone()
            return dict(row) if row else {}

    def approve_match(self, song_id: str, match_id: int) -> None:
        self._approve_match(song_id, match_id)

    def apply_manual_url(self, song_id: str, url: str) -> None:
        self._apply_manual_url(song_id, url)

    def review_media(self, media_items: list[dict]):
        """Processes a provided list of items requiring human review."""
        if not media_items:
            print("\n✅ No items currently require review.")
            return

        print(f"\n--- Reviewing {len(media_items)} Items ---")
        for i, item in enumerate(media_items, 1):
            print(f"\n[{i}/{len(media_items)}] Reviewing: {item['title']} - {item['artist']}")
            
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                # FIX: Updated column names to match the database schema
                cursor.execute("""
                    SELECT match_id, youtube_id, yt_title, confidence 
                    FROM matches 
                    WHERE song_id = ? AND approved = 0
                    ORDER BY confidence DESC
                """, (item['song_id'],))
                candidates = self._dedupe_candidates([dict(row) for row in cursor.fetchall()])

            if not candidates:
                print("  (No automatic match candidates found)")
                self._print_review_context(item['song_id'])
                self._handle_no_candidates(item)
                continue

            self._print_review_context(item['song_id'])

            for j, cand in enumerate(candidates, 1):
                # FIX: Updated dictionary keys
                score = cand['confidence'] * 100
                print(f"  [{j}] {cand['yt_title']} (Confidence: {score:.1f}%)")
            
            print("  [s] Skip for now")
            print("  [m] Enter manual URL")
            print("  [r] Reject all candidates (Reset to DISCOVERED)")
            print("  [q] Quit Review Queue")

            while True:
                choice = input("\nSelect candidate number or action: ").strip().lower()
                
                if choice == 'q':
                    print("\nExiting review queue...")
                    return
                elif choice == 's':
                    print("Skipped. (Remains in REVIEW queue)")
                    break
                elif choice == 'r':
                    self._update_song_status(item['song_id'], 'DISCOVERED')
                    print("Rejected all. Status reset to DISCOVERED.")
                    break
                elif choice == 'm':
                    url = input("Enter direct YouTube URL: ").strip()
                    if url:
                        self._apply_manual_url(item['song_id'], url)
                        print("✅ Manual URL applied. Status set to MATCHED.")
                    break
                elif choice.isdigit() and 1 <= int(choice) <= len(candidates):
                    selected = candidates[int(choice)-1]
                    self._approve_match(item['song_id'], selected['match_id'])
                    print(f"✅ Approved: {selected['yt_title']}")
                    break
                else:
                    print("❌ Invalid selection.")

        print("\n--- Review Queue Complete ---")

    def _approve_match(self, song_id: str, match_id: int):
        with self.db.get_connection() as conn:
            conn.execute("UPDATE matches SET approved = 0 WHERE song_id = ?", (song_id,))
            conn.execute("UPDATE matches SET approved = 1 WHERE match_id = ?", (match_id,))
            conn.execute("UPDATE songs SET status = 'MATCHED' WHERE song_id = ?", (song_id,))
            conn.execute("UPDATE review_queue SET status = 'RESOLVED', reason = NULL, source_url = NULL, source_error = NULL WHERE song_id = ?", (song_id,))
            conn.execute("DELETE FROM download_failures WHERE song_id = ?", (song_id,))
            conn.commit()

    def _update_song_status(self, song_id: str, status: str):
        with self.db.get_connection() as conn:
            conn.execute("UPDATE songs SET status = ? WHERE song_id = ?", (status, song_id))
            if status != "REVIEW":
                conn.execute("UPDATE review_queue SET status = 'RESOLVED' WHERE song_id = ?", (song_id,))
            conn.commit()

    def _apply_manual_url(self, song_id: str, url: str):
        yt_match = re.search(r'(?:v=|youtu\.be/)([^&?]+)', url)
        yt_id = yt_match.group(1) if yt_match else None
        
        acq_info = json.dumps({"direct_url": url, "youtube_id": yt_id})
        
        with self.db.get_connection() as conn:
            conn.execute("UPDATE songs SET status = 'MATCHED', acquisition_info = ? WHERE song_id = ?", (acq_info, song_id))
            conn.execute("UPDATE matches SET approved = 0 WHERE song_id = ?", (song_id,))
            conn.execute("UPDATE review_queue SET status = 'RESOLVED', reason = NULL, source_url = NULL, source_error = NULL WHERE song_id = ?", (song_id,))
            conn.execute("DELETE FROM download_failures WHERE song_id = ?", (song_id,))
            conn.commit()

    def _print_review_context(self, song_id: str) -> None:
        context = self.get_review_context(song_id)
        if not context:
            return
        reason = context.get("reason")
        source_url = context.get("source_url")
        source_error = context.get("source_error")
        if reason:
            print(f"  Note: {reason}")
        if source_url:
            print(f"  Old source: {source_url}")
        if source_error:
            print(f"  Error: {source_error}")

    def _dedupe_candidates(self, candidates: list[dict]) -> list[dict]:
        seen: set[str] = set()
        unique: list[dict] = []
        for candidate in candidates:
            key = self._candidate_key(candidate)
            if key in seen:
                continue
            seen.add(key)
            unique.append(candidate)
        return unique

    def _candidate_key(self, candidate: dict) -> str:
        youtube_id = str(candidate.get("youtube_id") or "").strip()
        if youtube_id:
            return f"id:{youtube_id}"
        title = " ".join(str(candidate.get("yt_title") or candidate.get("title") or "").lower().split())
        duration = str(candidate.get("yt_duration") or "").strip()
        return f"title:{title}|duration:{duration}"

    def _handle_no_candidates(self, item: dict):
        print("  [s] Skip for now")
        print("  [m] Enter manual URL")
        print("  [q] Quit Review Queue")
        
        while True:
            choice = input("\nSelect action: ").strip().lower()
            if choice == 'q': return
            elif choice == 's':
                print("Skipped.")
                break
            elif choice == 'm':
                url = input("Enter direct YouTube URL: ").strip()
                if url:
                    self._apply_manual_url(item['song_id'], url)
                    print("✅ Manual URL applied. Status set to MATCHED.")
                break
            else:
                print("❌ Invalid selection.")
