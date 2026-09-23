import asyncio
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Label, ListItem, ListView

from datetime import datetime, timezone

from core.jobs import JobStatus
from core.models import MediaQuery
from tui.screens.exports import export_status


class HomeScreen(VerticalScroll):
    can_focus = False

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.attention_actions: dict[str, str] = {}
        self.attention_payloads: dict[str, dict[str, Any]] = {}
        self._attention_signature: tuple[tuple[str, str], ...] = ()
        self._refresh_lock = asyncio.Lock()
        self._render_generation = 0

    def compose(self) -> ComposeResult:
        yield Label("Home", classes="section-header")
        with Horizontal(classes="home-dashboard"):
            with Vertical(classes="home-panel"):
                yield Label("Attention", classes="section-header")
                with ListView(id="home-action-list"):
                    pass
            with Vertical(classes="home-panel"):
                with Vertical(classes="home-stack"):
                    yield Label("Snapshot", classes="section-header")
                    yield Label("", id="home-library-summary", classes="mock-text")
                with Vertical(classes="home-stack"):
                    yield Label("Jobs", classes="section-header")
                    yield Label("", id="home-jobs-summary", classes="mock-text")

    async def on_show(self) -> None:
        await self.refresh_summary()

    async def refresh_summary(self) -> None:
        if self._refresh_lock.locked():
            return
        async with self._refresh_lock:
            collections = self.app.collection_manager.get_all_collections()
            track_count = self.app.collection_manager.count_media(MediaQuery())
            managed_exports = self.app.export_manager.get_managed_exports()
            jobs = self.app.job_manager.get_all_jobs()

            review_count = self.app.collection_manager.count_media(MediaQuery(in_review_queue=True, archived=False))
            matched_not_downloaded = self.app.collection_manager.count_media(MediaQuery(matched=True, downloaded=False, archived=False))
            failed_match_count = self.app.collection_manager.count_media(MediaQuery(status_groups=["failed"]))
            discovered_count = self.app.collection_manager.count_media(MediaQuery(status_groups=["discovered"]))
            stale_collections = self._stale_external_collections(collections)
            export_roots = self.app.configured_export_roots()
            stale_exports = [item for item in managed_exports if export_status(item, export_roots)["label"] == "Out Of Date"]
            failed_jobs = len([job for job in jobs if job.status == JobStatus.FAILED])
            missing_audio_features = self.app.audio_analysis_manager.count_missing_features()
            audio_summary = self.app.audio_analysis_manager.get_analysis_summary()

            self.query_one("#home-library-summary", Label).update(
                f"Collections: {len(collections)}\n"
                f"Tracks: {track_count}\n"
                f"Managed Exports: {len(managed_exports)}\n"
                f"Matched Not Downloaded: {matched_not_downloaded}\n"
                f"Failed Matches: {failed_match_count}"
            )

            running = len([job for job in jobs if job.status == JobStatus.RUNNING])
            queued = len([job for job in jobs if job.status == JobStatus.QUEUED])
            latest = jobs[-1].description if jobs else "None"
            self.query_one("#home-jobs-summary", Label).update(
                f"Running: {running}\nQueued: {queued}\nFailed: {failed_jobs}\nLatest: {latest}"
            )

            await self.render_attention(
                review_count,
                matched_not_downloaded,
                failed_match_count,
                discovered_count,
                stale_collections,
                stale_exports,
                failed_jobs,
                missing_audio_features,
                audio_summary.analyzed_count,
                audio_summary.failed_count,
            )

    def _stale_external_collections(self, collections: list[dict]) -> list[dict]:
        threshold_days = self.app.settings.get("collections.stale_refresh_days", 7)
        try:
            threshold_days = int(threshold_days)
        except (TypeError, ValueError):
            threshold_days = 7
        now = datetime.now(timezone.utc)
        stale = []
        for collection in collections:
            source_type = str(collection.get("source_type") or "").lower()
            if source_type == "manual":
                continue
            last_sync = _parse_iso(collection.get("last_sync"))
            if not last_sync or (now - last_sync).days >= threshold_days:
                stale.append(collection)
        return stale

    async def render_attention(
        self,
        review_count: int,
        matched_not_downloaded: int,
        failed_match_count: int,
        discovered_count: int,
        stale_collections: list[dict],
        stale_exports: list[dict],
        failed_jobs: int,
        missing_audio_features: int,
        analyzed_audio_features: int,
        failed_audio_features: int,
    ) -> None:
        list_view = self.query_one("#home-action-list", ListView)
        previous_index = list_view.index or 0
        rows: list[tuple[str, str, dict[str, Any]]] = []

        if review_count:
            rows.append((f"Review Items: {review_count}", "review", {}))
        if failed_match_count:
            rows.append((f"Failed Matches: {failed_match_count}", "failed_matches", {}))
        if discovered_count:
            rows.append((f"Match Discovered Tracks: {discovered_count}", "match_discovered", {}))
        if matched_not_downloaded:
            rows.append((f"Download Ready Tracks: {matched_not_downloaded}", "download_ready", {}))
        if stale_collections:
            rows.append((f"Refresh External Collections: {len(stale_collections)}", "refresh_stale_collections", {"collections": stale_collections}))
        if stale_exports:
            rows.append((f"Update Stale Exports: {len(stale_exports)}", "update_stale_exports", {"exports": stale_exports}))
        if missing_audio_features:
            rows.append((f"Analyze Audio Features: {missing_audio_features}", "analyze_audio_features", {}))
        if analyzed_audio_features or failed_audio_features or missing_audio_features:
            rows.append(("Audio Analysis Summary", "audio_analysis_summary", {}))
        if failed_jobs:
            rows.append((f"Failed Jobs: {failed_jobs}", "jobs", {}))
        if not rows:
            rows.append(("All clear", "none", {}))

        signature = tuple((label, action) for label, action, _payload in rows)
        if signature == self._attention_signature and len(list_view.children) == len(rows):
            return

        await list_view.clear()
        self._render_generation += 1
        generation = self._render_generation
        self.attention_actions = {}
        self.attention_payloads = {}
        self._attention_signature = signature

        for idx, (label, action, payload) in enumerate(rows):
            item_id = f"home-attention-{generation}-{idx}"
            self.attention_actions[item_id] = action
            self.attention_payloads[item_id] = payload
            await list_view.mount(ListItem(Label(label), id=item_id))

        if list_view.children:
            list_view.index = max(0, min(previous_index, len(list_view.children) - 1))
            list_view.focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        item_id = event.item.id or ""
        action = self.attention_actions.get(item_id)
        payload = self.attention_payloads.get(item_id, {})
        if action == "review":
            self.app.run_home_attention_action("review", payload)
        elif action == "match_discovered":
            self.app.run_home_attention_action("match_discovered", payload)
        elif action == "download_ready":
            self.app.run_home_attention_action("download_ready", payload)
        elif action == "failed_matches":
            self.app.run_home_attention_action("failed_matches", payload)
        elif action == "refresh_stale_collections":
            self.app.run_home_attention_action("refresh_stale_collections", payload)
        elif action == "update_stale_exports":
            self.app.run_home_attention_action("update_stale_exports", payload)
        elif action == "analyze_audio_features":
            self.app.run_home_attention_action("analyze_audio_features", payload)
        elif action == "audio_analysis_summary":
            self.app.run_home_attention_action("audio_analysis_summary", payload)
        elif action == "jobs":
            self.app.action_navigate("jobs")


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
