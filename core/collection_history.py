import datetime
import uuid

from infrastructure.database import DatabaseManager


class CollectionHistoryManager:
    def __init__(self, db: DatabaseManager):
        self.db = db

    def recover_draft_revisions(self) -> int:
        return self.commit_all_draft_revisions("recovered_draft")

    def commit_all_draft_revisions(self, reason: str = "app_close") -> int:
        now = self._now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT revision_id FROM collection_revisions WHERE state = 'DRAFT'")
            revision_ids = [row["revision_id"] for row in cursor.fetchall()]
            for revision_id in revision_ids:
                cursor.execute(
                    "UPDATE collection_revisions SET state = 'COMMITTED', reason = ?, committed_at = ? WHERE revision_id = ?",
                    (reason, now, revision_id),
                )
            conn.commit()
            return len(revision_ids)

    def commit_collection_draft(self, collection_id: str, reason: str = "manual_edit") -> bool:
        now = self._now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT revision_id FROM collection_revisions WHERE collection_id = ? AND state = 'DRAFT' ORDER BY started_at DESC LIMIT 1",
                (collection_id,),
            )
            row = cursor.fetchone()
            if not row:
                return False
            cursor.execute(
                "UPDATE collection_revisions SET state = 'COMMITTED', reason = ?, committed_at = ? WHERE revision_id = ?",
                (reason, now, row["revision_id"]),
            )
            conn.commit()
            return True

    def get_collection_history(self, collection_id: str) -> list[dict]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            current_items = self.snapshot_items_for_current(cursor, collection_id)
            cursor.execute(
                """
                SELECT revision_id, state, reason, started_at, committed_at
                FROM collection_revisions
                WHERE collection_id = ? AND state = 'COMMITTED'
                ORDER BY committed_at DESC, started_at DESC
                """,
                (collection_id,),
            )
            revisions = [dict(row) for row in cursor.fetchall()]
            revision_items = {
                revision["revision_id"]: self.snapshot_items_for_revision(cursor, revision["revision_id"])
                for revision in revisions
            }

        rows: list[dict] = []
        latest_items = revision_items.get(revisions[0]["revision_id"], []) if revisions else []
        current_diff = self.diff_item_sets(current_items, latest_items)
        rows.append({
            "revision_id": None,
            "label": "Current",
            "reason": "current",
            "track_count": len(current_items),
            **current_diff,
        })

        for idx, revision in enumerate(revisions):
            older_items = revision_items.get(revisions[idx + 1]["revision_id"], []) if idx + 1 < len(revisions) else []
            current_revision_items = revision_items.get(revision["revision_id"], [])
            diff = self.diff_item_sets(current_revision_items, older_items)
            rows.append({
                **revision,
                "label": revision.get("committed_at") or revision.get("started_at") or "Snapshot",
                "track_count": len(current_revision_items),
                **diff,
            })
        return rows

    def get_collection_revision_tracks(self, collection_id: str, revision_id: str | None = None) -> list[dict]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            if revision_id:
                cursor.execute(
                    """
                    SELECT s.song_id, s.title, s.artist, s.status as song_status,
                           cri.position, 'ACTIVE' as membership_status, NULL as added_at, NULL as archived_at
                    FROM collection_revision_items cri
                    JOIN songs s ON s.song_id = cri.song_id
                    WHERE cri.revision_id = ?
                    ORDER BY cri.position ASC
                    """,
                    (revision_id,),
                )
            else:
                cursor.execute(
                    """
                    SELECT s.song_id, s.title, s.artist, s.status as song_status,
                           cs.position, cs.status as membership_status, cs.added_at, cs.archived_at
                    FROM collection_songs cs
                    JOIN songs s ON s.song_id = cs.song_id
                    WHERE cs.collection_id = ? AND cs.status = 'ACTIVE'
                    ORDER BY cs.position ASC
                    """,
                    (collection_id,),
                )
            return [dict(row) for row in cursor.fetchall()]

    def create_committed_revision(self, cursor, collection_id: str, reason: str, now: str | None = None) -> str:
        now = now or self._now()
        revision_id = str(uuid.uuid4())
        base_revision_id = self.latest_committed_revision_id(cursor, collection_id)
        cursor.execute(
            """
            INSERT INTO collection_revisions (revision_id, collection_id, state, reason, started_at, committed_at, base_revision_id)
            VALUES (?, ?, 'COMMITTED', ?, ?, ?, ?)
            """,
            (revision_id, collection_id, reason, now, now, base_revision_id),
        )
        self.write_revision_items(cursor, revision_id, collection_id)
        return revision_id

    def sync_draft_revision(self, cursor, collection_id: str, reason: str, now: str | None = None) -> str:
        now = now or self._now()
        cursor.execute(
            "SELECT revision_id FROM collection_revisions WHERE collection_id = ? AND state = 'DRAFT' ORDER BY started_at DESC LIMIT 1",
            (collection_id,),
        )
        row = cursor.fetchone()
        if row:
            revision_id = row["revision_id"]
        else:
            revision_id = str(uuid.uuid4())
            base_revision_id = self.latest_committed_revision_id(cursor, collection_id)
            cursor.execute(
                """
                INSERT INTO collection_revisions (revision_id, collection_id, state, reason, started_at, base_revision_id)
                VALUES (?, ?, 'DRAFT', ?, ?, ?)
                """,
                (revision_id, collection_id, reason, now, base_revision_id),
            )
        cursor.execute("DELETE FROM collection_revision_items WHERE revision_id = ?", (revision_id,))
        self.write_revision_items(cursor, revision_id, collection_id)
        return revision_id

    def latest_committed_revision_id(self, cursor, collection_id: str) -> str | None:
        cursor.execute(
            """
            SELECT revision_id FROM collection_revisions
            WHERE collection_id = ? AND state = 'COMMITTED'
            ORDER BY committed_at DESC, started_at DESC
            LIMIT 1
            """,
            (collection_id,),
        )
        row = cursor.fetchone()
        return row["revision_id"] if row else None

    def write_revision_items(self, cursor, revision_id: str, collection_id: str) -> None:
        cursor.execute(
            """
            INSERT INTO collection_revision_items (revision_id, song_id, position)
            SELECT ?, song_id, position
            FROM collection_songs
            WHERE collection_id = ? AND status = 'ACTIVE'
            ORDER BY position ASC
            """,
            (revision_id, collection_id),
        )

    def snapshot_items_for_current(self, cursor, collection_id: str) -> list[tuple[str, int]]:
        cursor.execute(
            "SELECT song_id, position FROM collection_songs WHERE collection_id = ? AND status = 'ACTIVE' ORDER BY position ASC",
            (collection_id,),
        )
        return [(row["song_id"], row["position"] or 0) for row in cursor.fetchall()]

    def snapshot_items_for_revision(self, cursor, revision_id: str) -> list[tuple[str, int]]:
        cursor.execute(
            "SELECT song_id, position FROM collection_revision_items WHERE revision_id = ? ORDER BY position ASC",
            (revision_id,),
        )
        return [(row["song_id"], row["position"] or 0) for row in cursor.fetchall()]

    def diff_item_sets(self, newer: list[tuple[str, int]], older: list[tuple[str, int]]) -> dict:
        newer_ids = {song_id for song_id, _position in newer}
        older_ids = {song_id for song_id, _position in older}
        return {
            "added_count": len(newer_ids - older_ids),
            "removed_count": len(older_ids - newer_ids),
        }

    def _now(self) -> str:
        return datetime.datetime.now(datetime.timezone.utc).isoformat()
