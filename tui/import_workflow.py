import json
import re
from pathlib import Path

from textual.widgets import ContentSwitcher

from core.jobs import JobType
from core.models import MediaQuery
from tui.flows.management import ManagementFlow


def _fuzzy_score(query: str, text: str) -> float:
    if not query:
        return 1000.0
    q = query.lower()
    t = text.lower()
    if q == t:
        return 10000.0
    if t.startswith(q):
        return 5000.0 - len(t)
    if q in t:
        return 1000.0 - len(t)
    score = 0
    q_i = 0
    for char in t:
        if q_i < len(q) and char == q[q_i]:
            score += 10
            q_i += 1
    if q_i == len(q):
        return 100.0 + score - len(t)
    return float(score - len(t))


def _fuzzy_media_score(query: str, item: dict) -> float:
    return _fuzzy_score(query, item.get("title", "")) + (_fuzzy_score(query, item.get("artist", "")) * 0.5)


class ImportWorkflowMixin:
    async def commit_external_collection_url(self, flow: ManagementFlow) -> None:
        url = flow.query.strip()
        if not url:
            self.show_status("URL cannot be empty.")
            return
        source_type = self.detect_url_source(url)
        if not source_type or source_type not in self.plugins:
            self.show_status("Unsupported collection URL.")
            return

        origin = flow.origin_screen_id
        actions = flow.origin_actions
        self.management_flow = None
        self.query_one(ContentSwitcher).current = origin
        self._focus_main_content(origin)
        self.update_current_context_actions()
        self.job_manager.submit(
            job_type=JobType.REFRESH_COLLECTION,
            description=f"Import {source_type.title()} Collection",
            target_func=self.import_collection_from_url,
            source_type=source_type,
            url=url,
        )
        self.show_status(f"Importing {source_type.title()} collection.")
        await self.refresh_jobs_view()

    async def commit_youtube_radio_collection(self, flow: ManagementFlow) -> None:
        seed_video_id = (flow.setting_path or "").strip()
        if not seed_video_id:
            self.show_status("Selected media has no YouTube acquisition source.")
            return
        try:
            count = int(flow.query.strip())
        except ValueError:
            self.show_status("Number of songs must be a whole number.")
            return
        if count < 1:
            self.show_status("Number of songs must be at least 1.")
            return
        if count > 500:
            self.show_status("Number of songs must be 500 or fewer.")
            return

        origin = flow.origin_screen_id
        actions = flow.origin_actions
        name = flow.collection_name or "YouTube Radio"
        self.management_flow = None
        self.query_one(ContentSwitcher).current = origin
        self._focus_main_content(origin)
        self.update_current_context_actions()
        self.job_manager.submit(
            job_type=JobType.REFRESH_COLLECTION,
            description=f"Import {name}",
            target_func=self.import_youtube_radio_collection,
            seed_video_id=seed_video_id,
            max_results=count,
        )
        self.show_status(f"Importing {name}.")
        await self.refresh_jobs_view()

    async def commit_local_folder_path(self, flow: ManagementFlow) -> None:
        folder = flow.query.strip().strip('"')
        if not folder:
            self.show_status("Folder path cannot be empty.")
            return
        plugin = self.plugins.get("local_folder")
        if not plugin:
            self.show_status("Local folder import is not available.")
            return
        try:
            preview = plugin.preview_folder(folder)
        except Exception as exc:
            self.show_status(str(exc))
            return
        if int(preview.get("total_count", 0) or 0) <= 0:
            self.show_status("No supported audio files were found in that folder.")
            return

        flow.kind = "local_folder_import_confirm"
        flow.payload = preview
        flow.selected_index = 0
        flow.options = [
            f"Import All As One Collection ({preview.get('total_count', 0)} tracks, 1 collection)",
            f"Import Immediate Subfolders ({preview.get('subfolder_collection_count', 0)} collections)",
            "Cancel",
        ]
        await self.render_management_flow()

    async def commit_local_folder_import_confirm(self, flow: ManagementFlow) -> None:
        if flow.selected_index == 2:
            await self.cancel_management_flow()
            return
        folder = flow.payload.get("path")
        if not folder:
            self.show_status("Folder path is missing.")
            return
        if flow.selected_index == 1 and int(flow.payload.get("subfolder_collection_count", 0) or 0) <= 0:
            self.show_status("No immediate subfolders contain supported audio files.")
            return
        mode = "immediate_subfolders" if flow.selected_index == 1 else "single_collection"
        origin = flow.origin_screen_id
        actions = flow.origin_actions
        self.management_flow = None
        self.query_one(ContentSwitcher).current = origin
        self._focus_main_content(origin)
        self.update_current_context_actions()
        description = (
            f"Import Local Subfolders: {Path(folder).name or folder}"
            if mode == "immediate_subfolders"
            else f"Import Local Folder: {Path(folder).name or folder}"
        )
        self.job_manager.submit(
            job_type=JobType.REFRESH_COLLECTION,
            description=description,
            target_func=self.import_local_folder_collection,
            folder=folder,
            mode=mode,
        )
        self.show_status("Importing local folder.")
        await self.refresh_jobs_view()

    async def commit_external_media_url(self, flow: ManagementFlow) -> None:
        url = flow.query.strip()
        if not url:
            self.show_status("URL cannot be empty.")
            return
        source_type = self.detect_url_source(url)
        if not source_type or source_type not in self.plugins:
            self.show_status("Unsupported media URL.")
            return

        origin = flow.origin_screen_id
        actions = flow.origin_actions
        collection_id = flow.collection_id
        self.management_flow = None
        self.query_one(ContentSwitcher).current = origin
        self._focus_main_content(origin)
        self.update_current_context_actions()
        self.job_manager.submit(
            job_type=JobType.REFRESH_COLLECTION,
            description=f"Import {source_type.title()} Media",
            target_func=self.import_media_from_url,
            source_type=source_type,
            url=url,
            collection_id=collection_id,
        )
        self.show_status(f"Importing {source_type.title()} media.")
        await self.refresh_jobs_view()

    async def commit_local_file_path(self, flow: ManagementFlow) -> None:
        file_path = flow.query.strip().strip('"')
        if not file_path:
            self.show_status("File path cannot be empty.")
            return

        origin = flow.origin_screen_id
        actions = flow.origin_actions
        collection_id = flow.collection_id
        self.management_flow = None
        self.query_one(ContentSwitcher).current = origin
        self._focus_main_content(origin)
        self.update_current_context_actions()
        self.job_manager.submit(
            job_type=JobType.REFRESH_COLLECTION,
            description=f"Import Local File: {Path(file_path).name or file_path}",
            target_func=self.import_local_file_media,
            file_path=file_path,
            collection_id=collection_id,
        )
        self.show_status("Importing local file.")
        await self.refresh_jobs_view()

    def detect_url_source(self, url: str) -> str | None:
        value = url.lower()
        if "spotify.com" in value or value.startswith("spotify:"):
            return "spotify"
        if "youtube.com" in value or "youtu.be" in value:
            return "youtube"
        if "billboard.com/charts/" in value or value.startswith("billboard:"):
            return "billboard"
        return None

    def import_collection_from_url(self, source_type: str, url: str, job_context=None) -> None:
        plugin = self.plugins.get(source_type)
        if not plugin:
            raise RuntimeError(f"No plugin available for {source_type}.")
        if job_context:
            job_context.update_current_item(f"Fetching {source_type} collection")
        collection = plugin.fetch_collection(url)
        if not collection:
            raise RuntimeError("No collection could be imported from that URL.")
        if job_context and job_context.is_cancelled:
            return
        self.collection_manager.save_collection(collection, job_context=job_context)

    def import_youtube_radio_collection(self, seed_video_id: str, max_results: int, job_context=None) -> None:
        plugin = self.plugins.get("youtube")
        if not plugin or not hasattr(plugin, "fetch_radio_collection"):
            raise RuntimeError("YouTube radio import is not available.")
        if job_context:
            job_context.update_current_item("Fetching YouTube radio")
        collection = plugin.fetch_radio_collection(seed_video_id, max_results)
        if not collection:
            raise RuntimeError("No YouTube radio collection could be imported.")
        if job_context and job_context.is_cancelled:
            return
        self.collection_manager.save_collection(collection, job_context=job_context)

    def import_local_folder_collection(self, folder: str, mode: str = "single_collection", job_context=None) -> None:
        plugin = self.plugins.get("local_folder")
        if not plugin:
            raise RuntimeError("Local folder import is not available.")
        if job_context:
            job_context.update_current_item("Scanning local folder")
        if mode == "immediate_subfolders":
            collections = plugin.fetch_immediate_subfolder_collections(folder)
            if not collections:
                raise RuntimeError("No immediate subfolders contain supported audio files.")
            for index, collection in enumerate(collections, 1):
                if job_context and job_context.is_cancelled:
                    return
                if job_context:
                    job_context.update_progress_message(f"Importing collection {index} of {len(collections)}")
                self._save_and_acquire_local_collection(collection, job_context=job_context)
            return

        collection = plugin.fetch_collection(folder)
        if not collection:
            raise RuntimeError("No supported audio files were found in that folder.")
        self._save_and_acquire_local_collection(collection, job_context=job_context)

    def _save_and_acquire_local_collection(self, collection, job_context=None) -> None:
        if job_context and job_context.is_cancelled:
            return
        collection_id = self.collection_manager.save_collection(collection, job_context=job_context)
        if job_context and job_context.is_cancelled:
            return
        media_items = self.collection_manager.search_media(
            MediaQuery(collection_id=collection_id, downloaded=False, archived=False)
        )
        local_items = [item for item in media_items if self._acquisition_provider(item) == "local_file"]
        if local_items:
            if job_context:
                job_context.update_progress_message("Copying local files into library")
            self.acquisition_manager.download_media(local_items, job_context=job_context)

    def import_media_from_url(self, source_type: str, url: str, collection_id: str | None = None, job_context=None) -> None:
        plugin = self.plugins.get(source_type)
        if not plugin:
            raise RuntimeError(f"No plugin available for {source_type}.")
        if job_context:
            job_context.update_progress(0, 1)
            job_context.update_current_item(f"Fetching {source_type} media")
        item = plugin.fetch_item(url)
        if not item:
            raise RuntimeError("No media item could be imported from that URL.")
        if job_context and job_context.is_cancelled:
            return
        song_id = self.collection_manager.save_media_item(item)
        if collection_id:
            self.collection_manager.add_item_to_collection(collection_id, song_id)
        if job_context:
            job_context.update_progress(1, 1)

    def import_local_file_media(self, file_path: str, collection_id: str | None = None, job_context=None) -> None:
        plugin = self.plugins.get("local_folder")
        if not plugin:
            raise RuntimeError("Local file import is not available.")
        if job_context:
            job_context.update_progress(0, 1)
            job_context.update_current_item("Reading local file")
        item = plugin.fetch_item(file_path)
        if not item:
            raise RuntimeError("No media item could be imported from that file.")
        if job_context and job_context.is_cancelled:
            return
        song_id = self.collection_manager.save_media_item(item)
        if collection_id:
            self.collection_manager.add_item_to_collection(collection_id, song_id)
        details = self.collection_manager.get_track_details(song_id)
        if details and not details.get("filepath"):
            self.acquisition_manager.download_media([details], job_context=job_context)
        if job_context:
            job_context.update_progress(1, 1)

    def _acquisition_provider(self, item: dict) -> str | None:
        try:
            acquisition_info = json.loads(item.get("acquisition_info") or "{}")
        except (TypeError, json.JSONDecodeError):
            return None
        return acquisition_info.get("provider")

    def update_existing_media_results(self) -> None:
        flow = self.management_flow
        if not flow:
            return
        query = MediaQuery(text=flow.query.strip() or None, archived=False, limit=200)
        flow.results = self.collection_manager.search_media(query)
        flow.selected_index = 0
        flow.anchor_index = 0
        flow.selected_indices = set()

    def youtube_id_from_item(self, item: dict) -> str | None:
        youtube_id = item.get("youtube_id")
        if youtube_id:
            return str(youtube_id)
        try:
            acquisition_info = json.loads(item.get("acquisition_info") or "{}")
        except (TypeError, json.JSONDecodeError):
            acquisition_info = {}
        youtube_id = acquisition_info.get("youtube_id")
        if youtube_id:
            return str(youtube_id)
        direct_url = str(acquisition_info.get("direct_url") or item.get("external_url") or "")
        match = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{6,})", direct_url)
        return match.group(1) if match else None

    def youtube_radio_seed_results(self, text: str) -> list[dict]:
        query = MediaQuery(
            status_groups=["matched", "downloaded"],
            archived=False,
        )
        items = self.collection_manager.search_media(query)
        results = [item for item in items if self.youtube_id_from_item(item)]
        text = text.strip()
        if text:
            results.sort(key=lambda item: _fuzzy_media_score(text, item), reverse=True)
        return results

    def update_youtube_radio_seed_results(self, flow) -> None:
        flow.source_results = self.youtube_radio_seed_results(flow.query.strip())
        flow.total_count = len(flow.source_results)
        flow.page_offset = 0
        flow.results = flow.source_results[:max(1, flow.page_size)]
