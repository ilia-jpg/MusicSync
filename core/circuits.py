import datetime
import json
import logging
import os
import random
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from core.collections import CollectionManager
from core.models import MediaQuery
from infrastructure.database import DatabaseManager

logger = logging.getLogger(__name__)


class CircuitManager:
    SLOT_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    SLOT_WIDTH = 3

    def __init__(self, db: DatabaseManager, collection_manager: CollectionManager):
        self.db = db
        self.collection_manager = collection_manager

    def create_circuit(
        self,
        name: str,
        target_path: str,
        source_config: dict[str, Any],
        fill_policy: dict[str, Any] | None = None,
    ) -> str:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Circuit name cannot be empty.")
        target_dir = Path(target_path).expanduser().resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        now = self._now()
        circuit_id = str(uuid.uuid4())
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO circuits (
                    circuit_id, name, target_path, source_config_json, fill_policy_json,
                    status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
                """,
                (
                    circuit_id,
                    clean_name,
                    str(target_dir),
                    json.dumps(source_config, sort_keys=True),
                    json.dumps(fill_policy or {"mode": "shuffle", "target_count": 25}, sort_keys=True),
                    now,
                    now,
                ),
            )
            conn.commit()
        self._write_manifest(target_dir, circuit_id, None, [], operation="create")
        return circuit_id

    def update_circuit(
        self,
        circuit_id: str,
        *,
        name: str | None = None,
        source_config: dict[str, Any] | None = None,
        target_count: int | None = None,
    ) -> None:
        circuit = self.get_circuit(circuit_id)
        if not circuit:
            raise ValueError("Circuit was not found.")
        updated_name = (name or circuit["name"]).strip()
        if not updated_name:
            raise ValueError("Circuit name cannot be empty.")
        updated_source = source_config or circuit.get("source_config") or {"type": "all_downloaded"}
        policy = circuit.get("fill_policy") or {}
        if target_count is not None:
            policy["target_count"] = max(1, int(target_count))
            policy["limit"] = policy["target_count"]
        now = self._now()
        with self.db.get_connection() as conn:
            conn.execute(
                """
                UPDATE circuits
                SET name = ?, source_config_json = ?, fill_policy_json = ?, updated_at = ?
                WHERE circuit_id = ?
                """,
                (
                    updated_name,
                    json.dumps(updated_source, sort_keys=True),
                    json.dumps(policy, sort_keys=True),
                    now,
                    circuit_id,
                ),
            )
            conn.commit()

    def plan_circulation(
        self,
        circuit_id: str,
        *,
        marker_position: int | None = None,
        use_marker_override: bool = False,
        include_add_items: bool = True,
        feedback_changes: dict | None = None,
    ) -> dict:
        circuit = self.get_circuit(circuit_id)
        if not circuit:
            raise ValueError("Circuit was not found.")
        target_dir = Path(circuit["target_path"])
        send = self._latest_send(circuit_id)
        previous_items = self._send_items(send["send_id"]) if send else []
        missing_items = [item for item in previous_items if not (target_dir / item["filename"]).exists()]
        detected_marker = max(missing_items, key=lambda item: int(item["position"])) if missing_items else None
        if use_marker_override:
            override_position = max(0, int(marker_position or 0))
            stop_marker = next(
                (item for item in previous_items if int(item["position"]) == override_position),
                None,
            )
        else:
            stop_marker = detected_marker
        stop_position = int(stop_marker["position"]) if stop_marker else 0
        remove_items = [
            item for item in previous_items
            if stop_position and int(item["position"]) <= stop_position and (target_dir / item["filename"]).exists()
        ]
        keep_items = [
            item for item in previous_items
            if (not stop_position or int(item["position"]) > stop_position) and (target_dir / item["filename"]).exists()
        ]
        target_count = self._target_count(circuit)
        add_count = max(0, target_count - len(keep_items))
        keep_ids = {item["song_id"] for item in keep_items}
        heard_ids = {
            item["song_id"]
            for item in previous_items
            if stop_position and int(item["position"]) <= stop_position
        }
        candidates = [
            track for track in self._source_tracks(circuit.get("source_config") or {})
            if track.get("song_id") not in keep_ids and track.get("song_id") not in heard_ids
        ]
        existing_filenames = {item["filename"] for item in keep_items}
        max_position = max([int(item["position"]) for item in previous_items] or [0])
        if include_add_items:
            add_items = self._select_add_items(
                circuit,
                candidates,
                add_count,
                existing_filenames,
                max_position,
                feedback_changes=feedback_changes,
            )
            effective_add_count = len(add_items)
        else:
            effective_add_count = min(add_count, self._eligible_refill_count(circuit, candidates, feedback_changes or {}))
            add_items = []
        feedback = self.get_feedback_state(circuit_id, [item["song_id"] for item in previous_items])
        base_feedback = {song_id: dict(values) for song_id, values in feedback.items()}
        feedback_changes = {song_id: dict(scopes) for song_id, scopes in (feedback_changes or {}).items()}
        return {
            "circuit_id": circuit_id,
            "target_count": target_count,
            "current_count": len([item for item in previous_items if (target_dir / item["filename"]).exists()]),
            "previous_items": previous_items,
            "detected_marker": detected_marker,
            "stop_marker": stop_marker,
            "marker_position": stop_position,
            "marker_override": use_marker_override,
            "remove_items": remove_items,
            "keep_items": keep_items,
            "add_items": add_items,
            "add_count": effective_add_count,
            "missing_count": len(missing_items),
            "feedback_scope": "global",
            "feedback": feedback,
            "base_feedback": base_feedback,
            "feedback_changes": feedback_changes,
        }

    def circulate_circuit(self, circuit_id: str, plan: dict | None = None, job_context=None) -> str | None:
        circuit = self.get_circuit(circuit_id)
        if not circuit:
            raise ValueError("Circuit was not found.")
        target_dir = Path(circuit["target_path"])
        target_dir.mkdir(parents=True, exist_ok=True)
        plan = plan or self.plan_circulation(circuit_id)
        if plan.get("add_count") and not plan.get("add_items"):
            plan = self.plan_circulation(
                circuit_id,
                marker_position=plan.get("marker_position"),
                use_marker_override=bool(plan.get("marker_override")),
                include_add_items=True,
                feedback_changes=plan.get("feedback_changes") or {},
            )
        remove_items = plan.get("remove_items") or []
        keep_items = plan.get("keep_items") or []
        add_items = plan.get("add_items") or []
        feedback_changes = plan.get("feedback_changes") or {}
        has_feedback = bool(feedback_changes)
        if not remove_items and not add_items and not has_feedback:
            logger.info("Circuit %s is already at target with no progress marker; no writes needed.", circuit_id)
            return None
        if not remove_items and not add_items and has_feedback:
            now = self._now()
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                self._apply_feedback(cursor, circuit_id, feedback_changes, source="confirmation", event_at=now)
                cursor.execute(
                    "UPDATE circuits SET last_scanned_at = ?, updated_at = ? WHERE circuit_id = ?",
                    (now, now, circuit_id),
                )
                conn.commit()
            return None
        total = len(remove_items) + len(add_items)
        completed = 0
        for item in remove_items:
            if job_context and job_context.is_cancelled:
                return None
            completed += 1
            if job_context:
                job_context.wait_if_paused()
                job_context.update_progress(completed, max(1, total))
                job_context.update_current_item(item["filename"])
            (target_dir / item["filename"]).unlink(missing_ok=True)
        for item in add_items:
            if job_context and job_context.is_cancelled:
                return None
            completed += 1
            if job_context:
                job_context.wait_if_paused()
                job_context.update_progress(completed, max(1, total))
                job_context.update_current_item(f"{item.get('artist', '')} - {item.get('title', '')}")
            src_file = Path(item["filepath"])
            if src_file.exists():
                shutil.copy2(src_file, target_dir / item["filename"])
        send_id = str(uuid.uuid4())
        sent_at = self._now()
        manifest_items = sorted([*keep_items, *add_items], key=lambda item: int(item["position"]))
        self._write_manifest(target_dir, circuit_id, send_id, manifest_items, operation="circulate")
        stop_marker = plan.get("stop_marker")
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO circuit_sends (
                    send_id, circuit_id, sent_at, operation, target_path, manifest_path, track_count,
                    stop_marker_song_id, stop_marker_position, scanned_at
                )
                VALUES (?, ?, ?, 'circulate', ?, ?, ?, ?, ?, ?)
                """,
                (
                    send_id,
                    circuit_id,
                    sent_at,
                    str(target_dir),
                    str(target_dir / ".musiccircuit.json"),
                    len(manifest_items),
                    stop_marker.get("song_id") if stop_marker else None,
                    stop_marker.get("position") if stop_marker else None,
                    sent_at,
                ),
            )
            for item in manifest_items:
                state = "kept" if item in keep_items else "sent"
                cursor.execute(
                    """
                    INSERT INTO circuit_send_items (send_id, song_id, position, filename, returned_state)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (send_id, item["song_id"], item["position"], item["filename"], state),
                )
            for item in add_items:
                cursor.execute(
                    """
                    INSERT INTO circuit_song_state (circuit_id, song_id, last_sent_at, sent_count)
                    VALUES (?, ?, ?, 1)
                    ON CONFLICT(circuit_id, song_id) DO UPDATE SET
                        last_sent_at = excluded.last_sent_at,
                        sent_count = COALESCE(circuit_song_state.sent_count, 0) + 1
                    """,
                    (circuit_id, item["song_id"], sent_at),
                )
            for item in remove_items:
                cursor.execute(
                    """
                    INSERT INTO circuit_song_state (circuit_id, song_id, last_heard_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(circuit_id, song_id) DO UPDATE SET
                        last_heard_at = excluded.last_heard_at
                    """,
                    (circuit_id, item["song_id"], sent_at),
                )
            if stop_marker:
                cursor.execute(
                    """
                    INSERT INTO circuit_song_state (circuit_id, song_id, last_missing_at, last_stop_marker_at, last_heard_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(circuit_id, song_id) DO UPDATE SET
                        last_missing_at = excluded.last_missing_at,
                        last_stop_marker_at = excluded.last_stop_marker_at,
                        last_heard_at = excluded.last_heard_at
                    """,
                    (circuit_id, stop_marker["song_id"], sent_at, sent_at, sent_at),
                )
            self._apply_feedback(cursor, circuit_id, feedback_changes, source="confirmation", event_at=sent_at)
            cursor.execute(
                "UPDATE circuits SET last_sent_at = ?, last_scanned_at = ?, updated_at = ? WHERE circuit_id = ?",
                (sent_at, sent_at, sent_at, circuit_id),
            )
            conn.commit()
        return send_id

    def get_circuits(self, *, include_archived: bool = False) -> list[dict]:
        status_filter = "1=1" if include_archived else "status = 'ACTIVE'"
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT
                    c.*,
                    (
                        SELECT COUNT(*)
                        FROM circuit_send_items csi
                        JOIN circuit_sends cs ON csi.send_id = cs.send_id
                        WHERE cs.circuit_id = c.circuit_id
                          AND cs.send_id = (
                              SELECT send_id
                              FROM circuit_sends latest
                              WHERE latest.circuit_id = c.circuit_id
                              ORDER BY latest.sent_at DESC
                              LIMIT 1
                          )
                    ) AS last_track_count,
                    (
                        SELECT stop_marker_position
                        FROM circuit_sends latest
                        WHERE latest.circuit_id = c.circuit_id
                        ORDER BY latest.sent_at DESC
                        LIMIT 1
                    ) AS stop_marker_position
                FROM circuits c
                WHERE {status_filter}
                ORDER BY c.name
                """
            )
            rows = [self._decode_circuit_row(dict(row)) for row in cursor.fetchall()]
        return [self._decorate_circuit_row(row) for row in rows]

    def get_circuit(self, circuit_id: str) -> dict | None:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM circuits WHERE circuit_id = ?", (circuit_id,))
            row = cursor.fetchone()
            circuit = self._decode_circuit_row(dict(row)) if row else None
        return self._decorate_circuit_row(circuit) if circuit else None

    def delete_circuit(self, circuit_id: str) -> None:
        now = self._now()
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE circuits SET status = 'ARCHIVED', updated_at = ? WHERE circuit_id = ?",
                (now, circuit_id),
            )
            conn.commit()

    def send_circuit(self, circuit_id: str, limit: int | None = None, job_context=None) -> str | None:
        circuit = self.get_circuit(circuit_id)
        if not circuit:
            raise ValueError("Circuit was not found.")
        target_dir = Path(circuit["target_path"])
        target_dir.mkdir(parents=True, exist_ok=True)
        tracks = self._source_tracks(circuit.get("source_config") or {})
        if limit is None:
            policy_limit = (circuit.get("fill_policy") or {}).get("limit")
            limit = self._coerce_positive_int(policy_limit)
        if limit is not None:
            tracks = tracks[:]
            random.shuffle(tracks)
            tracks = tracks[: min(limit, len(tracks))]
        else:
            tracks = tracks[:]
            random.shuffle(tracks)

        self._clear_audio_files(target_dir)
        if not tracks:
            logger.warning("No downloaded tracks available to send for circuit %s.", circuit_id)
            return None

        send_id = str(uuid.uuid4())
        sent_at = self._now()
        copied_items: list[dict] = []
        total = len(tracks)
        for index, track in enumerate(tracks, 1):
            if job_context and job_context.is_cancelled:
                logger.info("Circuit send cancelled cooperatively.")
                break
            if job_context:
                job_context.wait_if_paused()
                job_context.update_progress(index, total)
                job_context.update_current_item(f"{track.get('artist', '')} - {track.get('title', '')}")
            filepath = track.get("filepath")
            if not filepath:
                continue
            src_file = Path(filepath)
            if not src_file.exists():
                continue
            filename = self._track_filename(index, track)
            dst_file = target_dir / filename
            shutil.copy2(src_file, dst_file)
            copied_items.append(
                {
                    "song_id": track["song_id"],
                    "position": index,
                    "filename": filename,
                    "title": track.get("title"),
                    "artist": track.get("artist"),
                }
            )

        if job_context and job_context.is_cancelled:
            return None
        if not copied_items:
            return None

        manifest_path = target_dir / ".musiccircuit.json"
        self._write_manifest(target_dir, circuit_id, send_id, copied_items, operation="send")
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO circuit_sends (send_id, circuit_id, sent_at, operation, target_path, manifest_path, track_count)
                VALUES (?, ?, ?, 'send', ?, ?, ?)
                """,
                (send_id, circuit_id, sent_at, str(target_dir), str(manifest_path), len(copied_items)),
            )
            for item in copied_items:
                cursor.execute(
                    """
                    INSERT INTO circuit_send_items (send_id, song_id, position, filename, returned_state)
                    VALUES (?, ?, ?, ?, 'sent')
                    """,
                    (send_id, item["song_id"], item["position"], item["filename"]),
                )
                cursor.execute(
                    """
                    INSERT INTO circuit_song_state (circuit_id, song_id, last_sent_at, sent_count)
                    VALUES (?, ?, ?, 1)
                    ON CONFLICT(circuit_id, song_id) DO UPDATE SET
                        last_sent_at = excluded.last_sent_at,
                        sent_count = COALESCE(circuit_song_state.sent_count, 0) + 1
                    """,
                    (circuit_id, item["song_id"], sent_at),
                )
            cursor.execute(
                "UPDATE circuits SET last_sent_at = ?, updated_at = ? WHERE circuit_id = ?",
                (sent_at, sent_at, circuit_id),
            )
            conn.commit()
        return send_id

    def scan_return(self, circuit_id: str, job_context=None) -> dict:
        circuit = self.get_circuit(circuit_id)
        if not circuit:
            raise ValueError("Circuit was not found.")
        send = self._latest_send(circuit_id)
        if not send:
            return {"total": 0, "missing": 0, "heard": 0, "stop_marker": None}
        target_dir = Path(circuit["target_path"])
        if not target_dir.exists():
            raise FileNotFoundError(f"Circuit folder does not exist: {target_dir}")
        items = self._send_items(send["send_id"])
        missing_items = [item for item in items if not (target_dir / item["filename"]).exists()]
        stop_marker = max(missing_items, key=lambda item: item["position"]) if missing_items else None
        heard_position = int(stop_marker["position"]) if stop_marker else 0
        scanned_at = self._now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            for index, item in enumerate(items, 1):
                if job_context and job_context.is_cancelled:
                    logger.info("Circuit scan cancelled cooperatively.")
                    break
                if job_context:
                    job_context.wait_if_paused()
                    job_context.update_progress(index, len(items))
                    job_context.update_current_item(item["filename"])
                exists = (target_dir / item["filename"]).exists()
                if stop_marker and item["song_id"] == stop_marker["song_id"]:
                    state = "stop_marker"
                elif exists:
                    state = "present"
                else:
                    state = "missing"
                cursor.execute(
                    "UPDATE circuit_send_items SET returned_state = ? WHERE send_id = ? AND song_id = ?",
                    (state, send["send_id"], item["song_id"]),
                )
                heard_at = scanned_at if heard_position and int(item["position"]) <= heard_position else None
                cursor.execute(
                    """
                    INSERT INTO circuit_song_state (
                        circuit_id, song_id, last_returned_at, last_missing_at,
                        last_stop_marker_at, last_heard_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(circuit_id, song_id) DO UPDATE SET
                        last_returned_at = excluded.last_returned_at,
                        last_missing_at = COALESCE(excluded.last_missing_at, circuit_song_state.last_missing_at),
                        last_stop_marker_at = COALESCE(excluded.last_stop_marker_at, circuit_song_state.last_stop_marker_at),
                        last_heard_at = COALESCE(excluded.last_heard_at, circuit_song_state.last_heard_at)
                    """,
                    (
                        circuit_id,
                        item["song_id"],
                        scanned_at if exists else None,
                        scanned_at if not exists else None,
                        scanned_at if state == "stop_marker" else None,
                        heard_at,
                    ),
                )
            cursor.execute(
                """
                UPDATE circuit_sends
                SET scanned_at = ?, stop_marker_song_id = ?, stop_marker_position = ?
                WHERE send_id = ?
                """,
                (
                    scanned_at,
                    stop_marker["song_id"] if stop_marker else None,
                    stop_marker["position"] if stop_marker else None,
                    send["send_id"],
                ),
            )
            cursor.execute(
                "UPDATE circuits SET last_scanned_at = ?, updated_at = ? WHERE circuit_id = ?",
                (scanned_at, scanned_at, circuit_id),
            )
            conn.commit()
        return {
            "total": len(items),
            "missing": len(missing_items),
            "heard": heard_position,
            "stop_marker": stop_marker,
        }

    def clear_circuit(self, circuit_id: str, job_context=None) -> int:
        circuit = self.get_circuit(circuit_id)
        if not circuit:
            raise ValueError("Circuit was not found.")
        target_dir = Path(circuit["target_path"])
        if not target_dir.exists():
            raise FileNotFoundError(f"Circuit folder does not exist: {target_dir}")
        removed = self._clear_audio_files(target_dir, job_context=job_context)
        self._write_manifest(target_dir, circuit_id, None, [], operation="clear")
        now = self._now()
        with self.db.get_connection() as conn:
            conn.execute("UPDATE circuits SET updated_at = ? WHERE circuit_id = ?", (now, circuit_id))
            conn.commit()
        return removed

    def _source_tracks(self, source_config: dict[str, Any]) -> list[dict]:
        source_type = source_config.get("type")
        if source_type == "all_downloaded":
            return self.collection_manager.search_media(MediaQuery(downloaded=True, archived=False))
        if source_type in ("collection", "collections"):
            collection_ids = source_config.get("collection_ids") or []
            if source_config.get("collection_id"):
                collection_ids = [source_config["collection_id"], *collection_ids]
            seen = set()
            tracks = []
            for collection_id in collection_ids:
                query = MediaQuery(collection_id=collection_id, downloaded=True, archived=False, sort_by="collection_order")
                for track in self.collection_manager.search_media(query):
                    song_id = track.get("song_id")
                    if song_id and song_id not in seen:
                        seen.add(song_id)
                        tracks.append(track)
            return tracks
        return []

    def _select_add_items(
        self,
        circuit: dict,
        candidates: list[dict],
        add_count: int,
        existing_filenames: set[str],
        max_position: int,
        *,
        feedback_changes: dict | None = None,
    ) -> list[dict]:
        if add_count <= 0 or not candidates:
            return []
        policy = self._smart_fill_policy(circuit)
        ranked = self._score_refill_candidates(circuit["circuit_id"], candidates, policy, feedback_changes or {})
        selected_tracks = self._weighted_sample_without_replacement(ranked, add_count)
        add_items = []
        next_position = max_position + 1
        for track in selected_tracks:
            filename = self._track_filename(next_position, track)
            while filename in existing_filenames:
                next_position += 1
                filename = self._track_filename(next_position, track)
            existing_filenames.add(filename)
            add_items.append({
                "song_id": track["song_id"],
                "position": next_position,
                "filename": filename,
                "title": track.get("title"),
                "artist": track.get("artist"),
                "filepath": track.get("filepath"),
                "selection_score": round(float(track.get("_selection_score") or 0), 3),
            })
            next_position += 1
        return add_items

    def _eligible_refill_count(self, circuit: dict, candidates: list[dict], feedback_changes: dict) -> int:
        if not candidates:
            return 0
        policy = self._smart_fill_policy(circuit)
        return len(self._score_refill_candidates(circuit["circuit_id"], candidates, policy, feedback_changes))

    def _score_refill_candidates(
        self,
        circuit_id: str,
        candidates: list[dict],
        policy: dict[str, Any],
        feedback_changes: dict,
    ) -> list[dict]:
        song_ids = [track.get("song_id") for track in candidates if track.get("song_id")]
        state = self._circuit_song_state(circuit_id, song_ids)
        feedback = self.get_feedback_state(circuit_id, song_ids)
        now = datetime.datetime.now(datetime.timezone.utc)
        scored = []
        for track in candidates:
            song_id = track.get("song_id")
            if not song_id:
                continue
            ratings = dict(feedback.get(song_id, {"global": "neutral", "here": "neutral"}))
            for scope, rating in (feedback_changes.get(song_id) or {}).items():
                if scope in ("global", "here"):
                    ratings[scope] = self._normalize_rating(rating)
            if ratings.get("here") == "disliked" and policy.get("circuit_boo_mode") == "suppress":
                continue
            score = float(policy["base_score"])
            if ratings.get("here") == "liked":
                score += float(policy["circuit_loved_bonus"])
            if ratings.get("global") == "liked":
                score += float(policy["global_loved_bonus"])
            elif ratings.get("global") == "disliked":
                score -= float(policy["global_boo_penalty"])

            row = state.get(song_id, {})
            if not row.get("last_sent_at"):
                score += float(policy["never_sent_bonus"])
            score += self._recency_score(row, now, policy)
            jitter = float(policy.get("jitter") or 0)
            if jitter > 0:
                score += random.uniform(-jitter, jitter)
            score = max(float(policy["minimum_weight"]), score)
            item = dict(track)
            item["_selection_score"] = score
            scored.append(item)
        return scored

    def _weighted_sample_without_replacement(self, candidates: list[dict], count: int) -> list[dict]:
        pool = list(candidates)
        selected = []
        for _ in range(min(count, len(pool))):
            total = sum(max(0.0, float(item.get("_selection_score") or 0.0)) for item in pool)
            if total <= 0:
                pick_index = random.randrange(len(pool))
            else:
                ticket = random.uniform(0, total)
                running = 0.0
                pick_index = len(pool) - 1
                for index, item in enumerate(pool):
                    running += max(0.0, float(item.get("_selection_score") or 0.0))
                    if ticket <= running:
                        pick_index = index
                        break
            selected.append(pool.pop(pick_index))
        return selected

    def _smart_fill_policy(self, circuit: dict) -> dict[str, Any]:
        policy = dict(circuit.get("fill_policy") or {})
        defaults = {
            "base_score": 100,
            "circuit_loved_bonus": 45,
            "global_loved_bonus": 25,
            "global_boo_penalty": 25,
            "circuit_boo_mode": "suppress",
            "never_sent_bonus": 35,
            "minimum_weight": 5,
            "jitter": 10,
        }
        for key, value in defaults.items():
            policy.setdefault(key, value)
        return policy

    def _recency_score(self, state: dict, now: datetime.datetime, policy: dict[str, Any]) -> float:
        dates = [
            self._parse_datetime(state.get("last_heard_at")),
            self._parse_datetime(state.get("last_sent_at")),
        ]
        last_activity = max([date for date in dates if date], default=None)
        if not last_activity:
            return 0.0
        days = max(0.0, (now - last_activity).total_seconds() / 86400)
        if days < 1:
            return -45.0
        if days < 4:
            return -30.0
        if days < 8:
            return -18.0
        if days < 15:
            return -8.0
        if days < 31:
            return 0.0
        return 10.0

    def _circuit_song_state(self, circuit_id: str, song_ids: list[str]) -> dict[str, dict]:
        unique_ids = [song_id for song_id in dict.fromkeys(song_ids) if song_id]
        if not unique_ids:
            return {}
        rows = {}
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            for chunk in self._chunks(unique_ids, 800):
                placeholders = ",".join("?" for _ in chunk)
                cursor.execute(
                    f"""
                    SELECT *
                    FROM circuit_song_state
                    WHERE circuit_id = ? AND song_id IN ({placeholders})
                    """,
                    [circuit_id, *chunk],
                )
                rows.update({row["song_id"]: dict(row) for row in cursor.fetchall()})
        return rows

    def _chunks(self, values: list[str], size: int) -> list[list[str]]:
        return [values[index:index + size] for index in range(0, len(values), size)]

    def _parse_datetime(self, value: Any) -> datetime.datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        return parsed.astimezone(datetime.timezone.utc)

    def _latest_send(self, circuit_id: str) -> dict | None:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT *
                FROM circuit_sends
                WHERE circuit_id = ?
                ORDER BY sent_at DESC
                LIMIT 1
                """,
                (circuit_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def _send_items(self, send_id: str) -> list[dict]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT csi.*, s.title, s.artist
                FROM circuit_send_items csi
                JOIN songs s ON csi.song_id = s.song_id
                WHERE csi.send_id = ?
                ORDER BY csi.position ASC
                """,
                (send_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_feedback_state(self, circuit_id: str, song_ids: list[str]) -> dict[str, dict[str, str]]:
        unique_ids = [song_id for song_id in dict.fromkeys(song_ids) if song_id]
        state = {song_id: {"global": "neutral", "here": "neutral"} for song_id in unique_ids}
        if not unique_ids:
            return state
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            for chunk in self._chunks(unique_ids, 800):
                placeholders = ",".join("?" for _ in chunk)
                cursor.execute(
                    f"SELECT song_id, rating FROM song_feedback WHERE song_id IN ({placeholders})",
                    chunk,
                )
                for row in cursor.fetchall():
                    state[row["song_id"]]["global"] = row["rating"] or "neutral"
                cursor.execute(
                    f"""
                    SELECT song_id, rating
                    FROM circuit_song_preferences
                    WHERE circuit_id = ? AND song_id IN ({placeholders})
                    """,
                    [circuit_id, *chunk],
                )
                for row in cursor.fetchall():
                    state[row["song_id"]]["here"] = row["rating"] or "neutral"
        return state

    def set_feedback_rating(
        self,
        song_id: str,
        rating: str,
        *,
        scope: str = "global",
        circuit_id: str | None = None,
        source: str = "manual",
    ) -> str:
        rating = self._normalize_rating(rating)
        scope = "here" if scope == "here" else "global"
        if scope == "here" and not circuit_id:
            raise ValueError("circuit_id is required for local circuit feedback")
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self._apply_feedback(cursor, circuit_id, {song_id: {scope: rating}}, source=source, event_at=now)
            conn.commit()
        return rating

    def get_circuit_track_feedback(self, circuit_id: str) -> list[dict]:
        circuit = self.get_circuit(circuit_id)
        if not circuit:
            return []
        items = self._source_tracks(circuit.get("source_config") or {})
        feedback = self.get_feedback_state(circuit_id, [item.get("song_id") for item in items])
        for item in items:
            ratings = feedback.get(item.get("song_id"), {})
            item["global_rating"] = ratings.get("global") or "neutral"
            item["here_rating"] = ratings.get("here") or "neutral"
        items.sort(key=lambda item: (str(item.get("artist") or "").lower(), str(item.get("title") or "").lower()))
        return items

    def get_feedback_debug_rows(self, circuit_id: str | None = None, limit: int = 200) -> list[dict]:
        params: list[Any] = []
        where = ""
        if circuit_id:
            where = "WHERE csp.circuit_id = ? OR e.circuit_id = ?"
            params.extend([circuit_id, circuit_id])
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT
                    s.title,
                    s.artist,
                    COALESCE(c.name, '-') AS circuit_name,
                    COALESCE(sf.rating, 'neutral') AS global_rating,
                    COALESCE(csp.rating, 'neutral') AS here_rating,
                    css.last_heard_at,
                    sf.last_liked_at AS global_liked_at,
                    sf.last_disliked_at AS global_disliked_at,
                    csp.last_liked_at AS here_liked_at,
                    csp.last_disliked_at AS here_disliked_at,
                    e.scope AS event_scope,
                    e.rating AS event_rating,
                    e.source AS event_source,
                    e.event_at
                FROM song_feedback_events e
                JOIN songs s ON s.song_id = e.song_id
                LEFT JOIN circuits c ON c.circuit_id = e.circuit_id
                LEFT JOIN song_feedback sf ON sf.song_id = e.song_id
                LEFT JOIN circuit_song_preferences csp ON csp.circuit_id = e.circuit_id AND csp.song_id = e.song_id
                LEFT JOIN circuit_song_state css ON css.circuit_id = e.circuit_id AND css.song_id = e.song_id
                {where}
                ORDER BY e.event_at DESC
                LIMIT ?
                """,
                [*params, max(1, int(limit))],
            )
            return [dict(row) for row in cursor.fetchall()]

    def _apply_feedback(self, cursor, circuit_id: str | None, feedback_changes: dict, *, source: str, event_at: str) -> None:
        for song_id, scopes in feedback_changes.items():
            if not song_id:
                continue
            for scope, rating in (scopes or {}).items():
                rating = self._normalize_rating(rating)
                if scope not in ("global", "here"):
                    continue
                if scope == "global":
                    cursor.execute(
                        """
                        INSERT INTO song_feedback (song_id, rating, last_liked_at, last_disliked_at, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(song_id) DO UPDATE SET
                            rating = excluded.rating,
                            last_liked_at = COALESCE(excluded.last_liked_at, song_feedback.last_liked_at),
                            last_disliked_at = COALESCE(excluded.last_disliked_at, song_feedback.last_disliked_at),
                            updated_at = excluded.updated_at
                        """,
                        (
                            song_id,
                            rating,
                            event_at if rating == "liked" else None,
                            event_at if rating == "disliked" else None,
                            event_at,
                        ),
                    )
                else:
                    if not circuit_id:
                        continue
                    cursor.execute(
                        """
                        INSERT INTO circuit_song_preferences (
                            circuit_id, song_id, rating, last_liked_at, last_disliked_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(circuit_id, song_id) DO UPDATE SET
                            rating = excluded.rating,
                            last_liked_at = COALESCE(excluded.last_liked_at, circuit_song_preferences.last_liked_at),
                            last_disliked_at = COALESCE(excluded.last_disliked_at, circuit_song_preferences.last_disliked_at),
                            updated_at = excluded.updated_at
                        """,
                        (
                            circuit_id,
                            song_id,
                            rating,
                            event_at if rating == "liked" else None,
                            event_at if rating == "disliked" else None,
                            event_at,
                        ),
                    )
                cursor.execute(
                    """
                    INSERT INTO song_feedback_events (event_id, circuit_id, song_id, scope, rating, source, event_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (str(uuid.uuid4()), circuit_id, song_id, scope, rating, source, event_at),
                )

    def _normalize_rating(self, rating: Any) -> str:
        value = str(rating or "neutral").lower()
        return value if value in ("liked", "disliked", "neutral") else "neutral"

    def _decode_circuit_row(self, row: dict) -> dict:
        row["source_config"] = self._json_value(row.get("source_config_json"), {})
        row["fill_policy"] = self._json_value(row.get("fill_policy_json"), {})
        return row

    def _decorate_circuit_row(self, row: dict) -> dict:
        send = self._latest_send(row["circuit_id"])
        target_dir = Path(row.get("target_path") or "")
        previous_items = self._send_items(send["send_id"]) if send else []
        row["last_track_count"] = row.get("last_track_count") if row.get("last_track_count") is not None else len(previous_items)
        if not target_dir.exists():
            row["current_fresh_count"] = 0
            row["current_marker_title"] = None
            row["current_marker_position"] = None
            return row

        missing_items = [item for item in previous_items if not (target_dir / item["filename"]).exists()]
        detected_marker = max(missing_items, key=lambda item: int(item["position"])) if missing_items else None
        marker_position = int(detected_marker["position"]) if detected_marker else 0
        fresh_items = [
            item for item in previous_items
            if (target_dir / item["filename"]).exists()
            and (not marker_position or int(item["position"]) > marker_position)
        ]
        row["current_fresh_count"] = len(fresh_items)
        row["current_marker_title"] = detected_marker.get("title") if detected_marker else None
        row["current_marker_position"] = marker_position or None
        return row

    def _json_value(self, value: str | None, default: Any) -> Any:
        if not value:
            return default
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default

    def _track_filename(self, index: int, track: dict) -> str:
        safe_title = re.sub(r'[<>:"/\\|?*]', "", str(track.get("title") or "Untitled")).strip() or "Untitled"
        return f"{self._slot_label(index)} - {safe_title}.mp3"

    def _slot_label(self, index: int) -> str:
        base = len(self.SLOT_ALPHABET)
        value = max(0, int(index) - 1)
        chars = []
        for _ in range(self.SLOT_WIDTH):
            chars.append(self.SLOT_ALPHABET[value % base])
            value //= base
        return "".join(reversed(chars))

    def _target_count(self, circuit: dict) -> int:
        policy = circuit.get("fill_policy") or {}
        return self._coerce_positive_int(policy.get("target_count")) or self._coerce_positive_int(policy.get("limit")) or 25

    def _clear_audio_files(self, target_dir: Path, job_context=None) -> int:
        removed = 0
        files = list(target_dir.glob("*.mp3"))
        for index, file in enumerate(files, 1):
            if job_context and job_context.is_cancelled:
                break
            if job_context:
                job_context.wait_if_paused()
                job_context.update_progress(index, len(files))
                job_context.update_current_item(file.name)
            file.unlink(missing_ok=True)
            removed += 1
        return removed

    def _write_manifest(
        self,
        directory: Path,
        circuit_id: str,
        send_id: str | None,
        items: list[dict],
        *,
        operation: str,
    ) -> None:
        manifest = {
            "version": 1,
            "circuit_id": circuit_id,
            "send_id": send_id,
            "operation": operation,
            "track_count": len(items),
            "items": [
                {
                    "song_id": item.get("song_id"),
                    "position": item.get("position"),
                    "filename": item.get("filename"),
                    "title": item.get("title"),
                    "artist": item.get("artist"),
                }
                for item in items
            ],
        }
        directory.mkdir(parents=True, exist_ok=True)
        manifest_path = directory / ".musiccircuit.json"
        temp_path = directory / f".musiccircuit.{uuid.uuid4().hex}.tmp"
        temp_path.write_text(json.dumps(manifest, indent=4))
        os.replace(temp_path, manifest_path)

    def _coerce_positive_int(self, value: Any) -> int | None:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    def _now(self) -> str:
        return datetime.datetime.now(datetime.timezone.utc).isoformat()
