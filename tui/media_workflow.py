import os
import os
import subprocess
import sys
from pathlib import Path

from textual.widgets import ContentSwitcher

from core.jobs import JobType
from core.models import MediaQuery
from tui.flows.management import ManagementFlow


class MediaWorkflowMixin:
    def get_selected_media_item(self) -> dict | None:
        current = self.query_one(ContentSwitcher).current
        if current == "media":
            screen = self.query_one("#media")
            if hasattr(screen, "get_selected_item"):
                return screen.get_selected_item()
            items = getattr(screen, "_display_data", [])
            index = screen.get_list_index()
            return items[index] if 0 <= index < len(items) else None
        if current == "collection_details":
            screen = self.query_one("#collection_details")
            if hasattr(screen, "get_selected_item"):
                return screen.get_selected_item()
            items = getattr(screen, "_display_data", [])
            index = screen.get_list_index()
            return items[index] if 0 <= index < len(items) else None
        if current == "vibe_preview":
            screen = self.query_one("#vibe_preview")
            if hasattr(screen, "get_selected_item"):
                return screen.get_selected_item()
            items = getattr(screen, "_display_data", [])
            index = screen.get_list_index() if hasattr(screen, "get_list_index") else 0
            return items[index] if 0 <= index < len(items) else None
        if current == "media_details":
            media_id = getattr(self, "current_media_id", None)
            return self.collection_manager.get_track_details(media_id) if media_id else None
        return None

    def play_selected_track(self) -> None:
        item = self.get_selected_media_item()
        if not item:
            self.show_status("No track selected.")
            return

        details = self.collection_manager.get_track_details(item.get("song_id")) or item
        file_path = details.get("filepath") or details.get("file_path") or item.get("filepath") or item.get("file_path")
        if not file_path:
            self.show_status("Track has not been downloaded yet.")
            return

        path = Path(file_path)
        if not path.exists():
            self.show_status("Downloaded file is missing.")
            return

        try:
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif os.name == "posix":
                opener = "open" if sys.platform == "darwin" else "xdg-open"
                subprocess.Popen([opener, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                self.show_status("Playback is not supported on this platform.")
                return
            self.show_status(f"Playing: {details.get('title') or item.get('title', 'track')}")
        except Exception as exc:
            self.show_status(f"Could not play track: {exc}")

    def play_media_items(self, items: list[dict], label: str = "tracks") -> None:
        paths = []
        for item in items:
            details = self.collection_manager.get_track_details(item.get("song_id")) if item.get("song_id") else item
            file_path = (details or item).get("filepath") or (details or item).get("file_path") or item.get("filepath") or item.get("file_path")
            if file_path and Path(file_path).exists():
                paths.append(str(Path(file_path)))
        if not paths:
            self.show_status("No downloaded tracks available to play.")
            return
        try:
            if os.name == "nt":
                os.startfile(paths[0])  # type: ignore[attr-defined]
                for path in paths[1:]:
                    os.startfile(path)  # type: ignore[attr-defined]
            elif os.name == "posix":
                opener = "open" if sys.platform == "darwin" else "xdg-open"
                subprocess.Popen([opener, *paths], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                self.show_status("Playback is not supported on this platform.")
                return
            self.show_status(f"Playing {len(paths)} {label}.")
        except Exception as exc:
            self.show_status(f"Could not play tracks: {exc}")

    def play_selected_collection(self) -> None:
        collection = self.get_selected_collection()
        if not collection:
            self.show_status("No collection selected.")
            return
        collection_id = collection.get("collection_id")
        query = MediaQuery(collection_id=collection_id, downloaded=True, archived=False, sort_by="collection_order")
        items = self.collection_manager.search_media(query)
        self.play_media_items(items, "collection tracks")

    async def start_new_media_flow(self, origin_screen_id: str, collection: dict | None = None) -> None:
        await self.start_management_flow(
            ManagementFlow(
                kind="new_media_form",
                origin_screen_id=origin_screen_id,
                origin_actions=self.current_context_actions,
                collection_id=collection.get("collection_id") if collection else None,
                collection_name=collection.get("name") if collection else None,
                fields=[("Title", ""), ("Artist", ""), ("Media Type", "music"), ("URL", "")],
            )
        )

    async def start_delete_standalone_flow(self) -> None:
        item = self.get_selected_media_item()
        if not item:
            self.show_status("No media item selected.")
            return
        details = self.collection_manager.get_track_details(item.get("song_id"))
        if details.get("collections"):
            self.show_status("This item belongs to collections. Remove it from those collections first.")
            return
        origin = self.query_one(ContentSwitcher).current
        origin_index = 0
        if origin == "media":
            origin_index = self.query_one("#media").get_list_index()
        await self.start_management_flow(
            ManagementFlow(
                kind="delete_standalone_confirm",
                origin_screen_id=origin,
                origin_actions=self.current_context_actions,
                song_id=item.get("song_id"),
                origin_index=origin_index,
                options=["Yes", "No"],
            )
        )

    async def create_download_job(self) -> None:
        current = self.query_one(ContentSwitcher).current
        collection_id = None
        force_download = False
        if current == "collections":
            collection = self.get_selected_collection()
            if not collection:
                self.show_status("No collection selected.")
                return
            collection_id = collection.get("collection_id")
            query = MediaQuery(collection_id=collection_id, archived=False)
            items = self.collection_manager.search_media(query)
            name = collection.get("name", "Collection")
            target_func = self.download_collection_pipeline
        else:
            selected_items = self.get_selected_media_items()
            if not selected_items:
                self.show_status("No media item selected.")
                return
            items = [
                self.collection_manager.get_track_details(item.get("song_id")) or item
                for item in selected_items
            ]
            force_download = any(self.media_is_match_failed(item) for item in items)
            items = [item for item in items if self.media_is_download_ready(item, force=force_download)]
            if not items:
                self.show_status("No selected tracks are ready to download.")
                return
            name = selected_items[0].get("title", "Media Item") if len(selected_items) == 1 else f"{len(selected_items)} Tracks"
            target_func = self.acquisition_manager.download_media

        submit_kwargs = {
            "job_type": JobType.DOWNLOAD_COLLECTION,
            "description": f"Force Download {name}" if force_download and not collection_id else f"Download {name}",
            "target_func": target_func,
            "media_items": items,
            "metadata": {"operation": "download", "collection_id": collection_id} if collection_id else {"operation": "download"},
        }
        if force_download and not collection_id:
            submit_kwargs["force"] = True
        self.job_manager.submit(**submit_kwargs)
        self.show_status(f"Force downloading {name}." if not collection_id and force_download else f"Downloading {name}.")
        await self.refresh_jobs_view()

    def download_collection_pipeline(self, media_items: list[dict], job_context=None) -> None:
        if job_context:
            job_context.update_progress_message("Matching discovered tracks")
            job_context.wait_if_paused()
        self.matching_engine.process_discovered_tracks(job_context=job_context)
        if job_context and job_context.is_cancelled:
            return
        if job_context:
            job_context.update_progress_message("Downloading approved media")
            job_context.wait_if_paused()
        refreshed = []
        for item in media_items:
            details = self.collection_manager.get_track_details(item.get("song_id"))
            refreshed.append(details or item)
        refreshed = [item for item in refreshed if self.media_is_download_ready(item)]
        self.acquisition_manager.download_media(refreshed, job_context=job_context)

    def download_ready_tracks(self, job_context=None) -> None:
        query = MediaQuery(matched=True, downloaded=False, archived=False)
        media_items = self.collection_manager.search_media(query)
        self.download_collection_pipeline(media_items, job_context=job_context)

    def match_single_item(self, song_id: str, job_context=None, force_review: bool = False) -> None:
        details = self.collection_manager.get_track_details(song_id)
        if not details:
            raise RuntimeError("Media item not found.")
        if job_context:
            job_context.update_progress(1, 1)
            job_context.update_current_item(f"Matching: {details.get('title')} - {details.get('artist')}")
            job_context.wait_if_paused()
        self.matching_engine.process_single_track_by_id(song_id, job_context=job_context, force_review=force_review)

    def match_media_items(self, media_items: list[dict], job_context=None) -> None:
        total = len(media_items)
        for index, item in enumerate(media_items, 1):
            if job_context and job_context.is_cancelled:
                break
            if job_context:
                job_context.wait_if_paused()
                job_context.update_progress(index, total)
                job_context.update_current_item(f"Matching: {item.get('title')} - {item.get('artist')}")
            song_id = item.get("song_id")
            if song_id:
                self.matching_engine.process_single_track_by_id(
                    song_id,
                    job_context=job_context,
                    force_review=self.media_is_match_failed(item),
                )

    async def create_match_job(self) -> None:
        items = self.get_selected_media_items()
        if not items:
            self.show_status("Match is only available for selected media.")
            return
        name = items[0].get("title", "Media Item") if len(items) == 1 else f"{len(items)} Tracks"
        force_review = any(self.media_is_match_failed(item) for item in items)
        self.job_manager.submit(
            job_type=JobType.DISCOVER_MATCH,
            description=f"Force Rematch for Review {name}" if force_review else f"Match {name}",
            target_func=self.match_media_items,
            media_items=items,
        )
        self.show_status(f"Force rematching {name} for review." if force_review else f"Matching {name}.")
        await self.refresh_jobs_view()

    async def create_analyze_track_job(self, *, force_all_downloaded: bool = False) -> None:
        if force_all_downloaded:
            count = self.audio_analysis_manager.count_downloaded_features()
            if not count:
                self.show_status("No downloaded tracks are available for analysis.")
                return
            self.job_manager.submit(
                job_type=JobType.ANALYZE_TRACK,
                description=f"Analyze Downloaded Tracks ({count})",
                target_func=self.audio_analysis_manager.analyze_downloaded_features,
                metadata={"operation": "analysis", "scope": "downloaded"},
            )
            self.show_status(f"Analyzing {count} downloaded tracks.")
            await self.refresh_jobs_view()
            return

        selected_items = self.get_selected_media_items()
        if not selected_items:
            self.show_status("Analysis is only available for selected media.")
            return
        details = [
            self.collection_manager.get_track_details(item.get("song_id")) or item
            for item in selected_items
        ]
        details = [item for item in details if self.media_has_file(item)]
        if not details:
            self.show_status("Selected tracks must be downloaded before analysis.")
            return
        song_ids = [item.get("song_id") for item in details if item.get("song_id")]
        if not song_ids:
            self.show_status("Selected tracks have no song ids.")
            return
        title = details[0].get("title") or "Media Item"
        description = f"Analyze {title}" if len(song_ids) == 1 else f"Analyze Selected Tracks ({len(song_ids)})"
        self.job_manager.submit(
            job_type=JobType.ANALYZE_TRACK,
            description=description,
            target_func=self.audio_analysis_manager.analyze_song_ids,
            song_ids=song_ids,
            metadata={"operation": "analysis", "scope": "selected", "count": len(song_ids)},
        )
        self.show_status(f"Analyzing {title}." if len(song_ids) == 1 else f"Analyzing {len(song_ids)} selected tracks.")
        await self.refresh_jobs_view()

    async def create_analyze_collection_job(self) -> None:
        collection = self.get_selected_collection()
        if not collection:
            self.show_status("No collection selected.")
            return
        collection_id = collection.get("collection_id")
        if not collection_id:
            self.show_status("Selected collection has no id.")
            return

        query = MediaQuery(collection_id=collection_id, archived=False)
        items = self.collection_manager.search_media(query)
        downloaded_items = [item for item in items if self.media_has_file(item)]
        song_ids = [item.get("song_id") for item in downloaded_items if item.get("song_id")]
        if not song_ids:
            self.show_status("Selected collection has no downloaded tracks to analyze.")
            return

        name = collection.get("name") or "Selected Collection"
        self.job_manager.submit(
            job_type=JobType.ANALYZE_TRACK,
            description=f"Analyze Collection: {name} ({len(song_ids)})",
            target_func=self.audio_analysis_manager.analyze_song_ids,
            song_ids=song_ids,
            metadata={
                "operation": "analysis",
                "scope": "collection",
                "collection_id": collection_id,
                "count": len(song_ids),
            },
        )
        self.show_status(f"Analyzing {len(song_ids)} tracks from {name}.")
        await self.refresh_jobs_view()

    async def start_archive_confirmation(self) -> None:
        item = self.get_selected_media_item()
        if not item:
            self.show_status("Archive is only available for selected media.")
            return
        status = str(item.get("song_status") or item.get("status") or "").upper()
        self.input_mode = "UNARCHIVE_CONFIRM" if status == "ARCHIVED" else "ARCHIVE_CONFIRM"
        self.input_query = item.get("song_id", "")
        self.input_cached_actions = self.current_context_actions
        self.update_context_actions("")
        prompt = "Unarchive item? (Y/N)" if self.input_mode == "UNARCHIVE_CONFIRM" else "Archive item? (Y/N)"
        self.show_status(prompt, persistent=True)

    async def confirm_archive_item(self) -> None:
        song_id = self.input_query
        current = self.query_one(ContentSwitcher).current
        collection_id = self.current_collection_id if current == "collection_details" and getattr(self, "current_collection_id", None) else None
        self.collection_manager.archive_media_item(song_id, collection_id)

        self.input_mode = "NORMAL"
        self.input_query = ""
        await self.refresh_current_list()
        self.update_current_context_actions()
        self.show_status("Item archived.")

    async def confirm_unarchive_item(self) -> None:
        song_id = self.input_query
        self.collection_manager.unarchive_media_item(song_id)

        self.input_mode = "NORMAL"
        self.input_query = ""
        await self.refresh_current_list()
        self.update_current_context_actions()
        self.show_status("Item unarchived.")

    def is_media_selected(self, song_id: str | None) -> bool:
        return bool(song_id and song_id in self.selected_media_ids)

    def current_media_list_context(self) -> tuple[object | None, list[dict], str | None]:
        current = self.query_one(ContentSwitcher).current
        if current == "media":
            screen = self.query_one("#media")
            return screen, list(getattr(screen, "_display_data", [])), "media"
        if current == "collection_details":
            screen = self.query_one("#collection_details")
            return screen, list(getattr(screen, "_display_data", [])), "collection_details"
        if current == "vibe_preview":
            screen = self.query_one("#vibe_preview")
            return screen, list(getattr(screen, "_display_data", []) or []), "vibe_preview"
        return None, [], None

    def get_current_media_index(self, screen: object | None) -> int:
        if screen and hasattr(screen, "get_list_index"):
            return screen.get_list_index()
        return 0

    def should_clear_media_selection_for_key(self, event) -> bool:
        if not self.selected_media_ids:
            return False
        if not self.current_media_list_context()[0]:
            return False
        if self.management_shift_pressed(event):
            return False
        key = (event.key or "").lower().replace("_", "+")
        navigation_keys = {
            "up", "down", "left", "right", "pageup", "pagedown",
            "home", "end", "ctrl+home", "ctrl+end",
        }
        return key in navigation_keys or key.endswith(("+up", "+down", "+pageup", "+pagedown", "+home", "+end"))

    def clear_media_selection(self) -> None:
        if not self.selected_media_ids and not self.selected_media_anchor_id:
            return
        screen, _items, _context = self.current_media_list_context()
        self.selected_media_ids = set()
        self.selected_media_anchor_id = None
        if screen and hasattr(screen, "refresh_selection_labels"):
            screen.refresh_selection_labels()

    def action_select_all_media(self) -> bool:
        screen, items, _context = self.current_media_list_context()
        if not screen or not items:
            return False
        self.selected_media_ids = {
            str(item.get("song_id"))
            for item in items
            if item.get("song_id")
        }
        self.selected_media_anchor_id = next(
            (str(item.get("song_id")) for item in items if item.get("song_id")),
            None,
        )
        if hasattr(screen, "refresh_selection_labels"):
            screen.refresh_selection_labels()
        self.show_status(f"Selected {len(self.selected_media_ids)} tracks.")
        return True

    async def extend_media_selection(self, direction: str) -> bool:
        screen, items, context = self.current_media_list_context()
        if not screen or not items:
            return False

        current_index = max(0, min(self.get_current_media_index(screen), len(items) - 1))
        delta = -1 if direction == "up" else 1
        next_index = max(0, min(current_index + delta, len(items) - 1))

        anchor_id = self.selected_media_anchor_id or items[current_index].get("song_id")
        self.selected_media_anchor_id = anchor_id
        if hasattr(screen, "set_list_index"):
            screen.set_list_index(next_index)

        anchor_index = next(
            (idx for idx, item in enumerate(items) if item.get("song_id") == anchor_id),
            current_index,
        )
        lo, hi = sorted((anchor_index, next_index))
        self.selected_media_ids = {
            str(item.get("song_id"))
            for item in items[lo:hi + 1]
            if item.get("song_id")
        }
        if hasattr(screen, "refresh_selection_labels"):
            screen.refresh_selection_labels()
        screen.set_list_index(next_index)
        self.show_status(f"Selected {len(self.selected_media_ids)} tracks.")
        return True

    def clear_media_selection_if_outside_lists(self, target_id: str) -> None:
        if target_id not in ("media", "collection_details"):
            self.selected_media_ids = set()
            self.selected_media_anchor_id = None

    def get_selected_media_items(self) -> list[dict]:
        screen, items, _context = self.current_media_list_context()
        if screen and self.selected_media_ids:
            selected = [item for item in items if item.get("song_id") in self.selected_media_ids]
            if selected:
                return selected
        item = self.get_selected_media_item()
        if item:
            return [item]
        return []
