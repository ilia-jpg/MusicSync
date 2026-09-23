import copy
import asyncio
import re
from datetime import datetime, timezone
from time import monotonic
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from pathlib import Path

from textual.widgets import Label, ListView, ListItem, ContentSwitcher, Rule, DataTable
from textual.timer import Timer
from textual import events
from rich.text import Text

from core.acquisition import AcquisitionManager
from core.audio_analysis import AudioAnalysisManager, FEATURE_PRESENTATION, IMPACT_PRESENTATION
from core.collections import CollectionManager
from core.circuits import CircuitManager
from core.exports import ExportManager
from core.jobs import JobManager, JobStatus, JobType
from core.library import LibraryManager
from core.matching import MatchingEngine
from core.models import MediaQuery
from core.review import ReviewManager
from core.settings import SettingsManager
from infrastructure.config import ConfigManager
from infrastructure.database import DatabaseManager
from plugins.spotify import SpotifySourcePlugin
from plugins.youtube import YouTubeSourcePlugin
from plugins.billboard import BillboardSourcePlugin
from plugins.local_files import LocalFolderSourcePlugin
from tui.command_workflow import CommandWorkflowMixin
from tui.collection_workflow import CollectionWorkflowMixin
from tui.exports_workflow import ExportWorkflowMixin
from tui.circuits_workflow import CircuitsWorkflowMixin
from tui.flows.management import ManagementFlow
from tui.flows.review import ReviewSession
from tui.import_workflow import ImportWorkflowMixin
from tui.input_workflow import InputWorkflowMixin
from tui.job_workflow import JobWorkflowMixin
from tui.media_workflow import MediaWorkflowMixin
from tui.review_workflow import ReviewWorkflowMixin
from tui.screens.exports import ExportDetailsScreen, ExportsScreen, export_needs_attention, export_status
from tui.screens.circuits import CircuitCirculationPreviewScreen, CircuitDetailsScreen, CircuitFeedbackDebugScreen, CircuitTrackFeedbackScreen, CircuitsScreen, feedback_icon
from tui.screens.home import HomeScreen
from tui.screens.jobs import JobDetailsScreen, JobsScreen
from tui.screens.management import ManagementFlowScreen
from tui.screens.placeholder import PlaceholderScreen
from tui.settings_workflow import SettingsWorkflowMixin
from tui.screens.settings import SettingsScreen
from tui.screens.vibe_editor import VibeEditorScreen, VibePreviewScreen
from tui.vibe_workflow import VIBE_FEATURE_BY_FIELD, VibeWorkflowMixin
from tui.widgets.list_items import FilterOption, FilterText

# --- Global Helpers ---

CUSTOM_URL_CANDIDATE_ID = "review-custom-url"
DEFAULT_MEDIA_LIST_PAGE_SIZE = 20
MEDIA_LIST_ROW_HEIGHT = 1

DEFAULT_FILTER_UI_STATE = {
    "status_discovered": True,
    "status_matched": True,
    "status_failed": True,
    "status_downloaded": True,
    "status_review": True,
    "feedback_liked": True,
    "feedback_neutral": True,
    "feedback_disliked": True,
    "type_music": True,
    "type_podcast": True,
    "type_audiobook": True,
    "flag_archived": False,
    "flag_file_missing": False,
    "flag_orphaned": False,
    "audio_analyzed": False,
    "audio_failed": False,
    "audio_missing": False,
    "audio_stale": False,
    "text_contains": "",
}

STATUS_FILTER_GROUPS = {
    "status_discovered": "discovered",
    "status_matched": "matched",
    "status_failed": "failed",
    "status_downloaded": "downloaded",
    "status_review": "review",
    "flag_archived": "archived",
}

MEDIA_TYPE_FILTERS = {
    "type_music": "music",
    "type_podcast": "podcast",
    "type_audiobook": "audiobook",
}

FEEDBACK_FILTERS = {
    "feedback_liked": "liked",
    "feedback_neutral": "neutral",
    "feedback_disliked": "disliked",
}

AUDIO_ANALYSIS_FILTERS = {
    "audio_analyzed": "analyzed",
    "audio_failed": "failed",
    "audio_missing": "missing",
    "audio_stale": "stale",
}

AUDIO_FEATURE_FILTER_TRAITS = [
    ("tempo_bpm", FEATURE_PRESENTATION["tempo_bpm"]),
    ("tempo_variability", FEATURE_PRESENTATION["tempo_variability"]),
    ("onset_density", FEATURE_PRESENTATION["onset_density"]),
    ("energy_mean", FEATURE_PRESENTATION["energy_mean"]),
    ("impact", IMPACT_PRESENTATION),
    ("spectral_brightness", FEATURE_PRESENTATION["spectral_brightness"]),
    ("spectral_flatness", FEATURE_PRESENTATION["spectral_flatness"]),
    ("instrumentalness", FEATURE_PRESENTATION["instrumentalness"]),
]
AUDIO_FEATURE_FILTER_KEYS = {
    f"feature_{field}_{score}": (field, score)
    for field, presentation in AUDIO_FEATURE_FILTER_TRAITS
    for score, _label in enumerate(presentation["labels"], 1)
}
for feature_key in AUDIO_FEATURE_FILTER_KEYS:
    DEFAULT_FILTER_UI_STATE[feature_key] = False


def fuzzy_score(query: str, text: str) -> float:
    """Ranks an item against a query. No thresholds; everything gets a score."""
    if not query: return 1000.0
    q = query.lower()
    t = text.lower()
    
    if q == t: return 10000.0
    if t.startswith(q): return 5000.0 - len(t)
    if q in t: return 1000.0 - len(t)
    
    # Subsequence matching
    score = 0
    q_i = 0
    for char in t:
        if q_i < len(q) and char == q[q_i]:
            score += 10
            q_i += 1
            
    if q_i == len(q):
        return 100.0 + score - len(t)
    
    return float(score - len(t))


def fuzzy_media_score(query: str, item: dict) -> float:
    title_score = fuzzy_score(query, item.get("title", ""))
    artist_score = fuzzy_score(query, item.get("artist", ""))
    return title_score + (artist_score * 0.5)


SORT_ALIASES = {
    "track": "title",
    "title": "title",
    "name": "name",
    "artist": "artist",
    "duration": "duration_ms",
    "source": "source_type",
    "status": "status",
    "type": "media_type",
    "media": "media_type",
    "downloaded": "downloaded",
    "preference": "preference",
    "pref": "preference",
    "love": "preference",
    "loved": "preference",
    "boo": "preference",
    "booed": "preference",
    "created": "created_at",
    "updated": "updated_at",
    "refreshed": "last_sync",
    "count": "track_count",
    "tracks": "track_count",
    "tempo": "tempo_bpm",
    "tempo_bpm": "tempo_bpm",
    "tempo_raw": "tempo_raw_bpm",
    "raw_tempo": "tempo_raw_bpm",
    "tempo_raw_bpm": "tempo_raw_bpm",
    "tempo_alt": "tempo_alt_bpm",
    "alt_tempo": "tempo_alt_bpm",
    "tempo_alt_bpm": "tempo_alt_bpm",
    "tempo_variability": "tempo_variability",
    "variability": "tempo_variability",
    "stability": "tempo_variability",
    "onset": "onset_density",
    "onset_density": "onset_density",
    "activity": "onset_density",
    "intensity": "energy_mean",
    "fullness": "energy_mean",
    "weight": "energy_mean",
    "force": "energy_mean",
    "energy_mean": "energy_mean",
    "average_energy": "energy_mean",
    "dynamics": "impact",
    "dynamic": "impact",
    "energy": "energy_mean",
    "power": "energy_mean",
    "impact": "impact",
    "energy_p90": "energy_p90",
    "peak_energy": "energy_p90",
    "brightness": "spectral_brightness",
    "tone": "spectral_brightness",
    "spectral_brightness": "spectral_brightness",
    "flatness": "spectral_flatness",
    "texture": "spectral_flatness",
    "spectral_flatness": "spectral_flatness",
    "vocals": "instrumentalness",
    "vocal": "instrumentalness",
    "vocalness": "instrumentalness",
    "instrumental": "instrumentalness",
    "instrumentalness": "instrumentalness",
    "peak": "normalized_peak",
    "normalized_peak": "normalized_peak",
    "key_confidence": "key_confidence",
    "key": "key_confidence",
    "harmonic_complexity": "harmonic_complexity",
    "harmony": "harmonic_complexity",
}

SORT_HELP = (
    "Sort options:\n"
    "track, artist, duration, status, downloaded\n"
    "preference\n"
    "tempo, stability, intensity, dynamics, activity\n"
    "tone, texture, vocals, key"
)


def sort_field_from_query(query: str) -> str | None:
    query = query.strip().lower()
    if query.startswith("sort:"):
        query = query[5:].strip()
    key = query.split(maxsplit=1)[0] if query else ""
    return SORT_ALIASES.get(key)


def feedback_display_label(rating: str | None) -> str:
    if rating == "liked":
        return "loved"
    if rating == "disliked":
        return "booed"
    return "neutral"


def sortable_value(item: dict, field: str):
    if field == "downloaded":
        return 0 if item.get("filepath") or item.get("file_path") else 1
    value = item.get(field)
    if field == "status":
        value = item.get("song_status") or value
    if field in ("duration_ms", "track_count"):
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0
    return str(value or "").lower()


def paged_media_query(query: MediaQuery, *, limit: int, offset: int) -> MediaQuery:
    paged = copy.copy(query)
    paged.limit = limit
    paged.offset = offset
    return paged


def media_query_signature(query: MediaQuery) -> tuple[tuple[str, object], ...]:
    values = []
    for key, value in vars(query).items():
        if key in ("limit", "offset"):
            continue
        if isinstance(value, list):
            value = tuple(value)
        values.append((key, value))
    return tuple(values)


def visible_media_page_size(list_view: ListView, row_height: int = MEDIA_LIST_ROW_HEIGHT) -> int:
    for attr_name in ("size", "region", "content_size"):
        value = getattr(list_view, attr_name, None)
        height = getattr(value, "height", None)
        if height:
            try:
                return max(1, int(height) // max(1, row_height))
            except (TypeError, ValueError):
                pass
    return max(1, len(getattr(list_view, "children", [])) or DEFAULT_MEDIA_LIST_PAGE_SIZE)


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def relative_age(value: str | None) -> str:
    dt = parse_iso_datetime(value)
    if not dt:
        return "Never refreshed"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    days = max(0, int((datetime.now(timezone.utc) - dt).total_seconds() // 86400))
    if days == 0:
        return "Refreshed today"
    if days == 1:
        return "Refreshed 1 day ago"
    if days < 14:
        return f"Refreshed {days} days ago"
    weeks = max(1, days // 7)
    return f"Refreshed {weeks} week{'s' if weeks != 1 else ''} ago"


def reveal_list_index(list_view: ListView, index: int) -> None:
    if not (0 <= index < len(list_view.children)):
        return
    item = list_view.children[index]
    for method_name in ("scroll_visible", "scroll_to_widget"):
        method = getattr(item if method_name == "scroll_visible" else list_view, method_name, None)
        if not method:
            continue
        try:
            if method_name == "scroll_to_widget":
                method(item, animate=False)
            else:
                method(animate=False)
            return
        except TypeError:
            try:
                method(item) if method_name == "scroll_to_widget" else method()
                return
            except Exception:
                pass
        except Exception:
            pass

# --- Application Screens ---

class LegacyHomeScreen(VerticalScroll):
    can_focus = False

    def compose(self) -> ComposeResult:
        collections = self.app.collection_manager.get_all_collections()
        media_count = self.app.collection_manager.count_media(MediaQuery())
        review_count = self.app.collection_manager.count_media(MediaQuery(in_review_queue=True, archived=False))
        managed_exports = self.app.export_manager.get_managed_exports()

        with Vertical(classes="home-section"):
            yield Label("Jobs\n────────────────────────", classes="section-header")
            yield Label("", id="home-jobs-summary", classes="mock-text")

        with Vertical(classes="home-section"):
            yield Label("Attention Required\n────────────────────────", classes="section-header")
            yield ListView(
                ListItem(Label(f"Review Items: {review_count}"), id="action-review"),
                ListItem(Label("Export Attention: 0"), id="action-missing-exports"),
                id="home-action-list"
            )

        with Vertical(classes="home-section"):
            yield Label("Library Summary\n────────────────────────", classes="section-header")
            yield Label(
                f"Collections: {len(collections)}\nMedia Items: {media_count}\nManaged Exports: {len(managed_exports)}\nExport Attention: 0",
                id="home-library-summary",
                classes="mock-text"
            )

    async def on_show(self) -> None:
        self.refresh_summary()

    def refresh_summary(self) -> None:
        jobs = self.app.job_manager.get_all_jobs()
        counts = {"RUNNING": 0, "QUEUED": 0, "FAILED": 0}
        for job in jobs:
            status_name = job.status.name if hasattr(job.status, "name") else str(job.status)
            if status_name in counts:
                counts[status_name] += 1
        self.query_one("#home-jobs-summary", Label).update(
            f"Running: {counts['RUNNING']}\nQueued: {counts['QUEUED']}\nFailed: {counts['FAILED']}"
        )
        collections = self.app.collection_manager.get_all_collections()
        media_count = self.app.collection_manager.count_media(MediaQuery())
        managed_exports = self.app.export_manager.get_managed_exports()
        export_roots = self.app.configured_export_roots()
        attention_count = sum(1 for item in managed_exports if export_needs_attention(export_status(item, export_roots)))
        try:
            self.query_one("#action-missing-exports", ListItem).query_one(Label).update(f"Export Attention: {attention_count}")
            self.query_one("#home-library-summary", Label).update(
                f"Collections: {len(collections)}\nMedia Items: {media_count}\nManaged Exports: {len(managed_exports)}\nExport Attention: {attention_count}"
            )
        except Exception:
            pass

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop() 
        action_id = event.item.id
        if action_id == "action-review":
            self.app.show_status("Would open Review Session")
        elif action_id == "action-missing-exports":
            self.app.action_navigate("exports")


class CollectionsScreen(Vertical):
    can_focus = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._needs_refresh = True
        self._base_data = []
        self._display_data = []
        self._display_rows = []
        self._find_backup = []
        self._has_ordering_backup = False
        self._render_generation = 0

    def compose(self) -> ComposeResult:
        yield Label("Collections", id="collections-header", classes="section-header")
        yield Label("Filter: None\n", classes="filter-indicator")
        with ListView(id="collection-list"):
            pass

    async def on_show(self) -> None:
        if self._needs_refresh:
            await self.refresh_list()
            self._needs_refresh = False

    async def refresh_list(self) -> None:
        self.app.show_status("Status: Loading", persistent=True)
        show_archived = getattr(self.app, "show_archived_collections", False)
        collections = self.app.collection_manager.get_all_collections(only_archived=show_archived)
        self._base_data = collections
        self._display_data = list(collections)
        try:
            self.app.query_one("#collection_details", CollectionDetailsScreen)._needs_refresh = True
        except Exception:
            pass
        title = "Archived Collections" if show_archived else "Collections"
        self.query_one("#collections-header", Label).update(f"{title} ({len(collections)})")
        await self.render_list(selected_index=self.get_list_index())
        self.app.update_current_context_actions()
        self.app.show_status("Status: Ready")

    def grouped_rows(self) -> list[dict]:
        rows = []
        collections_by_group: dict[str | None, list[dict]] = {None: []}
        for group in self.app.collection_manager.get_collection_groups():
            collections_by_group[group.get("group_id")] = []
        for collection in self._display_data:
            collections_by_group.setdefault(collection.get("group_id"), []).append(collection)

        group_defs = [
            {
                "group_id": None,
                "name": "Ungrouped",
                "collapsed": self.app.collection_manager.get_ungrouped_collapsed(),
            },
            *self.app.collection_manager.get_collection_groups(),
        ]
        for group in group_defs:
            group_id = group.get("group_id")
            collections = sorted(collections_by_group.get(group_id, []), key=lambda item: str(item.get("name") or "").lower())
            collapsed = bool(group.get("collapsed"))
            rows.append(
                {
                    "type": "group",
                    "group_id": group_id,
                    "name": group.get("name") or "Ungrouped",
                    "collapsed": collapsed,
                    "collection_count": len(collections),
                }
            )
            if collapsed:
                continue
            for collection in collections:
                rows.append({"type": "collection", "collection": collection})
        return rows

    async def render_list(self, selected_index: int | None = None) -> None:
        self._render_generation += 1
        generation = self._render_generation
        list_view = self.query_one("#collection-list", ListView)
        if selected_index is None:
            selected_index = list_view.index or 0
        self.reset_list_scroll(list_view)
        await list_view.clear()
        self._display_rows = self.grouped_rows()
        for row in self._display_rows:
            if generation != self._render_generation:
                return
            if row["type"] == "group":
                marker = "+" if row["collapsed"] else "-"
                count = row["collection_count"]
                await list_view.mount(
                    ListItem(
                        Horizontal(
                            Label(f"{marker} {row['name']}", classes="list-primary"),
                            Label(f"{count} collection{'s' if count != 1 else ''}", classes="list-secondary"),
                            classes="list-row"
                        ),
                        id=f"group-{row['group_id'] or 'ungrouped'}"
                    )
                )
                continue

            collection = row["collection"]
            secondary = str(collection.get('track_count', 0))
            if str(collection.get("source_type") or "").lower() == "vibe":
                secondary = f"{secondary} | Vibe"
            if collection.get("last_sync"):
                secondary = f"{secondary} | {relative_age(collection.get('last_sync'))}"
            await list_view.mount(
                ListItem(
                    Horizontal(
                        Label(f"    {collection['name']}", classes="list-primary"),
                        Label(secondary, classes="list-secondary"),
                        classes="list-row"
                    ),
                    id=f"collection-{collection['collection_id']}"
                )
            )
        if generation != self._render_generation:
            return
        if list_view.children:
            target = max(0, min(selected_index, len(list_view.children) - 1))
            list_view.index = target
            list_view.focus()
            self.reset_list_scroll(list_view) if target == 0 else reveal_list_index(list_view, target)
            await asyncio.sleep(0)
            self.reset_list_scroll(list_view) if target == 0 else reveal_list_index(list_view, target)

    def reset_list_scroll(self, list_view: ListView) -> None:
        for attr_name in ("scroll_y", "scroll_target_y", "_scroll_y", "_scroll_target_y"):
            try:
                setattr(list_view, attr_name, 0)
            except Exception:
                pass
        for method_name in ("scroll_home", "scroll_to"):
            method = getattr(list_view, method_name, None)
            if not method:
                continue
            try:
                if method_name == "scroll_to":
                    method(y=0, animate=False)
                else:
                    method(animate=False)
                return
            except TypeError:
                try:
                    method()
                    return
                except Exception:
                    pass
            except Exception:
                pass

    # --- Searchable Interface ---
    def get_list_index(self):
        lv = self.query_one("#collection-list", ListView)
        return lv.index or 0

    def get_selected_collection_id(self) -> str | None:
        lv = self.query_one("#collection-list", ListView)
        row_id = self.selected_list_item_id(lv)
        if isinstance(row_id, str) and row_id.startswith("collection-"):
            return row_id.split("-", 1)[1]
        return None

    def get_selected_group_id(self) -> str | None:
        lv = self.query_one("#collection-list", ListView)
        row_id = self.selected_list_item_id(lv)
        if not (isinstance(row_id, str) and row_id.startswith("group-")):
            return None
        group_id = row_id.split("-", 1)[1]
        return None if group_id == "ungrouped" else group_id

    def get_selected_group(self) -> dict | None:
        lv = self.query_one("#collection-list", ListView)
        row_id = self.selected_list_item_id(lv)
        if not (isinstance(row_id, str) and row_id.startswith("group-")):
            return None
        group_id = row_id.split("-", 1)[1]
        if group_id == "ungrouped":
            return {
                "group_id": None,
                "name": "Ungrouped",
                "collapsed": self.app.collection_manager.get_ungrouped_collapsed(),
            }
        return next((group for group in self.app.collection_manager.get_collection_groups() if group.get("group_id") == group_id), None)

    def selected_row_kind(self) -> str | None:
        lv = self.query_one("#collection-list", ListView)
        row_id = self.selected_list_item_id(lv)
        if isinstance(row_id, str) and row_id.startswith("group-"):
            return "group"
        if isinstance(row_id, str) and row_id.startswith("collection-"):
            return "collection"
        return None

    def get_selected_item(self) -> dict | None:
        collection_id = self.get_selected_collection_id()
        if collection_id:
            return next((item for item in self._display_data if item.get("collection_id") == collection_id), None)
        return None

    def selected_list_item_id(self, list_view: ListView) -> str | None:
        for attr in ("highlighted_child", "highlighted"):
            item = getattr(list_view, attr, None)
            item_id = getattr(item, "id", None)
            if item_id:
                return item_id
        index = list_view.index or 0
        if 0 <= index < len(list_view.children):
            return getattr(list_view.children[index], "id", None)
        return None

    def set_list_index(self, idx):
        lv = self.query_one("#collection-list", ListView)
        if lv.children:
            target = max(0, min(idx, len(lv.children) - 1))
            lv.index = target
            reveal_list_index(lv, target)

    def index_for_collection_id(self, collection_id: str) -> int | None:
        for idx, row in enumerate(self._display_rows):
            if row.get("type") == "collection" and row.get("collection", {}).get("collection_id") == collection_id:
                return idx
        return None

    def save_original_ordering(self):
        if not self._has_ordering_backup:
            self._find_backup = list(self._display_data)
            self._has_ordering_backup = True

    async def restore_original_ordering(self):
        self._display_data = list(self._find_backup)
        self._has_ordering_backup = False
        await self.render_list(selected_index=0)

    async def execute_jump(self, query: str):
        if not query: return
        q = query.lower()
        for i, row in enumerate(self._display_rows):
            name = row["name"] if row["type"] == "group" else row["collection"].get("name", "")
            if name.lower().startswith(q):
                self.set_list_index(i)
                return

    async def execute_find(self, query: str):
        if not query:
            self._display_data = list(self._find_backup)
        else:
            self._display_data.sort(key=lambda x: fuzzy_score(query, x['name']), reverse=True)
        await self.render_list(selected_index=0)

    async def execute_sort(self, query: str) -> bool:
        sort_field = sort_field_from_query(query)
        if not sort_field or sort_field == "preference":
            return False
        self._display_data.sort(key=lambda x: sortable_value(x, sort_field))
        await self.render_list(selected_index=0)
        return True

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        item_id = getattr(event.item, 'id', None)
        if isinstance(item_id, str) and item_id.startswith("group-"):
            group = self.get_selected_group()
            if group is None:
                return
            group_id = group.get("group_id")
            collapsed = not bool(group.get("collapsed"))
            self.app.collection_manager.set_group_collapsed(group_id, collapsed)
            current_index = self.get_list_index()
            self.app.call_later(lambda: asyncio.create_task(self.render_list(selected_index=current_index)))
            return

        collection_id = item_id.split('-', 1)[1] if isinstance(item_id, str) and item_id.startswith('collection-') else None
        
        if collection_id:
            try:
                self.app.query_one("#collection_details", CollectionDetailsScreen)._needs_refresh = True
            except Exception:
                pass

            self.app.current_collection_id = collection_id
            self.app.current_collection_track_id = None
            self.app.current_collection_track_index = 0
            self.app.open_details_screen("collection_details")


class CollectionDetailsScreen(Vertical):
    can_focus = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._needs_refresh = True
        self._base_data = []
        self._display_data = []
        self._find_backup = []
        self._render_generation = 0
        self._offset = 0
        self._page_size = DEFAULT_MEDIA_LIST_PAGE_SIZE
        self._total_count = 0
        self._active_count = 0
        self._current_query = MediaQuery()
        self._query_signature = None
        self._find_text = ""
        self._saved_query = None
        self._saved_filter_ui_state = None
        self._saved_find_text = ""

    def compose(self) -> ComposeResult:
        with Vertical(classes="details-header"):
            yield Label("", id="collection-details-title", classes="details-title")
            yield Label("", id="collection-details-subtitle", classes="details-subtitle")
            yield Label("", id="collection-details-meta", classes="details-meta")
            yield Label("", id="collection-details-status", classes="details-meta")
            yield Label("", id="collection-details-rules", classes="detail-block")
            yield Label("Filter: Default", id="collection-details-filter", classes="filter-indicator")

        with ListView(id="cd-track-list"):
            pass

    async def on_show(self) -> None:
        if self._needs_refresh:
            await self.refresh_details()
            self._needs_refresh = False

    async def on_resize(self, event: events.Resize) -> None:
        try:
            current = getattr(self.app.query_one(ContentSwitcher), "current", None)
        except Exception:
            return
        if not self._needs_refresh and current == "collection_details":
            await self.refresh_details()

    async def refresh_details(self) -> None:
        self.app.show_status("Status: Loading", persistent=True)
        await asyncio.sleep(0)
        collection_id = getattr(self.app, 'current_collection_id', None)
        collection = None
        tracks = []

        if collection_id:
            collections = self.app.collection_manager.get_all_collections(include_archived=True)
            collection = next((item for item in collections if item['collection_id'] == collection_id), None)
            
            query = copy.copy(self.app.collection_filter)
            query.collection_id = collection_id
            signature = media_query_signature(query)
            if signature != self._query_signature:
                self._offset = 0
                self._query_signature = signature
            self._current_query = query
            self._total_count = self.app.collection_manager.count_media(query)
            track_list = self.query_one("#cd-track-list", ListView)
            self._page_size = visible_media_page_size(track_list)
            self._offset = max(0, min(self._offset, self.last_page_offset()))
            if self._find_text:
                ranked_query = copy.copy(query)
                ranked_query.limit = None
                ranked_query.offset = 0
                ranked = self.app.collection_manager.search_media(ranked_query)
                ranked.sort(key=lambda item: fuzzy_media_score(self._find_text, item), reverse=True)
                tracks = ranked[self._offset:self._offset + self._page_size]
            else:
                tracks = self.app.collection_manager.search_media(
                    paged_media_query(query, limit=self._page_size, offset=self._offset)
                )
            active_query = self.app.active_count_query()
            active_query.collection_id = collection_id
            self._active_count = self.app.collection_manager.count_media(active_query)
        else:
            self._current_query = MediaQuery()
            self._query_signature = None
            self._find_text = ""
            self._total_count = 0
            self._active_count = 0

        self._base_data = tracks
        self._display_data = list(tracks)

        title = collection['name'] if collection else 'Unknown Collection'
        if collection and str(collection.get("source_type") or "").lower() == "vibe":
            subtitle = "Vibe Collection"
        else:
            subtitle = (collection.get('source_type') or 'Collection') if collection else 'Collection'
        status_text = collection.get('status') if collection else None

        self.query_one("#collection-details-title", Label).update(title)
        self.query_one("#collection-details-subtitle", Label).update(subtitle)
        count_text = self.app.filter_count_text(self._total_count, self._active_count, "collection_details")
        if self._total_count > self._page_size:
            start = self._offset + 1 if tracks else 0
            end = self._offset + len(tracks)
            count_text = f"{count_text}, page {self.current_page_number()}/{self.total_page_count()} ({start}-{end})"
        self.query_one("#collection-details-meta", Label).update(count_text)
        
        if status_text:
            self.query_one("#collection-details-status", Label).update(f"Status: {status_text}")
        else:
            self.query_one("#collection-details-status", Label).update("")

        rules_label = self.query_one("#collection-details-rules", Label)
        if collection and str(collection.get("source_type") or "").lower() == "vibe":
            vibe_rule = self.app.collection_manager.get_collection_rule(collection.get("collection_id"))
            if vibe_rule:
                rules_label.update("Rules\n" + self.app.format_vibe_rule_summary(vibe_rule, include_counts=False))
            else:
                rules_label.update("Rules\n  unavailable")
        else:
            rules_label.update("")

        filter_indicator = self.query_one("#collection-details-filter", Label)
        if self.app.is_default_filter("collection_details"):
            filter_indicator.update("Filter: Default\n")
        else:
            filter_indicator.update("Filter Active\n")

        target_track_id = getattr(self.app, 'current_collection_track_id', None)
        target_index = getattr(self.app, 'current_collection_track_index', 0) or 0
        target_offset = self._offset
        for idx, track in enumerate(tracks):
            if track.get('song_id') == target_track_id:
                target_index = idx
                break
        if target_track_id and not any(track.get('song_id') == target_track_id for track in tracks):
            target_index = 0
            target_offset = self.find_item_offset(target_track_id)
            if target_offset != self._offset:
                self._offset = target_offset
                tracks = self.app.collection_manager.search_media(
                    paged_media_query(self._current_query, limit=self._page_size, offset=self._offset)
                )
                self._base_data = tracks
                self._display_data = list(tracks)
                for idx, track in enumerate(tracks):
                    if track.get('song_id') == target_track_id:
                        target_index = idx
                        break

        await self.render_list(selected_index=target_index)
        self.app.update_current_context_actions()
        self.app.show_status("Status: Ready")

    async def render_list(self, selected_index: int | None = None) -> None:
        self._render_generation += 1
        generation = self._render_generation
        track_list = self.query_one("#cd-track-list", ListView)
        if selected_index is None:
            selected_index = track_list.index or 0
        self.reset_list_scroll(track_list)
        await track_list.clear()
        total = len(self._display_data)
        await self.mount_media_rows(track_list, total, "track", generation)
        if generation != self._render_generation:
            return
        if track_list.children:
            target = max(0, min(selected_index, len(track_list.children) - 1))
            track_list.index = target
            track_list.focus()
            self.reset_list_scroll(track_list) if target == 0 else reveal_list_index(track_list, target)
            await asyncio.sleep(0)
            self.reset_list_scroll(track_list) if target == 0 else reveal_list_index(track_list, target)
        if total >= 100:
            self.app.show_status("Status: Ready")

    def last_page_offset(self) -> int:
        if self._total_count <= 0:
            return 0
        return max(0, ((self._total_count - 1) // self._page_size) * self._page_size)

    def current_page_number(self) -> int:
        return (self._offset // self._page_size) + 1

    def total_page_count(self) -> int:
        if self._total_count <= 0:
            return 1
        return ((self._total_count - 1) // self._page_size) + 1

    async def go_to_offset(self, offset: int) -> bool:
        target = max(0, min(offset, self.last_page_offset()))
        if target == self._offset:
            return False
        self._offset = target
        await self.refresh_details()
        return True

    async def next_page(self) -> bool:
        return await self.go_to_offset(self._offset + self._page_size)

    async def previous_page(self) -> bool:
        return await self.go_to_offset(self._offset - self._page_size)

    def find_item_offset(self, song_id: str) -> int:
        probe = copy.copy(self._current_query)
        probe.limit = None
        probe.offset = 0
        for idx, item in enumerate(self.app.collection_manager.search_media(probe)):
            if item.get("song_id") == song_id:
                return (idx // self._page_size) * self._page_size
        return self._offset

    async def mount_media_rows(self, list_view: ListView, total: int, row_id_prefix: str, generation: int, batch_size: int = 75) -> None:
        for start in range(0, total, batch_size):
            if generation != self._render_generation:
                return
            batch = [
                ListItem(
                    Label(self.app.format_media_list_row(item), classes="media-row-text"),
                    id=f"{row_id_prefix}-{item['song_id']}",
                )
                for item in self._display_data[start:start + batch_size]
            ]
            try:
                await list_view.mount(*batch)
            except TypeError:
                for row in batch:
                    await list_view.mount(row)
            loaded = min(start + len(batch), total)
            if total >= 100 and generation == self._render_generation:
                self.app.show_status(f"Status: Loading {loaded} of {total} items", persistent=True)
            await asyncio.sleep(0)

    def reset_list_scroll(self, list_view: ListView) -> None:
        for attr_name in ("scroll_y", "scroll_target_y", "_scroll_y", "_scroll_target_y"):
            try:
                setattr(list_view, attr_name, 0)
            except Exception:
                pass
        for method_name in ("scroll_home", "scroll_to"):
            method = getattr(list_view, method_name, None)
            if not method:
                continue
            try:
                if method_name == "scroll_to":
                    method(y=0, animate=False)
                else:
                    method(animate=False)
                return
            except TypeError:
                try:
                    method()
                    return
                except Exception:
                    pass
            except Exception:
                pass

    def refresh_selection_labels(self) -> None:
        track_list = self.query_one("#cd-track-list", ListView)
        items_by_id = {item.get("song_id"): item for item in self._display_data}
        for item in track_list.children:
            try:
                item_id = getattr(item, "id", None)
                song_id = item_id.split("-", 1)[1] if isinstance(item_id, str) and item_id.startswith("track-") else None
                data = items_by_id.get(song_id)
                if data:
                    item.query(Label).first().update(self.app.format_media_list_row(data))
            except Exception:
                pass

    # --- Searchable Interface ---
    def get_list_index(self):
        lv = self.query_one("#cd-track-list", ListView)
        return lv.index or 0

    def get_selected_song_id(self) -> str | None:
        lv = self.query_one("#cd-track-list", ListView)
        row_id = self.selected_list_item_id(lv)
        if isinstance(row_id, str) and row_id.startswith("track-"):
            return row_id.split("-", 1)[1]
        return None

    def get_selected_item(self) -> dict | None:
        song_id = self.get_selected_song_id()
        if song_id:
            return next((item for item in self._display_data if item.get("song_id") == song_id), None)
        index = self.get_list_index()
        return self._display_data[index] if 0 <= index < len(self._display_data) else None

    def index_for_song_id(self, song_id: str | None) -> int | None:
        if not song_id:
            return None
        for index, item in enumerate(self._display_data):
            if item.get("song_id") == song_id:
                return index
        return None

    def selected_list_item_id(self, list_view: ListView) -> str | None:
        for attr in ("highlighted_child", "highlighted"):
            item = getattr(list_view, attr, None)
            item_id = getattr(item, "id", None)
            if item_id:
                return item_id
        index = list_view.index or 0
        if 0 <= index < len(list_view.children):
            return getattr(list_view.children[index], "id", None)
        return None

    def set_list_index(self, idx):
        lv = self.query_one("#cd-track-list", ListView)
        if lv.children:
            target = max(0, min(idx, len(lv.children) - 1))
            lv.index = target
            reveal_list_index(lv, target)

    def save_original_ordering(self):
        self._find_backup = list(self._display_data)
        if self._saved_query is None:
            self._saved_query = copy.copy(self.app.collection_filter)
            self._saved_filter_ui_state = dict(self.app.collection_filter_ui_state)
            self._saved_find_text = self._find_text

    async def restore_original_ordering(self):
        if self._saved_query is not None:
            self.app.collection_filter = copy.copy(self._saved_query)
            self._saved_query = None
        if self._saved_filter_ui_state is not None:
            self.app.collection_filter_ui_state = dict(self._saved_filter_ui_state)
            self._saved_filter_ui_state = None
        self._find_text = self._saved_find_text
        self._saved_find_text = ""
        self._offset = 0
        await self.refresh_details()

    async def execute_jump(self, query: str):
        if not query: return
        probe = copy.copy(self._current_query)
        probe.limit = None
        probe.offset = 0
        q = query.lower()
        for i, track in enumerate(self.app.collection_manager.search_media(probe)):
            if track.get('title', '').lower().startswith(q):
                self._offset = (i // self._page_size) * self._page_size
                await self.refresh_details()
                self.set_list_index(i - self._offset)
                return

    async def execute_find(self, query: str):
        self._find_text = query.strip()
        self._offset = 0
        await self.refresh_details()

    async def execute_sort(self, query: str) -> bool:
        sort_field = sort_field_from_query(query)
        if not sort_field:
            return False
        self.app.collection_filter.sort_by = sort_field
        self._find_text = ""
        self._offset = 0
        await self.refresh_details()
        return True

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        list_view = getattr(event, 'list_view', None) or self.query_one("#cd-track-list", ListView)
        item_id = getattr(event.item, 'id', None)
        song_id = item_id.split('-', 1)[1] if isinstance(item_id, str) and item_id.startswith('track-') else None
        
        if song_id:
            if getattr(self.app, 'current_media_id', None) != song_id:
                try:
                    self.app.query_one("#media_details", MediaDetailsScreen)._needs_refresh = True
                except Exception: pass
                
            self.app.current_media_id = song_id
            self.app.current_collection_track_id = song_id
            self.app.current_collection_track_index = getattr(list_view, 'index', 0) or 0
            
        self.app.open_details_screen("media_details")


class CollectionHistoryScreen(VerticalScroll):
    can_focus = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._rows = []

    def compose(self) -> ComposeResult:
        yield Label("Collection History", id="collection-history-title", classes="section-header")
        with ListView(id="collection-history-list"):
            pass

    async def on_show(self) -> None:
        await self.refresh_history()

    async def refresh_history(self) -> None:
        collection = self.app.get_selected_collection()
        collection_id = getattr(self.app, "current_collection_id", None) or (collection or {}).get("collection_id")
        title = (collection or {}).get("name", "Collection")
        self.query_one("#collection-history-title", Label).update(f"History: {title}")
        list_view = self.query_one("#collection-history-list", ListView)
        await list_view.clear()
        self._rows = self.app.collection_manager.get_collection_history(collection_id) if collection_id else []
        for idx, row in enumerate(self._rows):
            label = self.app.format_history_row(row)
            await list_view.mount(ListItem(Label(label), id=f"history-{idx}"))
        if list_view.children:
            list_view.index = 0
            list_view.focus()

    def get_list_index(self) -> int:
        return self.query_one("#collection-history-list", ListView).index or 0

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        index = self.get_list_index()
        if 0 <= index < len(self._rows):
            self.app.current_revision_id = self._rows[index].get("revision_id")
            self.app.open_details_screen("collection_snapshot")


class CollectionSnapshotScreen(VerticalScroll):
    can_focus = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._tracks = []

    def compose(self) -> ComposeResult:
        yield Label("Snapshot", id="collection-snapshot-title", classes="section-header")
        with ListView(id="collection-snapshot-list"):
            pass

    async def on_show(self) -> None:
        await self.refresh_snapshot()

    async def refresh_snapshot(self) -> None:
        collection_id = getattr(self.app, "current_collection_id", None)
        revision_id = getattr(self.app, "current_revision_id", None)
        self._tracks = self.app.collection_manager.get_collection_revision_tracks(collection_id, revision_id) if collection_id else []
        title = "Current" if not revision_id else "Snapshot"
        self.query_one("#collection-snapshot-title", Label).update(f"{title} ({len(self._tracks)} tracks)")
        list_view = self.query_one("#collection-snapshot-list", ListView)
        await list_view.clear()
        for track in self._tracks:
            await list_view.mount(
                ListItem(
                    Horizontal(
                        Label(self.app.status_icon(track), classes=f"status-icon {self.app.status_class(track)}"),
                        Label(track.get("title", "Unknown"), classes="track-title"),
                        Label(track.get("artist", "Unknown Artist"), classes="track-artist"),
                        classes="track-row",
                    ),
                    id=f"snapshot-track-{track.get('song_id')}",
                )
            )
        if list_view.children:
            list_view.index = 0
            list_view.focus()


class MediaScreen(Vertical):
    can_focus = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._needs_refresh = True
        self._base_data = []
        self._display_data = []
        self._find_backup = []
        self._render_generation = 0
        self._offset = 0
        self._page_size = DEFAULT_MEDIA_LIST_PAGE_SIZE
        self._total_count = 0
        self._active_count = 0
        self._current_query = MediaQuery()
        self._query_signature = None
        self._find_text = ""
        self._saved_query = None
        self._saved_filter_ui_state = None
        self._saved_find_text = ""

    def compose(self) -> ComposeResult:
        yield Label("Media (0)", id="media-header", classes="section-header")
        yield Label("Filter: Default\n", id="media-filter-indicator", classes="filter-indicator")
        with ListView(id="media-list"):
            pass

    async def on_show(self) -> None:
        if self._needs_refresh:
            await self.refresh_list()
            self._needs_refresh = False

    async def on_resize(self, event: events.Resize) -> None:
        try:
            current = getattr(self.app.query_one(ContentSwitcher), "current", None)
        except Exception:
            return
        if not self._needs_refresh and current == "media":
            await self.refresh_list()

    async def refresh_list(self) -> None:
        self.app.show_status("Status: Loading", persistent=True)
        await asyncio.sleep(0)
        query = copy.copy(self.app.media_filter)
        signature = media_query_signature(query)
        if signature != self._query_signature:
            self._offset = 0
            self._query_signature = signature
        self._current_query = query
        self._total_count = self.app.collection_manager.count_media(query)
        list_view = self.query_one("#media-list", ListView)
        self._page_size = visible_media_page_size(list_view)
        self._offset = max(0, min(self._offset, self.last_page_offset()))
        if self._find_text:
            ranked_query = copy.copy(query)
            ranked_query.limit = None
            ranked_query.offset = 0
            ranked = self.app.collection_manager.search_media(ranked_query)
            ranked.sort(key=lambda item: fuzzy_media_score(self._find_text, item), reverse=True)
            media_items = ranked[self._offset:self._offset + self._page_size]
        else:
            media_items = self.app.collection_manager.search_media(
                paged_media_query(query, limit=self._page_size, offset=self._offset)
            )
        self._active_count = self.app.collection_manager.count_media(self.app.active_count_query())
        
        self._base_data = media_items
        self._display_data = list(media_items)

        count_text = self.app.filter_count_text(self._total_count, self._active_count, "media")
        if self._total_count > self._page_size:
            start = self._offset + 1 if media_items else 0
            end = self._offset + len(media_items)
            count_text = f"{count_text}, page {self.current_page_number()}/{self.total_page_count()} ({start}-{end})"
        self.query_one("#media-header", Label).update(f"Media ({count_text})")

        indicator = self.query_one("#media-filter-indicator", Label)
        if self.app.is_default_filter("media"):
            indicator.update("Filter: Default\n")
        else:
            indicator.update("Filter Active\n")

        selected_index = self.index_for_song_id(getattr(self.app, "current_media_id", None))
        await self.render_list(selected_index=selected_index if selected_index is not None else self.get_list_index())
        self.app.update_current_context_actions()
        self.app.show_status("Status: Ready")

    async def render_list(self, selected_index: int | None = None) -> None:
        self._render_generation += 1
        generation = self._render_generation
        list_view = self.query_one("#media-list", ListView)
        if selected_index is None:
            selected_index = list_view.index or 0
        self.reset_list_scroll(list_view)
        await list_view.clear()
        total = len(self._display_data)
        await self.mount_media_rows(list_view, total, "media", generation)
        if generation != self._render_generation:
            return
        if list_view.children:
            target = max(0, min(selected_index, len(list_view.children) - 1))
            list_view.index = target
            list_view.focus()
            self.reset_list_scroll(list_view) if target == 0 else reveal_list_index(list_view, target)
            await asyncio.sleep(0)
            self.reset_list_scroll(list_view) if target == 0 else reveal_list_index(list_view, target)
        if total >= 100:
            self.app.show_status("Status: Ready")

    def last_page_offset(self) -> int:
        if self._total_count <= 0:
            return 0
        return max(0, ((self._total_count - 1) // self._page_size) * self._page_size)

    def current_page_number(self) -> int:
        return (self._offset // self._page_size) + 1

    def total_page_count(self) -> int:
        if self._total_count <= 0:
            return 1
        return ((self._total_count - 1) // self._page_size) + 1

    async def go_to_offset(self, offset: int) -> bool:
        target = max(0, min(offset, self.last_page_offset()))
        if target == self._offset:
            return False
        self._offset = target
        await self.refresh_list()
        return True

    async def next_page(self) -> bool:
        return await self.go_to_offset(self._offset + self._page_size)

    async def previous_page(self) -> bool:
        return await self.go_to_offset(self._offset - self._page_size)

    async def mount_media_rows(self, list_view: ListView, total: int, row_id_prefix: str, generation: int, batch_size: int = 75) -> None:
        for start in range(0, total, batch_size):
            if generation != self._render_generation:
                return
            batch = [
                ListItem(
                    Label(self.app.format_media_list_row(item), classes="media-row-text"),
                    id=f"{row_id_prefix}-{item['song_id']}",
                )
                for item in self._display_data[start:start + batch_size]
            ]
            try:
                await list_view.mount(*batch)
            except TypeError:
                for row in batch:
                    await list_view.mount(row)
            loaded = min(start + len(batch), total)
            if total >= 100 and generation == self._render_generation:
                self.app.show_status(f"Status: Loading {loaded} of {total} items", persistent=True)
            await asyncio.sleep(0)

    def reset_list_scroll(self, list_view: ListView) -> None:
        for attr_name in ("scroll_y", "scroll_target_y", "_scroll_y", "_scroll_target_y"):
            try:
                setattr(list_view, attr_name, 0)
            except Exception:
                pass
        for method_name in ("scroll_home", "scroll_to"):
            method = getattr(list_view, method_name, None)
            if not method:
                continue
            try:
                if method_name == "scroll_to":
                    method(y=0, animate=False)
                else:
                    method(animate=False)
                return
            except TypeError:
                try:
                    method()
                    return
                except Exception:
                    pass
            except Exception:
                pass

    def refresh_selection_labels(self) -> None:
        list_view = self.query_one("#media-list", ListView)
        items_by_id = {item.get("song_id"): item for item in self._display_data}
        for item in list_view.children:
            try:
                item_id = getattr(item, "id", None)
                song_id = item_id.split("-", 1)[1] if isinstance(item_id, str) and item_id.startswith("media-") else None
                data = items_by_id.get(song_id)
                if data:
                    item.query(Label).first().update(self.app.format_media_list_row(data))
            except Exception:
                pass

    # --- Searchable Interface ---
    def get_list_index(self):
        lv = self.query_one("#media-list", ListView)
        return lv.index or 0

    def get_selected_song_id(self) -> str | None:
        lv = self.query_one("#media-list", ListView)
        row_id = self.selected_list_item_id(lv)
        if isinstance(row_id, str) and row_id.startswith("media-"):
            return row_id.split("-", 1)[1]
        return None

    def get_selected_item(self) -> dict | None:
        song_id = self.get_selected_song_id()
        if song_id:
            return next((item for item in self._display_data if item.get("song_id") == song_id), None)
        index = self.get_list_index()
        return self._display_data[index] if 0 <= index < len(self._display_data) else None

    def index_for_song_id(self, song_id: str | None) -> int | None:
        if not song_id:
            return None
        for index, item in enumerate(self._display_data):
            if item.get("song_id") == song_id:
                return index
        return None

    def selected_list_item_id(self, list_view: ListView) -> str | None:
        for attr in ("highlighted_child", "highlighted"):
            item = getattr(list_view, attr, None)
            item_id = getattr(item, "id", None)
            if item_id:
                return item_id
        index = list_view.index or 0
        if 0 <= index < len(list_view.children):
            return getattr(list_view.children[index], "id", None)
        return None

    def set_list_index(self, idx):
        lv = self.query_one("#media-list", ListView)
        if lv.children:
            target = max(0, min(idx, len(lv.children) - 1))
            lv.index = target
            reveal_list_index(lv, target)

    def save_original_ordering(self):
        self._find_backup = list(self._display_data)
        if self._saved_query is None:
            self._saved_query = copy.copy(self.app.media_filter)
            self._saved_filter_ui_state = dict(self.app.media_filter_ui_state)
            self._saved_find_text = self._find_text

    async def restore_original_ordering(self):
        if self._saved_query is not None:
            self.app.media_filter = copy.copy(self._saved_query)
            self._saved_query = None
        if self._saved_filter_ui_state is not None:
            self.app.media_filter_ui_state = dict(self._saved_filter_ui_state)
            self._saved_filter_ui_state = None
        self._find_text = self._saved_find_text
        self._saved_find_text = ""
        self._offset = 0
        await self.refresh_list()

    async def execute_jump(self, query: str):
        if not query: return
        q = query.lower()
        probe = copy.copy(self._current_query)
        probe.limit = None
        probe.offset = 0
        for i, item in enumerate(self.app.collection_manager.search_media(probe)):
            if item.get('title', '').lower().startswith(q):
                self._offset = (i // self._page_size) * self._page_size
                await self.refresh_list()
                self.set_list_index(i - self._offset)
                return

    async def execute_find(self, query: str):
        self._find_text = query.strip()
        self._offset = 0
        await self.refresh_list()

    async def execute_sort(self, query: str) -> bool:
        sort_field = sort_field_from_query(query)
        if not sort_field:
            return False
        self.app.media_filter.sort_by = sort_field
        self._find_text = ""
        self._offset = 0
        await self.refresh_list()
        return True

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        item_id = getattr(event.item, 'id', None)
        song_id = item_id.split('-', 1)[1] if isinstance(item_id, str) and item_id.startswith('media-') else None
        
        if song_id:
            if getattr(self.app, 'current_media_id', None) != song_id:
                try:
                    self.app.query_one("#media_details", MediaDetailsScreen)._needs_refresh = True
                except Exception: pass

            self.app.current_media_id = song_id
            self.app.current_collection_track_id = song_id
            
        self.app.open_details_screen("media_details")


class FilterScreen(VerticalScroll):
    can_focus = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.filter_rule: dict = {}
        self._rule_rows: list[dict] = []
        self.active_column = "left"
        self.pending_rule_target: dict = {}

    def compose(self) -> ComposeResult:
        yield Label("Filter Settings", classes="section-header")
        yield Label("", id="filter-summary", classes="details-meta")
        yield Rule()
        with Horizontal(id="filter-columns"):
            with Vertical(id="filter-left-column", classes="filter-column"):
                yield Label("Filters", classes="filter-column-title")
                with ListView(id="filter-options-list"):
                    pass
            with Vertical(id="filter-right-column", classes="filter-column"):
                yield Label("Audio Rules", classes="filter-column-title")
                with ListView(id="filter-rules-list"):
                    pass

    async def on_show(self) -> None:
        list_view = self.query_one("#filter-options-list", ListView)
        await list_view.clear()
        rules_view = self.query_one("#filter-rules-list", ListView)
        await rules_view.clear()

        context = self.app.active_filter_context
        current_state = self.app.normalize_filter_ui_state(
            self.app.media_filter_ui_state if context == "media" else self.app.collection_filter_ui_state
        )
        self.filter_rule = self.rule_from_state(current_state)
        self._rule_rows = []
        self.pending_rule_target = {}
        self.active_column = "left"

        def val(k): return current_state.get(k, False)
        def tval(k): return current_state.get(k, "")

        await list_view.mount(ListItem(Label("Status:"), disabled=True, classes="filter-heading"))
        await list_view.mount(FilterOption("status_discovered", "Discovered", val("status_discovered")))
        await list_view.mount(FilterOption("status_matched", "Matched", val("status_matched")))
        await list_view.mount(FilterOption("status_failed", "Failed Matches", val("status_failed")))
        await list_view.mount(FilterOption("status_downloaded", "Downloaded", val("status_downloaded")))
        await list_view.mount(FilterOption("status_review", "Review", val("status_review")))
        await list_view.mount(FilterOption("flag_archived", "Archived", val("flag_archived")))

        await list_view.mount(ListItem(Label("Media Type:"), disabled=True, classes="filter-heading"))
        await list_view.mount(FilterOption("type_music", "Music", val("type_music")))
        await list_view.mount(FilterOption("type_podcast", "Podcast", val("type_podcast")))
        await list_view.mount(FilterOption("type_audiobook", "Audiobook", val("type_audiobook")))

        await list_view.mount(ListItem(Label("Preference:"), disabled=True, classes="filter-heading"))
        await list_view.mount(FilterOption("feedback_liked", "Loved", val("feedback_liked")))
        await list_view.mount(FilterOption("feedback_neutral", "Neutral", val("feedback_neutral")))
        await list_view.mount(FilterOption("feedback_disliked", "Booed", val("feedback_disliked")))

        await list_view.mount(ListItem(Label("Only Include:"), disabled=True, classes="filter-heading"))
        await list_view.mount(FilterOption("flag_file_missing", "Only media with missing files", val("flag_file_missing")))
        if context == "media":
            await list_view.mount(FilterOption("flag_orphaned", "Only orphaned media", val("flag_orphaned")))

        await list_view.mount(ListItem(Label("Audio Analysis:"), disabled=True, classes="filter-heading"))
        await list_view.mount(FilterOption("audio_analyzed", "Analyzed", val("audio_analyzed")))
        await list_view.mount(FilterOption("audio_failed", "Failed analysis", val("audio_failed")))
        await list_view.mount(FilterOption("audio_missing", "Missing analysis", val("audio_missing")))
        await list_view.mount(FilterOption("audio_stale", "Stale analysis", val("audio_stale")))

        await list_view.mount(ListItem(Label("Text:"), disabled=True, classes="filter-heading"))
        await list_view.mount(FilterText("text_contains", "Contains", tval("text_contains")))

        await self.refresh_rules_list()
        list_view.index = 1 
        list_view.focus()
        self.refresh_summary()
        self.app.update_current_context_actions()

    def on_key(self, event: events.Key) -> None:
        if self.app.input_mode != "NORMAL":
            return
        key = (event.key or "").lower().replace("_", "+")
        highlighted = self.highlighted_child()

        if key == "left":
            self.focus_column("left")
            event.stop()
        elif key == "right":
            self.focus_column("right")
            event.stop()
        elif key == "escape":
            self.execute_action("cancel")
            event.stop()
        elif isinstance(highlighted, FilterText):
            if key == "backspace":
                highlighted.update_text(highlighted.text_val[:-1])
                self.refresh_summary()
                event.stop()
            elif event.is_printable and event.character:
                highlighted.update_text(highlighted.text_val + event.character)
                self.refresh_summary()
                event.stop()
        elif key == "t":
            self.execute_action("apply")
            event.stop()
        elif key == "c":
            self.execute_action("clear")
            event.stop()
        elif key == "a" and self.active_column == "right":
            self.start_rule_input()
            event.stop()
        elif key == "g" and self.active_column == "right":
            asyncio.create_task(self.add_group_and_rule())
            event.stop()
        elif key == "e" and self.active_column == "right":
            self.start_rule_input(edit=True)
            event.stop()
        elif key == "d" and self.active_column == "right":
            self.delete_selected_rule()
            event.stop()
        elif key == "l" and self.active_column == "right":
            self.loosen_selected_rule()
            event.stop()
        elif key == "enter" and isinstance(highlighted, FilterOption):
            highlighted.toggle()
            self.refresh_summary()
            event.stop()
        elif key == "enter" and self.active_column == "right":
            self.start_rule_input(edit=True)
            event.stop()

    def shift_pressed(self, event: events.Key) -> bool:
        key = (event.key or "").lower().replace("_", "+")
        if "shift" in key:
            return True
        modifiers = getattr(event, "modifiers", None)
        return bool(modifiers and any(str(modifier).lower() == "shift" for modifier in modifiers))

    def focused_list(self) -> ListView:
        target = "#filter-rules-list" if self.active_column == "right" else "#filter-options-list"
        return self.query_one(target, ListView)

    def highlighted_child(self):
        return self.focused_list().highlighted_child

    def text_filter_focused(self) -> bool:
        return isinstance(self.highlighted_child(), FilterText)

    def append_text_filter_character(self, character: str) -> None:
        highlighted = self.highlighted_child()
        if isinstance(highlighted, FilterText):
            highlighted.update_text(highlighted.text_val + character)
            self.refresh_summary()

    def focus_column(self, column: str) -> None:
        self.active_column = "right" if column == "right" else "left"
        list_view = self.focused_list()
        if list_view.children and list_view.index is None:
            list_view.index = 0
        list_view.focus()
        self.app.update_current_context_actions()

    def current_ui_state(self) -> dict:
        state = self.app.default_filter_ui_state()
        list_view = self.query_one("#filter-options-list", ListView)
        for item in list_view.children:
            if isinstance(item, FilterOption):
                state[item.key] = item.val
            elif isinstance(item, FilterText):
                state[item.key] = item.text_val
        if self.app.vibe_rule_has_any_rules(self.filter_rule):
            state["audio_rule"] = copy.deepcopy(self.filter_rule)
        return state

    def rule_from_state(self, state: dict) -> dict:
        stored_rule = state.get("audio_rule")
        if isinstance(stored_rule, dict):
            return copy.deepcopy(stored_rule)
        rules = []
        for field, presentation in AUDIO_FEATURE_FILTER_TRAITS:
            values = [
                score
                for score, _label in enumerate(presentation["labels"], 1)
                if state.get(f"feature_{field}_{score}")
            ]
            if values:
                rules.append(self.app.vibe_rule_item(field, values))
        rule = self.app.empty_vibe_rule()
        rule["required"]["rules"] = rules
        return rule

    async def refresh_rules_list(self) -> None:
        rules_view = self.query_one("#filter-rules-list", ListView)
        selected = rules_view.index or 0
        await rules_view.clear()
        self._rule_rows = self.build_rule_rows()
        for index, row in enumerate(self._rule_rows):
            await rules_view.mount(ListItem(Label(self.format_rule_row(row)), id=f"filter-rule-row-{index}"))
        if rules_view.children:
            rules_view.index = max(0, min(selected, len(rules_view.children) - 1))

    def build_rule_rows(self) -> list[dict]:
        rule = self.filter_rule or self.app.empty_vibe_rule()
        rows = [{"kind": "required_header"}]
        required_rules = rule.get("required", {}).get("rules", [])
        if required_rules:
            for rule_index, item in enumerate(required_rules):
                rows.append({"kind": "rule", "section": "required", "group_index": None, "rule_index": rule_index, "rule": item})
        else:
            rows.append({"kind": "empty", "section": "required", "group_index": None})
        for group_index, group in enumerate(rule.get("groups") or []):
            rows.append({"kind": "group_header", "group_index": group_index})
            group_rules = group.get("rules", [])
            if group_rules:
                for rule_index, item in enumerate(group_rules):
                    rows.append({"kind": "rule", "section": "group", "group_index": group_index, "rule_index": rule_index, "rule": item})
            else:
                rows.append({"kind": "empty", "section": "group", "group_index": group_index})
        return rows

    def format_rule_row(self, row: dict) -> str:
        kind = row.get("kind")
        if kind == "required_header":
            return "Required Rules"
        if kind == "group_header":
            return f"Group {int(row.get('group_index') or 0) + 1}"
        if kind == "empty":
            return "  none"
        if kind == "rule":
            return "  " + self.app.format_vibe_rule_line(row.get("rule") or {})
        return ""

    def selected_rule_row(self) -> dict:
        index = self.query_one("#filter-rules-list", ListView).index or 0
        if 0 <= index < len(self._rule_rows):
            return self._rule_rows[index]
        return {"kind": "required_header"}

    def selected_rule_target(self) -> tuple[str, int | None]:
        row = self.selected_rule_row()
        if row.get("section") == "required" or row.get("kind") == "required_header":
            return "required", None
        group_index = row.get("group_index")
        if group_index is None:
            groups = (self.filter_rule or {}).get("groups") or []
            group_index = max(0, len(groups) - 1)
        return "group", int(group_index)

    def selected_rule_location(self) -> tuple[str, int | None, int] | None:
        row = self.selected_rule_row()
        if row.get("kind") != "rule":
            return None
        return row.get("section"), row.get("group_index"), int(row.get("rule_index") or 0)

    def filter_rule_at(self, section: str, group_index: int | None, rule_index: int) -> dict | None:
        rule = self.filter_rule or self.app.empty_vibe_rule()
        try:
            if section == "required":
                return rule["required"]["rules"][rule_index]
            return rule["groups"][int(group_index or 0)]["rules"][rule_index]
        except (KeyError, IndexError, TypeError, ValueError):
            return None

    def start_rule_input(self, *, edit: bool = False) -> None:
        location = self.selected_rule_location() if edit else None
        if edit and location is None:
            self.app.show_status("Select an audio rule to edit.")
            return
        if location:
            section, group_index, rule_index = location
            existing = self.filter_rule_at(section, group_index, rule_index)
            self.pending_rule_target = {"section": section, "group_index": group_index, "rule_index": rule_index}
            self.app.vibe_pending_rule = copy.deepcopy(existing or {})
        else:
            section, group_index = self.selected_rule_target()
            self.pending_rule_target = {"section": section, "group_index": group_index, "rule_index": None}
            self.app.vibe_pending_rule = {}
        self.app.input_mode = "FILTER_RULE_FEATURE"
        self.app.input_query = ""
        self.app.update_context_actions(self.app.filter_rule_hint_actions())
        self.app.show_status("Feature: _", persistent=True)

    async def commit_rule_input(self) -> None:
        if self.app.input_mode == "FILTER_RULE_FEATURE":
            parsed = self.app.parse_vibe_rule_text(self.app.input_query)
            if parsed:
                await self.apply_filter_rules(parsed)
                return
            field = self.app.resolve_vibe_feature(self.app.input_query)
            if not field:
                self.app.show_status(f"Unknown feature: {self.app.input_query}", persistent=True)
                return
            self.app.vibe_pending_rule["field"] = field
            self.app.input_mode = "FILTER_RULE_LEVEL"
            self.app.input_query = ""
            self.app.update_context_actions(self.app.filter_rule_hint_actions())
            feature_name = VIBE_FEATURE_BY_FIELD[field][0]
            self.app.show_status(f"Feature: {feature_name} is _", persistent=True)
            return
        if self.app.input_mode == "FILTER_RULE_LEVEL":
            field = self.app.vibe_pending_rule.get("field")
            values = self.app.resolve_vibe_levels(field, self.app.input_query)
            if not values:
                parsed = self.app.parse_vibe_rule_text(f"{VIBE_FEATURE_BY_FIELD.get(field, ('',))[0]} is {self.app.input_query}")
                if parsed:
                    values = parsed[0].get("values", [])
            if not values:
                self.app.show_status(f"Unknown level: {self.app.input_query}", persistent=True)
                return
            self.app.vibe_pending_rule["op"] = "bucket_is"
            self.app.vibe_pending_rule["values"] = values
            if field:
                self.app.vibe_pending_rule["display"] = self.app.display_for_vibe_expression(field, self.app.input_query, values)
            await self.apply_filter_rules([copy.deepcopy(self.app.vibe_pending_rule)])

    async def apply_filter_rules(self, rules: list[dict]) -> None:
        rule = self.filter_rule or self.app.empty_vibe_rule()
        target = self.pending_rule_target
        section = target.get("section")
        group_index = target.get("group_index")
        rule_index = target.get("rule_index")
        destination = rule["required"]["rules"] if section == "required" else rule["groups"][int(group_index or 0)]["rules"]
        if rule_index is None:
            for item in rules:
                self.upsert_rule(destination, item)
        else:
            destination[int(rule_index)] = rules[0]
            for item in rules[1:]:
                self.upsert_rule(destination, item)
            self.merge_rules(destination)
        self.filter_rule = rule
        self.pending_rule_target = {}
        self.app.vibe_pending_rule = {}
        self.app.input_mode = "NORMAL"
        self.app.input_query = ""
        await self.refresh_rules_list()
        self.refresh_summary()
        self.app.update_current_context_actions()
        self.app.show_status("Status: Ready")

    async def abort_rule_input(self) -> None:
        self.pending_rule_target = {}
        self.app.vibe_pending_rule = {}
        self.app.input_mode = "NORMAL"
        self.app.input_query = ""
        self.app.update_current_context_actions()
        self.app.show_status("Cancelled.")

    async def update_rule_input_status(self) -> None:
        self.app.update_context_actions(self.app.filter_rule_hint_actions())
        if self.app.input_mode == "FILTER_RULE_FEATURE":
            self.app.show_status(f"Feature: {self.app.input_query}_", persistent=True)
        elif self.app.input_mode == "FILTER_RULE_LEVEL":
            field = self.app.vibe_pending_rule.get("field")
            feature_name = VIBE_FEATURE_BY_FIELD.get(field, ("Feature", {}))[0]
            self.app.show_status(f"Feature: {feature_name} is {self.app.input_query}_", persistent=True)

    def upsert_rule(self, rules: list[dict], new_rule: dict) -> None:
        for existing in rules:
            if existing.get("field") == new_rule.get("field"):
                merged = sorted(set(int(v) for v in existing.get("values", [])) | set(int(v) for v in new_rule.get("values", [])))
                existing["values"] = merged
                existing["op"] = "bucket_is"
                existing.pop("display", None)
                return
        rules.append(new_rule)

    def merge_rules(self, rules: list[dict]) -> None:
        merged: list[dict] = []
        for rule in rules:
            self.upsert_rule(merged, rule)
        rules[:] = merged

    async def add_group_and_rule(self) -> None:
        rule = self.filter_rule or self.app.empty_vibe_rule()
        rule.setdefault("groups", []).append({"match": "all", "rules": []})
        self.filter_rule = rule
        await self.refresh_rules_list()
        for index, row in enumerate(self._rule_rows):
            if row.get("kind") == "group_header" and row.get("group_index") == len(rule["groups"]) - 1:
                self.query_one("#filter-rules-list", ListView).index = index
                break
        self.start_rule_input()

    def delete_selected_rule(self) -> None:
        location = self.selected_rule_location()
        row = self.selected_rule_row()
        rule = self.filter_rule or self.app.empty_vibe_rule()
        if location:
            self.remove_rule_at(*location)
        elif row.get("kind") == "group_header":
            group_index = int(row.get("group_index") or 0)
            if len(rule.get("groups") or []) <= 1:
                rule["groups"][0]["rules"] = []
            else:
                del rule["groups"][group_index]
            self.filter_rule = rule
        else:
            self.app.show_status("Select a rule or group to delete.")
            return
        asyncio.create_task(self.refresh_rules_list())
        self.refresh_summary()
        self.app.show_status("Deleted.")

    def remove_rule_at(self, section: str, group_index: int | None, rule_index: int) -> None:
        rule = self.filter_rule or self.app.empty_vibe_rule()
        if section == "required":
            del rule["required"]["rules"][int(rule_index)]
        else:
            group = rule["groups"][int(group_index or 0)]
            del group["rules"][int(rule_index)]
            if not group["rules"] and len(rule["groups"]) > 1:
                del rule["groups"][int(group_index or 0)]
        self.filter_rule = rule

    def loosen_selected_rule(self) -> None:
        location = self.selected_rule_location()
        if not location:
            self.app.show_status("Select an audio rule to loosen.")
            return
        rule = self.filter_rule_at(*location)
        if not rule:
            self.app.show_status("Select an audio rule to loosen.")
            return
        field = rule.get("field")
        feature = VIBE_FEATURE_BY_FIELD.get(field)
        if not feature:
            self.app.show_status("Selected audio rule cannot be loosened.")
            return
        labels = feature[1]["labels"]
        expanded = set()
        for raw in rule.get("values") or []:
            score = int(raw)
            expanded.update(value for value in (score - 1, score, score + 1) if 1 <= value <= len(labels))
        rule["values"] = sorted(expanded)
        rule.pop("display", None)
        asyncio.create_task(self.refresh_rules_list())
        self.refresh_summary()
        self.app.show_status("Audio rule loosened.")

    def refresh_summary(self) -> None:
        summary = self.summary_for_state(self.current_ui_state())
        self.query_one("#filter-summary", Label).update(summary)

    def summary_for_state(self, state: dict) -> str:
        parts = []
        for key, label in (
            ("status_discovered", "Discovered"),
            ("status_matched", "Matched"),
            ("status_failed", "Failed Matches"),
            ("status_downloaded", "Downloaded"),
            ("status_review", "Review"),
            ("flag_archived", "Archived"),
            ("type_music", "Music"),
            ("type_podcast", "Podcast"),
            ("type_audiobook", "Audiobook"),
            ("feedback_liked", "Loved"),
            ("feedback_neutral", "Neutral"),
            ("feedback_disliked", "Booed"),
            ("flag_file_missing", "Missing files"),
            ("flag_orphaned", "Orphaned"),
            ("audio_analyzed", "Analyzed"),
            ("audio_failed", "Failed analysis"),
            ("audio_missing", "Missing analysis"),
            ("audio_stale", "Stale analysis"),
        ):
            if state.get(key):
                parts.append(label)
        audio_rule = state.get("audio_rule")
        if isinstance(audio_rule, dict) and self.app.vibe_rule_has_any_rules(audio_rule):
            parts.append("Audio rules")
        text_val = str(state.get("text_contains") or "").strip()
        if text_val:
            parts.append(f'Text: "{text_val}"')
        return "Active: " + ("; ".join(parts) if parts else "Default")

    def execute_action(self, action: str) -> None:
        context = self.app.active_filter_context

        if action == "cancel":
            self.app.action_go_back()
            return

        if action == "clear":
            new_ui_state = self.app.default_filter_ui_state()
            query = self.app.query_from_filter_state(new_ui_state, context)
        else: # apply
            new_ui_state = self.current_ui_state()
            query = self.app.query_from_filter_state(new_ui_state, context)

        # Persist and enforce refresh!
        if context == "media":
            self.app.media_filter_ui_state = new_ui_state
            self.app.media_filter = query
            if action in ("apply", "clear"):
                screen = self.app.query_one("#media", MediaScreen)
                screen._offset = 0
                screen._needs_refresh = True
        else:
            self.app.collection_filter_ui_state = new_ui_state
            self.app.collection_filter = query
            if action in ("apply", "clear"):
                screen = self.app.query_one("#collection_details", CollectionDetailsScreen)
                screen._offset = 0
                screen._needs_refresh = True

        self.app.action_go_back()


class MediaDetailsScreen(VerticalScroll):
    can_focus = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._needs_refresh = True

    def compose(self) -> ComposeResult:
        yield Label("", id="media-details-title", classes="details-title")
        yield Label("", id="media-details-artist", classes="details-subtitle")
        yield Rule()
        yield from self._build_dense_row("Artist:", "", id_suffix="artist")
        yield from self._build_dense_row("Album:", "", id_suffix="album")
        yield from self._build_dense_row("Duration:", "", id_suffix="duration")
        yield from self._build_dense_row("Status:", "", id_suffix="status")
        yield from self._build_dense_row("Source:", "", id_suffix="source")
        yield from self._build_dense_row("File:", "", id_suffix="file")
        yield from self._build_dense_row("Spotify ID:", "", id_suffix="spotify")
        yield from self._build_dense_row("YouTube ID:", "", id_suffix="youtube")
        yield Label("")
        yield Label("Audio Analysis", classes="section-header")
        yield from self._build_dense_row("Analysis:", "", id_suffix="analysis-status")
        yield from self._build_dense_row("Analyzed:", "", id_suffix="analysis-time")
        yield from self._build_dense_row("Felt tempo:", "", id_suffix="analysis-tempo")
        yield from self._build_dense_row("Detected tempo:", "", id_suffix="analysis-tempo-raw")
        yield from self._build_dense_row("Tempo stability:", "", id_suffix="analysis-tempo-var")
        yield from self._build_dense_row("Intensity:", "", id_suffix="analysis-intensity")
        yield from self._build_dense_row("Dynamics:", "", id_suffix="analysis-impact")
        yield from self._build_dense_row("Activity:", "", id_suffix="analysis-onset-density")
        yield from self._build_dense_row("Tone:", "", id_suffix="analysis-brightness")
        yield from self._build_dense_row("Texture:", "", id_suffix="analysis-flatness")
        yield from self._build_dense_row("Vocals:", "", id_suffix="analysis-vocals")
        yield from self._build_dense_row("Key:", "", id_suffix="analysis-key-scale")
        yield from self._build_dense_row("Key certainty:", "", id_suffix="analysis-key-confidence")
        yield from self._build_dense_row("Harmony:", "", id_suffix="analysis-harmony")
        yield from self._build_dense_row("Analyzer:", "", id_suffix="analysis-version")
        yield Label("", id="analysis-error", classes="detail-block")

    async def on_show(self) -> None:
        if self._needs_refresh:
            await self.refresh_details()
            self._needs_refresh = False

    async def refresh_details(self) -> None:
        self.app.show_status("Status: Loading", persistent=True)
        song_id = getattr(self.app, 'current_media_id', None)
        item = self.app.collection_manager.get_track_details(song_id) if song_id else {}

        title = item.get('title', 'Unknown Media')
        artist = item.get('artist', 'Unknown Artist')
        album = item.get('album') or 'Unknown Album'
        duration = self.app.format_duration(item.get('duration_ms'))
        status = (item.get('status') or item.get('song_status') or 'UNKNOWN').capitalize()
        source = 'Spotify' if item.get('spotify_id') else 'YouTube' if item.get('youtube_id') else 'Library'
        file_path = item.get('filepath') or item.get('file_path') or 'Not available'
        spotify_id = item.get('spotify_id') or 'Not available'
        youtube_id = item.get('youtube_id') or 'Not available'

        self.query_one("#media-details-title", Label).update(title)
        self.query_one("#media-details-artist", Label).update(artist)
        self._update_dense_row("artist", artist)
        self._update_dense_row("album", album)
        self._update_dense_row("duration", duration)
        self._update_dense_row("status", status)
        self._update_dense_row("source", source)
        self._update_dense_row("file", file_path)
        self._update_dense_row("spotify", str(spotify_id))
        self._update_dense_row("youtube", str(youtube_id))
        self._update_analysis_rows(song_id)
        self.app.update_current_context_actions()
        self.app.show_status("Status: Ready")

    def _update_analysis_rows(self, song_id: str | None) -> None:
        profile = self.app.audio_analysis_manager.get_song_features(song_id) if song_id else None
        if not profile:
            values = {
                "analysis-status": "Not analyzed",
                "analysis-time": "Not available",
                "analysis-tempo": "Not available",
                "analysis-tempo-raw": "Not available",
                "analysis-tempo-var": "Not available",
                "analysis-intensity": "Not available",
                "analysis-onset-density": "Not available",
                "analysis-impact": "Not available",
                "analysis-brightness": "Not available",
                "analysis-flatness": "Not available",
                "analysis-vocals": "Not available",
                "analysis-key-scale": "Not available",
                "analysis-key-confidence": "Not available",
                "analysis-harmony": "Not available",
                "analysis-version": "Not available",
            }
            for suffix, value in values.items():
                self._update_dense_row(suffix, value)
            self.query_one("#analysis-error", Label).update("")
            return

        humanized = self.app.audio_analysis_manager.humanize_profile(profile)
        impact = self.app.audio_analysis_manager.humanize_impact(profile)
        status = (profile.analysis_status or "UNKNOWN").capitalize()
        stale = profile.analysis_status == "COMPLETE" and not self.app.audio_analysis_manager.is_profile_current(profile)
        if stale:
            status = "Stale - re-analyze"
        analyzed = self.app.format_history_timestamp(profile.analyzed_at) if profile.analyzed_at else "Not available"
        if stale:
            values = {
                "analysis-status": status,
                "analysis-time": analyzed,
                "analysis-tempo": "Re-analyze for current labels",
                "analysis-tempo-raw": self._format_float(profile.tempo_raw_bpm, 1, " BPM"),
                "analysis-tempo-var": "Re-analyze for current labels",
                "analysis-intensity": "Re-analyze for current labels",
                "analysis-onset-density": "Re-analyze for current labels",
                "analysis-impact": "Re-analyze for current labels",
                "analysis-brightness": "Re-analyze for current labels",
                "analysis-flatness": "Re-analyze for current labels",
                "analysis-vocals": "Re-analyze for current labels",
                "analysis-key-scale": self._format_key_scale(profile),
                "analysis-key-confidence": "Re-analyze for current labels",
                "analysis-harmony": "Re-analyze for current labels",
                "analysis-version": profile.analyzer_version or "Not available",
            }
            for suffix, value in values.items():
                self._update_dense_row(suffix, value)
            self.query_one("#analysis-error", Label).update("")
            return

        values = {
            "analysis-status": status,
            "analysis-time": analyzed,
            "analysis-tempo": humanized["tempo_bpm"]["display"],
            "analysis-tempo-raw": humanized["tempo_raw_bpm"]["raw"],
            "analysis-tempo-var": humanized["tempo_variability"]["label"],
            "analysis-intensity": humanized["energy_mean"]["label"],
            "analysis-onset-density": humanized["onset_density"]["label"],
            "analysis-impact": impact["label"],
            "analysis-brightness": humanized["spectral_brightness"]["label"],
            "analysis-flatness": humanized["spectral_flatness"]["label"],
            "analysis-vocals": humanized["instrumentalness"]["label"],
            "analysis-key-scale": self._format_key_scale(profile),
            "analysis-key-confidence": humanized["key_confidence"]["label"],
            "analysis-harmony": humanized["harmonic_complexity"]["raw"],
            "analysis-version": profile.analyzer_version or "Not available",
        }
        for suffix, value in values.items():
            self._update_dense_row(suffix, value)
        self.query_one("#analysis-error", Label).update(
            f"Error: {profile.analysis_error}" if profile.analysis_error else ""
        )

    def _format_float(self, value: float | None, places: int, suffix: str = "") -> str:
        if value is None:
            return "Not available"
        return f"{float(value):.{places}f}{suffix}"

    def _format_key_scale(self, profile) -> str:
        scale = profile.key_scale or "Not available"
        if scale == "fluid":
            return "Fluid"
        root = profile.key_root or "Unknown"
        return f"{root} {scale.capitalize()}"

    def _update_dense_row(self, suffix: str, value: str) -> None:
        try:
            self.query_one(f"#dense-value-{suffix}", Label).update(value)
        except Exception:
            pass

    def _build_dense_row(self, key: str, val: str, id_suffix: str | None = None) -> ComposeResult:
        value_id = f"dense-value-{id_suffix}" if id_suffix else None
        with Horizontal(classes="dense-row"):
            yield Label(key, classes="dense-key")
            yield Label(val, classes="dense-value", id=value_id)


class AudioAnalysisSummaryScreen(VerticalScroll):
    can_focus = True

    def compose(self) -> ComposeResult:
        yield Label("Audio Analysis Summary", classes="section-header")
        yield Label("", id="audio-summary-counts", classes="detail-block")
        yield Rule()
        yield Label("Metric Calibration", classes="section-header")
        yield Label("", id="audio-summary-metrics", classes="detail-block")
        yield Rule()
        yield Label("Key Scale", classes="section-header")
        yield Label("", id="audio-summary-keys", classes="detail-block")

    async def on_show(self) -> None:
        await self.refresh_summary()

    async def refresh_summary(self) -> None:
        summary = self.app.audio_analysis_manager.get_analysis_summary()
        self.query_one("#audio-summary-counts", Label).update(
            f"Analyzed: {summary.analyzed_count}\n"
            f"Failed: {summary.failed_count}\n"
            f"Missing/Stale: {summary.missing_stale_count}"
        )
        self.query_one("#audio-summary-metrics", Label).update(self._format_metrics(summary.metrics))
        self.query_one("#audio-summary-keys", Label).update(
            f"Major: {summary.key_scale_counts.get('major', 0)}\n"
            f"Minor: {summary.key_scale_counts.get('minor', 0)}\n"
            f"Fluid: {summary.key_scale_counts.get('fluid', 0)}"
        )
        self.app.update_current_context_actions()
        self.app.show_status("Status: Ready")

    def _format_metrics(self, metrics: dict) -> str:
        lines = []
        for field, presentation in self._summary_presentations():
            item = metrics.get(field)
            name = str(presentation.get("name") or field)
            if not item or item.count == 0:
                lines.append(f"{name} (n=0)\n  min n/a | p10 n/a | p25 n/a | median n/a\n  p75 n/a | p90 n/a | max n/a")
                continue
            places = int(presentation.get("places") or 2)
            values = {
                attr: self._format_number(getattr(item, attr), places)
                for attr in ("min", "p10", "p25", "median", "p75", "p90", "max")
            }
            lines.append(
                f"{name} (n={item.count})\n"
                f"  min {values['min']} | p10 {values['p10']} | p25 {values['p25']} | median {values['median']}\n"
                f"  p75 {values['p75']} | p90 {values['p90']} | max {values['max']}"
            )
        return "\n\n".join(lines)

    def _summary_presentations(self) -> list[tuple[str, dict]]:
        items: list[tuple[str, dict]] = []
        for field, presentation in FEATURE_PRESENTATION.items():
            items.append((field, presentation))
            if field == "energy_p90":
                items.append(("impact", IMPACT_PRESENTATION))
        return items

    def _format_number(self, value: float | None, places: int) -> str:
        if value is None:
            return "n/a"
        return f"{float(value):.{places}f}"


class AudioMatrixScreen(VerticalScroll):
    can_focus = True

    TRAITS = {
        "tempo": ("tempo_bpm", FEATURE_PRESENTATION["tempo_bpm"]),
        "activity": ("onset_density", FEATURE_PRESENTATION["onset_density"]),
        "intensity": ("energy_mean", FEATURE_PRESENTATION["energy_mean"]),
        "dynamics": ("impact", IMPACT_PRESENTATION),
        "tone": ("spectral_brightness", FEATURE_PRESENTATION["spectral_brightness"]),
        "texture": ("spectral_flatness", FEATURE_PRESENTATION["spectral_flatness"]),
        "vocals": ("instrumentalness", FEATURE_PRESENTATION["instrumentalness"]),
    }
    TRAIT_ORDER = ["tone", "activity", "tempo", "intensity", "dynamics", "texture", "vocals"]

    def compose(self) -> ComposeResult:
        yield Label("Experimental Audio Matrix", classes="section-header")
        yield Label("", id="audio-matrix-status", classes="detail-block")
        with ListView(id="audio-matrix-trait-list"):
            pass
        yield DataTable(id="audio-matrix-table")
        yield Label("", id="audio-matrix-samples", classes="detail-block")

    async def on_show(self) -> None:
        await self.refresh_report()

    async def refresh_report(self) -> None:
        self._mode = "choose_rows"
        self._row_trait = None
        self._column_trait = None
        self._rows = self._audio_rows()
        self._matrix_calibration = self.app.audio_analysis_manager.get_feature_calibration()
        await self._show_trait_picker("Choose row trait")
        self.app.update_current_context_actions()
        self.app.show_status("Status: Ready")

    async def _show_trait_picker(self, prompt: str) -> None:
        self._mode = "choose_columns" if self._row_trait else "choose_rows"
        self.query_one("#audio-matrix-status", Label).update(prompt)
        trait_list = self.query_one("#audio-matrix-trait-list", ListView)
        table = self.query_one("#audio-matrix-table", DataTable)
        samples = self.query_one("#audio-matrix-samples", Label)
        trait_list.display = True
        table.display = False
        samples.display = False
        await trait_list.clear()
        for trait in self.TRAIT_ORDER:
            if self._mode == "choose_columns" and trait == self._row_trait:
                continue
            await trait_list.mount(ListItem(Label(self._title(trait)), id=f"audio-matrix-trait-{trait}"))
        trait_list.index = 0
        trait_list.focus()

    async def _show_matrix(self) -> None:
        if not self._row_trait or not self._column_trait:
            return
        self._mode = "matrix"
        trait_list = self.query_one("#audio-matrix-trait-list", ListView)
        table = self.query_one("#audio-matrix-table", DataTable)
        samples = self.query_one("#audio-matrix-samples", Label)
        trait_list.display = False
        table.display = True
        samples.display = True
        self.query_one("#audio-matrix-status", Label).update(
            f"{self._title(self._row_trait)} x {self._title(self._column_trait)}"
        )
        table.clear(columns=True)
        table.cursor_type = "cell"
        table.zebra_stripes = True
        table.add_columns("", *self._labels(self._column_trait))
        for row_label in self._labels(self._row_trait):
            counts = [
                len(self._cell_rows(row_label, column_label))
                for column_label in self._labels(self._column_trait)
            ]
            table.add_row(row_label, *[str(count) for count in counts])
        table.focus()
        self._update_samples_for_coordinate(0, 1)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        if self._mode not in ("choose_rows", "choose_columns"):
            return
        item_id = event.item.id or ""
        prefix = "audio-matrix-trait-"
        if not item_id.startswith(prefix):
            return
        trait = item_id[len(prefix):]
        if self._mode == "choose_rows":
            self._row_trait = trait
            asyncio.create_task(self._show_trait_picker("Choose column trait"))
        else:
            self._column_trait = trait
            asyncio.create_task(self._show_matrix())

    def on_data_table_cell_highlighted(self, event) -> None:
        coordinate = getattr(event, "coordinate", None)
        if coordinate is not None:
            self._update_samples_for_coordinate(coordinate.row, coordinate.column)

    def on_data_table_cell_selected(self, event) -> None:
        coordinate = getattr(event, "coordinate", None)
        if coordinate is not None:
            self._open_cell_for_coordinate(coordinate.row, coordinate.column)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            if self._mode == "choose_columns":
                self._row_trait = None
                asyncio.create_task(self._show_trait_picker("Choose row trait"))
                event.stop()
            elif self._mode == "matrix":
                self._column_trait = None
                asyncio.create_task(self._show_trait_picker("Choose column trait"))
                event.stop()
        elif event.key == "enter" and self._mode == "matrix":
            table = self.query_one("#audio-matrix-table", DataTable)
            coordinate = getattr(table, "cursor_coordinate", None)
            if coordinate is not None:
                self._open_cell_for_coordinate(coordinate.row, coordinate.column)
                event.stop()

    def _audio_rows(self) -> list[dict]:
        with self.app.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT s.artist, s.title,
                       af.tempo_bpm,
                       af.onset_density,
                       af.energy_mean,
                       af.energy_p90,
                       af.spectral_brightness,
                       af.spectral_flatness,
                       af.instrumentalness
                FROM song_audio_features af
                JOIN songs s ON s.song_id = af.song_id
                WHERE af.analysis_status = 'COMPLETE'
                ORDER BY s.artist, s.title
                """
            )
            return [dict(row) for row in cursor.fetchall()]

    def _sample_rows(self, rows: list[dict], count: int) -> list[dict]:
        if len(rows) <= count:
            return rows
        fractions = [index / max(1, count - 1) for index in range(count)]
        indexes = [round((len(rows) - 1) * fraction) for fraction in fractions]
        samples = []
        seen = set()
        for index in indexes:
            row = rows[index]
            key = (row.get("artist"), row.get("title"))
            if key not in seen:
                seen.add(key)
                samples.append(row)
        return samples

    def _cell_rows(self, row_label: str, column_label: str) -> list[dict]:
        if not self._row_trait or not self._column_trait:
            return []
        return [
            row for row in getattr(self, "_rows", [])
            if self._bucket(row, self._row_trait) == row_label
            and self._bucket(row, self._column_trait) == column_label
        ]

    def _labels_for_coordinate(self, row_index: int, column_index: int) -> tuple[str, str] | None:
        if not self._row_trait or not self._column_trait:
            return None
        row_labels = self._labels(self._row_trait)
        column_labels = self._labels(self._column_trait)
        if column_index <= 0:
            return None
        column_index -= 1
        if row_index < 0 or row_index >= len(row_labels) or column_index < 0 or column_index >= len(column_labels):
            return None
        return row_labels[row_index], column_labels[column_index]

    def _update_samples_for_coordinate(self, row_index: int, column_index: int) -> None:
        labels = self._labels_for_coordinate(row_index, column_index)
        samples = self.query_one("#audio-matrix-samples", Label)
        if labels is None:
            samples.update("Select a count cell.")
            return
        row_label, column_label = labels
        rows = self._cell_rows(row_label, column_label)
        lines = [f"{row_label} + {column_label}: {len(rows)} tracks", ""]
        for row in self._sample_rows(rows, 10):
            lines.append(f"{row.get('artist') or 'Unknown Artist'} - {row.get('title') or 'Unknown Track'}")
        samples.update("\n".join(lines))

    def _open_cell_for_coordinate(self, row_index: int, column_index: int) -> None:
        labels = self._labels_for_coordinate(row_index, column_index)
        if labels is None or not self._row_trait or not self._column_trait:
            return
        row_label, column_label = labels
        row_score = self._labels(self._row_trait).index(row_label) + 1
        column_score = self._labels(self._column_trait).index(column_label) + 1
        state = self.app.default_filter_ui_state()
        for trait, score in ((self._row_trait, row_score), (self._column_trait, column_score)):
            field, _presentation = self.TRAITS[trait]
            state[f"feature_{field}_{score}"] = True
        self.app.media_filter_ui_state = state
        self.app.media_filter = self.app.query_from_filter_state(state, "media")
        screen = self.app.query_one("#media", MediaScreen)
        screen._offset = 0
        screen._needs_refresh = True
        self.app.action_navigate("media")

    def _sample_detail(self, row: dict) -> str:
        parts = []
        for trait in ("tempo", "activity", "intensity", "dynamics", "tone", "texture", "vocals"):
            label = self._bucket(row, trait)
            if label:
                parts.append(label)
        return f" [{', '.join(parts)}]" if parts else ""

    def _bucket(self, row: dict, trait: str) -> str | None:
        field, presentation = self.TRAITS[trait]
        if trait == "dynamics":
            value = self.app.audio_analysis_manager._impact_value(row.get("energy_mean"), row.get("energy_p90"))
        else:
            value = row.get(field)
        if not isinstance(value, (int, float)):
            return None
        calibration = getattr(self, "_matrix_calibration", None) or self.app.audio_analysis_manager.get_feature_calibration()
        score = self.app.audio_analysis_manager._score_for_field(field, float(value), presentation, calibration)
        return str(presentation["labels"][max(0, min(score - 1, len(presentation["labels"]) - 1))])

    def _labels(self, trait: str) -> list[str]:
        return list(self.TRAITS[trait][1]["labels"])

    def _title(self, trait: str) -> str:
        return str(self.TRAITS[trait][1]["name"])


class ReviewScreen(VerticalScroll):
    can_focus = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._render_lock = asyncio.Lock()
        self._render_generation = 0

    def compose(self) -> ComposeResult:
        yield Label("", id="review-progress", classes="section-header")
        yield Rule()
        yield Label("Track:", classes="dense-key")
        yield Label("", id="review-title", classes="details-title")
        yield Label("Artist:", classes="dense-key")
        yield Label("", id="review-artist", classes="details-subtitle")
        yield Label("Status:", classes="dense-key")
        yield Label("", id="review-status", classes="details-meta")
        yield Label("", id="review-note", classes="detail-block")
        yield Rule()
        yield Label("Candidates", classes="section-header")
        with ListView(id="review-candidate-list"):
            pass
        yield Rule()
        yield Label("", id="review-summary", classes="detail-block")

    async def on_show(self) -> None:
        await self.render_session()

    async def render_session(self) -> None:
        async with self._render_lock:
            self._render_generation += 1
            generation = self._render_generation
            session = self.app.review_session
            candidate_list = self.query_one("#review-candidate-list", ListView)
            await candidate_list.clear()

            if not session:
                self.query_one("#review-progress", Label).update("Review")
                self.query_one("#review-title", Label).update("")
                self.query_one("#review-artist", Label).update("")
                self.query_one("#review-status", Label).update("")
                self.query_one("#review-note", Label).update("")
                self.query_one("#review-summary", Label).update("")
                return

            if session.complete:
                self.query_one("#review-progress", Label).update("Review Complete")
                self.query_one("#review-title", Label).update("")
                self.query_one("#review-artist", Label).update("")
                self.query_one("#review-status", Label).update("")
                self.query_one("#review-note", Label).update("")
                self.query_one("#review-summary", Label).update(
                    f"Reviewed: {session.reviewed_count}\n"
                    f"Skipped: {session.skipped_count}\n"
                    f"Approved: {session.approved_count}\n\n"
                    "Press Enter to return."
                )
                await candidate_list.mount(ListItem(Label("Return to previous screen"), id=f"review-return-{generation}"))
                candidate_list.index = 0
                candidate_list.focus()
                return

            item = session.current_item or {}
            details = self.app.collection_manager.get_track_details(item.get("song_id"))
            title = details.get("title") or item.get("title", "Unknown")
            artist = details.get("artist") or item.get("artist", "Unknown Artist")
            status = details.get("status") or item.get("song_status") or item.get("status") or "UNKNOWN"

            self.query_one("#review-progress", Label).update(
                f"Review {session.current_index + 1} / {session.total}"
            )
            self.query_one("#review-title", Label).update(title)
            self.query_one("#review-artist", Label).update(artist)
            self.query_one("#review-status", Label).update(str(status))
            self.query_one("#review-note", Label).update(self._format_review_note(details))
            self.query_one("#review-summary", Label).update("")

            for idx, candidate in enumerate(session.candidates, 1):
                if generation != self._render_generation:
                    return
                if candidate.get("kind") == "custom_url":
                    label = "Custom URL"
                    item_id = f"{CUSTOM_URL_CANDIDATE_ID}-{generation}-{idx}"
                else:
                    confidence = candidate.get("confidence")
                    score = f" ({confidence * 100:.1f}%)" if isinstance(confidence, (int, float)) else ""
                    label = f"{idx}. {candidate.get('yt_title') or candidate.get('title') or 'Untitled candidate'}{score}"
                    item_id = f"review-candidate-{generation}-{idx}-{candidate.get('match_id')}"
                await candidate_list.mount(ListItem(Label(label), id=item_id))

            candidate_list.index = max(0, min(session.candidate_index, len(session.candidates) - 1))
            candidate_list.focus()

    def _format_review_note(self, details: dict) -> str:
        reason = details.get("review_reason")
        source_url = details.get("review_source_url")
        source_error = details.get("review_source_error")
        if not any((reason, source_url, source_error)):
            return ""
        lines = []
        if reason:
            lines.append(str(reason))
        if source_url:
            lines.append(f"Old source: {source_url}")
        if source_error:
            lines.append(f"Error: {source_error}")
        return "\n".join(lines)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if self.app.review_session:
            list_view = getattr(event, "list_view", None) or self.query_one("#review-candidate-list", ListView)
            self.app.review_session.candidate_index = list_view.index or 0


class LegacyManagementFlowScreen(VerticalScroll):
    can_focus = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._render_generation = 0

    def compose(self) -> ComposeResult:
        yield Label("", id="management-title", classes="section-header")
        yield Rule()
        yield Label("", id="management-body", classes="detail-block")
        with ListView(id="management-list"):
            pass

    async def on_show(self) -> None:
        await self.render_flow()

    async def render_flow(self) -> None:
        flow = self.app.management_flow
        list_view = self.query_one("#management-list", ListView)
        self._render_generation += 1
        generation = self._render_generation
        await list_view.clear()

        if not flow:
            self.query_one("#management-title", Label).update("")
            self.query_one("#management-body", Label).update("")
            return

        title, body, rows = self.app.describe_management_flow(flow)
        if generation != self._render_generation:
            return
        self.query_one("#management-title", Label).update(title)
        self.query_one("#management-body", Label).update(body)
        for idx, row in enumerate(rows):
            if generation != self._render_generation:
                return
            await list_view.mount(ListItem(Label(self.app.format_management_row(flow, idx, row)), id=f"management-row-{generation}-{idx}"))
        if rows:
            target_index = flow.field_index if flow.kind in ("new_media_form", "penalty_form") else flow.selected_index
            list_view.index = max(0, min(target_index, len(rows) - 1))
            list_view.focus()

    def set_selection_index(self) -> None:
        flow = self.app.management_flow
        if not flow:
            return
        list_view = self.query_one("#management-list", ListView)
        if list_view.children:
            target_index = flow.field_index if flow.kind in ("new_media_form", "penalty_form") else flow.selected_index
            list_view.index = max(0, min(target_index, len(list_view.children) - 1))

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        flow = self.app.management_flow
        if flow:
            list_view = getattr(event, "list_view", None) or self.query_one("#management-list", ListView)
            rows = self.app.describe_management_flow(flow)[2]
            index = list_view.index or 0
            index = max(0, min(index, len(rows) - 1)) if rows else 0
            if flow.kind in ("new_media_form", "penalty_form"):
                flow.field_index = index
            else:
                flow.selected_index = index

    def refresh_row_labels(self) -> None:
        flow = self.app.management_flow
        if not flow:
            return
        rows = self.app.describe_management_flow(flow)[2]
        list_view = self.query_one("#management-list", ListView)
        for idx, item in enumerate(list_view.children):
            if idx >= len(rows):
                break
            try:
                item.query_one(Label).update(self.app.format_management_row(flow, idx, rows[idx]))
            except Exception:
                pass


class LegacyExportsScreen(VerticalScroll):
    can_focus = False

    def compose(self) -> ComposeResult:
        exports = self.app.export_manager.get_managed_exports()
        yield Label(f"Managed Exports ({len(exports)})", classes="section-header")
        yield Label("\n", classes="filter-indicator")

        with ListView(id="export-list"):
            for export in exports:
                target_dir = Path(export['target_path'])
                icon = "✓" if target_dir.exists() else "!"
                css_cls = "success" if target_dir.exists() else "warning"
                with ListItem(id=f"export-{export['export_id']}"):
                    with Horizontal(classes="list-row"):
                        yield Label(icon, classes=f"status-icon {css_cls}")
                        yield Label(export.get('collection_name', 'Unknown Collection'), classes="list-primary")
                        yield Label(str(target_dir), classes="list-secondary")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        item_id = getattr(event.item, 'id', None)
        export_id = item_id.split('-', 1)[1] if isinstance(item_id, str) and item_id.startswith('export-') else None
        
        if export_id:
            if getattr(self.app, 'current_export_id', None) != export_id:
                try:
                    self.app.query_one("#export_details", ExportDetailsScreen)._needs_refresh = True
                except Exception: pass
            self.app.current_export_id = export_id
            
        self.app.open_details_screen("export_details")


class LegacyExportDetailsScreen(VerticalScroll):
    can_focus = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._needs_refresh = True

    def compose(self) -> ComposeResult:
        yield Label("", id="export-details-title", classes="details-title")
        yield Rule()

        with Horizontal(classes="dense-row"):
            yield Label("Path:", classes="dense-key")
            yield Label("", id="export-details-path", classes="dense-value")
        with Horizontal(classes="dense-row"):
            yield Label("Status:", classes="dense-key")
            yield Label("", id="export-details-status", classes="dense-value")
        with Horizontal(classes="dense-row"):
            yield Label("Size:", classes="dense-key")
            yield Label("", id="export-details-size", classes="dense-value")
        with Horizontal(classes="dense-row"):
            yield Label("Collection:", classes="dense-key")
            yield Label("", id="export-details-collection", classes="dense-value")
        with Horizontal(classes="dense-row"):
            yield Label("Last Update:", classes="dense-key")
            yield Label("", id="export-details-updated", classes="dense-value")

    async def on_show(self) -> None:
        if self._needs_refresh:
            await self.refresh_details()
            self._needs_refresh = False

    async def refresh_details(self) -> None:
        export_id = getattr(self.app, 'current_export_id', None)
        export_record = None
        for item in self.app.export_manager.get_managed_exports():
            if item.get('export_id') == export_id:
                export_record = item
                break

        if not export_record:
            self.query_one("#export-details-title", Label).update("No export selected")
            self.query_one("#export-details-path", Label).update("")
            self.query_one("#export-details-status", Label).update("")
            self.query_one("#export-details-size", Label).update("")
            self.query_one("#export-details-collection", Label).update("")
            self.query_one("#export-details-updated", Label).update("")
            return

        target_dir = Path(export_record['target_path'])
        status = "Up To Date" if target_dir.exists() else "Missing"
        size_bytes = 0
        if target_dir.exists():
            for path in target_dir.rglob('*'):
                if path.is_file():
                    size_bytes += path.stat().st_size
        size_text = f"{size_bytes / (1024 * 1024):.1f} MB" if size_bytes else "0 MB"

        self.query_one("#export-details-title", Label).update(export_record.get('collection_name', 'Managed Export'))
        self.query_one("#export-details-path", Label).update(str(target_dir))
        self.query_one("#export-details-status", Label).update(status)
        self.query_one("#export-details-size", Label).update(size_text)
        self.query_one("#export-details-collection", Label).update(export_record.get('collection_name', 'Unknown Collection'))
        self.query_one("#export-details-updated", Label).update(str(export_record.get('last_updated', 'Unknown')))


# --- Main Application ---

class MusicSyncApp(
    CollectionWorkflowMixin,
    SettingsWorkflowMixin,
    CircuitsWorkflowMixin,
    ExportWorkflowMixin,
    VibeWorkflowMixin,
    ImportWorkflowMixin,
    JobWorkflowMixin,
    MediaWorkflowMixin,
    CommandWorkflowMixin,
    InputWorkflowMixin,
    ReviewWorkflowMixin,
    App,
):
    """MusicSync TUI Framework (Phase 6: Jump, Find, & Strict Polish)"""

    CSS = """
    /* --- Main Layout --- */
    #app-grid { layout: horizontal; height: 1fr; }
    #sidebar-pane { width: 30; height: 1fr; background: $surface; }
    #nav-section { height: auto; padding: 1 2 0 2; }
    #sidebar-list { height: auto; background: transparent; }
    .sidebar-divider { margin: 1 2; }
    #action-section { height: auto; padding: 0 2; }
    #context-actions { color: $text-muted; }
    #main-pane { width: 1fr; height: 1fr; border: round $primary; background: $surface; padding: 1 2; overflow-y: hidden; }
    #content-switcher { height: 1fr; }

    /* --- Shared Typography & Components --- */
    .section-header { text-style: bold; color: $accent; margin-bottom: 1; }
    .filter-indicator { color: $warning; text-style: italic; }
    .mock-text { padding-left: 1; color: $text; }
    .home-dashboard { layout: horizontal; height: auto; width: 100%; }
    .home-panel { width: 1fr; height: auto; padding-right: 2; }
    .home-stack { height: auto; margin-bottom: 1; }
    
    .list-row { layout: horizontal; height: 1; width: 100%; background: transparent; }
    .list-primary { width: 1fr; }
    .list-secondary { width: auto; color: $text-muted; }
    .empty-list-text { color: $text-muted; }
    .circuit-summary { color: $text-muted; margin-bottom: 1; text-wrap: wrap; width: 100%; }
    .circuit-item { height: 2; }
    .circuit-row { height: 2; width: 100%; }
    .circuit-fill { width: auto; color: $accent; text-style: bold; }
    .circuit-secondary { width: 1fr; color: $text-muted; }
    .circuit-preview-summary { color: $text; margin-bottom: 1; text-wrap: wrap; width: 100%; }
    .circuit-preview-row { layout: horizontal; height: 1; width: 100%; }
    .preview-marker { width: 3; color: $accent; text-style: bold; }
    .preview-feedback-cell { width: 9; color: $text-muted; text-align: center; }
    .preview-song { width: 1fr; }
    .active-feedback-column { color: $accent; text-style: bold; }

    /* --- Dense Layout (Media Details / Export Details) --- */
    .dense-row { layout: horizontal; height: auto; min-height: 1; width: 100%; margin-bottom: 0; }
    .dense-key { width: 22; text-style: bold; color: $text-muted; }
    .dense-value { width: 1fr; color: $text; text-wrap: wrap; }
    .detail-block { color: $text; margin-bottom: 1; text-wrap: wrap; width: 100%; }

    /* --- Detailed View Typography --- */
    .details-header { height: auto; }
    .details-title { text-style: bold; color: $accent; }
    .details-subtitle { color: $text; }
    .details-meta { color: $text-muted; }

    /* --- Filter UI Forms --- */
    #filter-columns { layout: horizontal; height: 1fr; width: 100%; }
    .filter-column { width: 1fr; height: 1fr; }
    .filter-column-title { text-style: bold; color: $accent; padding: 0 1; }
    #filter-options-list, #filter-rules-list { height: 1fr; }
    .filter-heading { text-style: bold; color: $accent; margin-top: 1; }
    .filter-option { padding: 0 1; }
    .filter-text { padding: 0 1; color: $text; }

    /* --- Lists & Status Icons (High Contrast) --- */
    #collection-list, #circuit-list, #circuit-preview-list, #circuit-track-feedback-list, #circuit-feedback-debug-list, #export-list, #settings-list, #media-list, #cd-track-list, #jobs-list, #filter-options-list, #filter-rules-list, #review-candidate-list, #management-list, #vibe-editor-list, #vibe-preview-list { background: transparent; }
    #collections, #collection_details, #media, #circuits, #circuit_preview, #circuit_track_feedback, #circuit_feedback_debug, #vibe_editor, #vibe_preview { height: 1fr; overflow-y: hidden; }
    #collection-list, #circuit-list, #circuit-preview-list, #circuit-track-feedback-list, #circuit-feedback-debug-list, #cd-track-list, #media-list, #vibe-editor-list, #vibe-preview-list { height: 1fr; }
    
    .track-row { layout: horizontal; height: 1; width: 100%; }
    .status-icon { width: 3; text-style: bold; background: transparent; }
    .success { color: $success; background: transparent; }
    .warning { color: $warning; background: transparent; }
    .error { color: $error; }
    .review { color: $accent; }
    .archived { color: $text-muted; text-style: bold; }
    .discovered { color: $text-muted; }
    .disconnected { color: $text-muted; }
    .shuffled { color: $accent; text-style: bold; }
    .loop { color: $success; text-style: bold; }
    .empty { color: $accent; text-style: bold; }
    
    .media-row-text { width: 100%; height: 1; overflow: hidden; }
    .track-title { width: 2fr; }
    .track-artist { width: 1fr; color: $text-muted; }

    /* --- Jobs Styling --- */
    .job-container { height: 2; margin-bottom: 1; }
    .job-row-top { layout: horizontal; height: 1; width: 100%; }
    .job-row-bottom { layout: horizontal; height: 1; width: 100%; }
    .job-name { width: 1fr; text-style: bold; }
    .job-status-running { width: auto; color: $accent; }
    .job-status-failed { width: auto; color: $error; }
    .job-status-paused { width: auto; color: $warning; }
    .job-status-completed { width: auto; color: $success; }
    .job-progress-bar { width: 1fr; color: $success; }
    .job-progress-text { width: auto; color: $text-muted; }

    /* --- Status Bar --- */
    #status-bar { dock: bottom; width: 100%; height: 3; border-top: solid $primary; background: $surface; color: $text; content-align: left middle; padding: 0 2; }
    """

    BINDINGS = [
        Binding("1", "navigate('home')", "Home", show=False),
        Binding("2", "navigate('collections')", "Collections", show=False),
        Binding("3", "navigate('media')", "Media", show=False),
        Binding("4", "navigate('circuits')", "Circuits", show=False),
        Binding("5", "navigate('exports')", "Exports", show=False),
        Binding("6", "navigate('jobs')", "Jobs", show=False),
        Binding("7", "navigate('settings')", "Settings", show=False),
        Binding("/", "command_palette", "Command Palette", show=False, priority=True),
        Binding("enter", "commit_input", "Commit Input", show=False, priority=True),
        Binding("shift+enter", "commit_input_shift", "Commit Input Shift", show=False, priority=True),
        Binding("escape", "escape_key", "Back", show=False, priority=True),
        Binding("[", "previous_media_pages_10", "Back 10 Pages", show=False, priority=True),
        Binding("]", "next_media_pages_10", "Forward 10 Pages", show=False, priority=True),
        Binding("s", "skip_review", "Skip Review Item", show=False, priority=True),
        
        # Actions
        Binding("a", "action_a", "A Action", show=False),
        Binding("b", "action_b", "B Action", show=False),
        Binding("c", "action_c", "C Action", show=False),
        Binding("d", "action_d", "D Action", show=False),
        Binding("f", "action_f", "F Action", show=False),
        Binding("g", "action_g", "G Action", show=False),
        Binding("h", "action_h", "H Action", show=False),
        Binding("j", "action_j", "J Action", show=False),
        Binding("l", "action_l", "L Action", show=False),
        Binding("m", "action_m", "M Action", show=False),
        Binding("o", "action_o", "O Action", show=False),
        Binding("p", "action_p", "P Action", show=False),
        Binding("r", "action_r", "R Action", show=False),
        Binding("t", "action_t", "T Action", show=False),
        Binding("e", "action_e", "E Action", show=False),
        Binding("u", "action_u", "U Action", show=False),
        Binding("v", "action_v", "V Action", show=False),
        Binding("x", "action_x", "X Action", show=False),
        Binding("y", "action_y", "Y Action", show=False),
        Binding("ctrl+a", "select_all_media", "Select All", show=False, priority=True),
        Binding("q", "quit", "Quit App"),
    ]

    NAV_MAP = {
        "home": ("1 Home", 0, "Enter  Open"),
        "collections": ("2 Collections", 1, "Enter  Open/Collapse\nA      Add Collection\nC      Copy\nG      Group\nH      History\nR      Refresh\nD      Download\nJ      Jump\nF      Find\nS      Sort\nE      Export\nX      Archive/Delete Group"),
        "media": ("3 Media", 2, "Enter  Open\nLeft/Right Page\nA      Add Media\nT      Filter\nJ      Jump\nF      Find\nS      Sort"),
        "circuits": ("4 Circuits", 3, "Enter  Open\nA      Add\nC      Circulate\nE      Edit\nD      Delete"),
        "exports": ("5 Exports", 4, "Enter  Open\nA      Add\nU      Update\nS      Shuffle\nC      Clear\nD      Delete\nR      Recover\nJ      Jump\nF      Find"),
        "jobs": ("6 Jobs", 5, "Enter  Details"),
        "settings": ("7 Settings", 6, "Enter  Edit"),
    }

    def __init__(self):
        super().__init__()
        self._status_timer: Timer | None = None
        self._status_persistent = False
        self._nav_stack = ["home"]
        
        # State Initialization
        self.settings = SettingsManager()
        self.db = DatabaseManager(self.settings.get("database_path", "./database.db"))
        self.config = ConfigManager("config.yaml")
        self.collection_manager = CollectionManager(self.db)
        self.library_manager = LibraryManager(self.config, self.db)
        self.acquisition_manager = AcquisitionManager(self.config, self.db, self.library_manager)
        self.audio_analysis_manager = AudioAnalysisManager(self.db, self.config)
        self.review = ReviewManager(self.db)
        self.export_manager = ExportManager(self.config, self.db)
        self.circuit_manager = CircuitManager(self.db, self.collection_manager)
        self.job_manager = JobManager(max_workers=2)
        self.plugins = {}
        self.discovery_plugins = self.plugins
        self.resolver_plugins = []
        self.rebuild_plugin_registries()
        self.matching_engine = MatchingEngine(self.config, self.db, self.resolver_plugins)
        
        # Context Variables
        self.current_collection_id = None
        self.show_archived_collections = False
        self.current_revision_id = None
        self.current_media_id = None
        self.current_collection_track_id = None
        self.current_collection_track_index = 0
        self.current_export_id = None
        self.current_circuit_id = None
        self.current_circuit_plan = None
        self.current_circuit_preview_id = None
        self.current_circuit_preview_origin = None
        self.current_circuit_preview_name = None
        self.current_job_id = None
        self._known_job_statuses: dict[str, str] = {}
        self._catalog_refresh_task = None
        self._catalog_refresh_pending = False
        self.selected_media_ids: set[str] = set()
        self.selected_media_anchor_id: str | None = None
        self._media_page_turn_running = False
        self._media_page_cooldown_until = 0.0

        # Filter State Management
        self.media_filter_ui_state = self.default_filter_ui_state()
        self.media_filter = self.query_from_filter_state(self.media_filter_ui_state, "media")
        
        self.collection_filter_ui_state = self.default_filter_ui_state()
        self.collection_filter = self.query_from_filter_state(self.collection_filter_ui_state, "collection_details")
        self.active_filter_context = None
        
        # Input State Management (Jump / Find)
        self.input_mode = "NORMAL"
        self.input_query = ""
        self.input_screen = None
        self.input_original_index = 0
        self.input_cached_actions = ""
        self.order_revert_screen = None
        self.order_revert_index = 0
        self.current_context_actions = "Enter  Open"
        self.review_session: ReviewSession | None = None
        self.review_cached_actions = ""
        self.management_flow: ManagementFlow | None = None
        self.current_vibe_collection_id = None
        self.current_vibe_name = None
        self.current_vibe_rule = None
        self.vibe_pending_rule = {}
        self.vibe_rule_target = {}
        self.vibe_rule_transfer = {}
        self.current_vibe_preview_items = []

    def compose(self) -> ComposeResult:
        with Horizontal(id="app-grid"):
            with Vertical(id="sidebar-pane"):
                with Vertical(id="nav-section"):
                    yield ListView(
                        ListItem(Label("1 Home"), id="nav-home"),
                        ListItem(Label("2 Collections"), id="nav-collections"),
                        ListItem(Label("3 Media"), id="nav-media"),
                        ListItem(Label("4 Circuits"), id="nav-circuits"),
                        ListItem(Label("5 Exports"), id="nav-exports"),
                        ListItem(Label("6 Jobs"), id="nav-jobs"),
                        ListItem(Label("7 Settings"), id="nav-settings"),
                        id="sidebar-list"
                    )
                yield Rule(classes="sidebar-divider")
                with Vertical(id="action-section"):
                    yield Label("Enter  Open", id="context-actions")
                
            with Vertical(id="main-pane"):
                with ContentSwitcher(initial="home", id="content-switcher"):
                    yield HomeScreen(id="home")
                    yield CollectionsScreen(id="collections")
                    yield CollectionDetailsScreen(id="collection_details")
                    yield CollectionHistoryScreen(id="collection_history")
                    yield CollectionSnapshotScreen(id="collection_snapshot")
                    yield MediaScreen(id="media")
                    yield MediaDetailsScreen(id="media_details")
                    yield AudioAnalysisSummaryScreen(id="audio_analysis_summary")
                    yield AudioMatrixScreen(id="audio_matrix")
                    yield ReviewScreen(id="review_session")
                    yield ManagementFlowScreen(id="management_flow")
                    yield CircuitsScreen(id="circuits")
                    yield CircuitDetailsScreen(id="circuit_details")
                    yield CircuitTrackFeedbackScreen(id="circuit_track_feedback")
                    yield CircuitCirculationPreviewScreen(id="circuit_preview")
                    yield CircuitFeedbackDebugScreen(id="circuit_feedback_debug")
                    yield ExportsScreen(id="exports")
                    yield ExportDetailsScreen(id="export_details")
                    yield JobsScreen(id="jobs")
                    yield JobDetailsScreen(id="job_details")
                    yield SettingsScreen(id="settings")
                    yield FilterScreen(id="filter")
                    yield VibeEditorScreen(id="vibe_editor")
                    yield VibePreviewScreen(id="vibe_preview")
                    yield PlaceholderScreen("Sub-Settings", id="sub_settings")

        yield Label("Status: Ready", id="status-bar")

    def rebuild_plugin_registries(self) -> None:
        self.plugins = {}
        self.discovery_plugins = self.plugins
        self.resolver_plugins = []
        self.spotify_unavailable_reason = ""
        try:
            spotify_plugin = SpotifySourcePlugin(self.config)
            self.discovery_plugins[spotify_plugin.source_type] = spotify_plugin
        except ValueError as exc:
            self.spotify_unavailable_reason = str(exc)
        except Exception:
            self.spotify_unavailable_reason = "Spotify could not start. Check your Spotify setup in Settings."
        try:
            youtube_plugin = YouTubeSourcePlugin(self.config)
            self.discovery_plugins[youtube_plugin.source_type] = youtube_plugin
            self.resolver_plugins.append(youtube_plugin)
        except Exception:
            pass
        try:
            billboard_plugin = BillboardSourcePlugin(self.config)
            self.discovery_plugins[billboard_plugin.source_type] = billboard_plugin
        except Exception:
            pass
        try:
            local_folder_plugin = LocalFolderSourcePlugin(self.config)
            self.discovery_plugins[local_folder_plugin.source_type] = local_folder_plugin
        except Exception:
            pass

    def on_mount(self) -> None:
        recovered = self.collection_manager.recover_draft_revisions()
        if recovered:
            self.show_status(f"Recovered {recovered} collection history drafts.")
        sidebar = self.query_one("#sidebar-list", ListView)
        sidebar.can_focus = False  
        sidebar.index = 0          
        self._focus_main_content("home")
        self.set_interval(1.0, self.refresh_dynamic_views)
        self.job_manager.submit(
            job_type=JobType.VERIFY_LIBRARY,
            description="Verify Library",
            target_func=self.library_manager.verify_library,
        )

    def on_unmount(self) -> None:
        try:
            self.collection_manager.commit_all_draft_revisions("app_close")
        except Exception:
            pass

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in ("previous_media_page", "next_media_page", "previous_media_pages_10", "next_media_pages_10"):
            if self.management_flow:
                return self.management_flow.kind == "youtube_radio_track_search"
            if self.input_mode != "NORMAL" or self.review_session:
                return False
            return self.query_one(ContentSwitcher).current in ("media", "collection_details")
        if self.management_flow:
            return action in ("commit_input", "commit_input_shift", "escape_key")
        if self.input_mode != "NORMAL":
            return action in ("commit_input", "escape_key")
        if self.review_session:
            return action in ("commit_input", "escape_key", "skip_review")
        if action == "commit_input":
            try:
                return self.query_one(ContentSwitcher).current == "vibe_editor"
            except Exception:
                return False
            return False
        if action == "commit_input_shift":
            return False
        if action == "skip_review":
            return True
        if action == "command_palette":
            return True
        return True

    # --- Input Processing (Jump / Find / Sort) ---
    async def on_paste(self, event: events.Paste) -> None:
        text = event.text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ")
        if not text:
            return

        if self.management_flow:
            self.edit_management_text(text=text)
            await self.refresh_management_after_text_edit()
            self._consume_input_key(event)
            return

        if self.input_mode == "VIBE_RULE_TARGET":
            self._consume_input_key(event)
            return

        if self.input_mode != "NORMAL":
            self.input_query += text
            await self._update_input_state()
            self._consume_input_key(event)

    async def on_key(self, event: events.Key) -> None:
        page_direction = self.media_page_key_direction(event)
        if page_direction and self.media_page_key_available():
            await self.handle_media_page_key(page_direction, event)
            return

        if self.management_flow:
            await self.handle_management_key(event)
            return

        if self.input_mode != "NORMAL":
            if event.key in ("up", "down", "pageup", "pagedown"):
                return  # Let textual handle list navigation

            if event.key == "escape":
                await self.action_abort_input()
                self._consume_input_key(event)
            elif event.key == "enter":
                await self.action_commit_input()
                self._consume_input_key(event)
            elif self.input_mode == "VIBE_RULE_TARGET":
                self._consume_input_key(event)
            elif self.input_mode in ("ARCHIVE_CONFIRM", "UNARCHIVE_CONFIRM"):
                char = (event.character or "").lower()
                if char == "y":
                    if self.input_mode == "UNARCHIVE_CONFIRM":
                        await self.confirm_unarchive_item()
                    else:
                        await self.confirm_archive_item()
                    self._consume_input_key(event)
                elif char == "n":
                    await self.action_abort_input()
                    self._consume_input_key(event)
            elif event.key == "backspace":
                if len(self.input_query) > 0:
                    self.input_query = self.input_query[:-1]
                    await self._update_input_state()
                else:
                    await self._update_input_state()
                self._consume_input_key(event)
            elif event.is_printable and len(event.character or "") == 1:
                self.input_query += event.character
                await self._update_input_state()
                self._consume_input_key(event)
            return

        key = (event.key or "").lower().replace("_", "+")
        if key == "ctrl+a":
            if self.action_select_all_media():
                self._consume_input_key(event)
            return

        direction = self.management_arrow_direction(event)
        if direction and self.management_shift_pressed(event):
            if await self.extend_media_selection(direction):
                self._consume_input_key(event)
            return
        if self.should_clear_media_selection_for_key(event):
            self.clear_media_selection()

    def media_page_key_direction(self, event: events.Key) -> int:
        key = (event.key or "").lower().replace("_", "+")
        character = event.character or ""
        if key == "right":
            return 1
        if key == "left":
            return -1
        if character == "]" or key in ("]", "right_square_bracket", "close_square_bracket", "right_bracket"):
            return 10
        if character == "[" or key in ("[", "left_square_bracket", "open_square_bracket", "left_bracket"):
            return -10
        return 0

    def media_page_key_available(self) -> bool:
        if self.input_mode != "NORMAL" or self.review_session:
            return False
        if self.management_flow:
            return self.management_flow.kind == "youtube_radio_track_search"
        return self.query_one(ContentSwitcher).current in ("media", "collection_details", "vibe_preview")

    async def handle_media_page_key(self, direction: int, event: events.Key) -> None:
        if not self.media_page_key_available():
            return
        self._consume_input_key(event)
        now = monotonic()
        if self._media_page_turn_running or now < self._media_page_cooldown_until:
            return
        await self.turn_media_page(direction)

    def _consume_input_key(self, event: events.Key | events.Paste) -> None:
        event.stop()
        prevent_default = getattr(event, "prevent_default", None)
        if prevent_default:
            prevent_default()

    async def handle_management_key(self, event: events.Key) -> None:
        flow = self.management_flow
        if not flow:
            return
        key = (event.key or "").lower().replace("_", "+")

        if key == "escape":
            await self.cancel_management_flow()
            self._consume_input_key(event)
            return
        if key.endswith("enter") and flow.kind in ("circuit_source_select", "circuit_edit_source_select"):
            shifted = self.management_shift_pressed(event)
            if shifted:
                await self.commit_management_flow()
            else:
                await self.handle_circuit_source_enter(flow)
            self._consume_input_key(event)
            return
        if key == "enter":
            await self.commit_management_flow()
            self._consume_input_key(event)
            return
        direction = self.management_arrow_direction(event)
        if direction:
            delta = -1 if direction == "up" else 1
            if flow.kind in ("new_media_form", "penalty_form"):
                flow.field_index = max(0, min(flow.field_index + delta, len(flow.fields) - 1))
                flow.selected_index = flow.field_index
                screen = self.query_one("#management_flow", ManagementFlowScreen)
                screen.refresh_row_labels()
                screen.set_selection_index()
            else:
                row_count = len(self.describe_management_flow(flow)[2])
                if row_count:
                    shifted = self.management_shift_pressed(event)
                    if shifted and flow.kind == "existing_media_search":
                        flow.selected_index = max(0, min(flow.selected_index + delta, row_count - 1))
                        lo, hi = sorted((flow.anchor_index, flow.selected_index))
                        flow.selected_indices = set(range(lo, hi + 1))
                        screen = self.query_one("#management_flow", ManagementFlowScreen)
                        screen.set_selection_index()
                        screen.refresh_row_labels()
                    else:
                        flow.selected_index = max(0, min(flow.selected_index + delta, row_count - 1))
                        flow.anchor_index = flow.selected_index
                        if flow.kind == "existing_media_search":
                            flow.selected_indices = set()
                            self.query_one("#management_flow", ManagementFlowScreen).refresh_row_labels()
                        self.query_one("#management_flow", ManagementFlowScreen).set_selection_index()
            self._consume_input_key(event)
            return
        if flow.kind in ("remove_track_confirm", "delete_standalone_confirm", "export_delete_confirm", "export_clear_confirm", "collection_group_delete_confirm", "circuit_delete_confirm"):
            char = (event.character or "").lower()
            if char in ("y", "n"):
                flow.selected_index = 0 if char == "y" else 1
                await self.commit_management_flow()
                self._consume_input_key(event)
                return
        if event.key == "backspace":
            self.edit_management_text(backspace=True)
            await self.refresh_management_after_text_edit()
            self._consume_input_key(event)
            return
        if event.is_printable and len(event.character or "") == 1:
            self.edit_management_text(character=event.character)
            await self.refresh_management_after_text_edit()
            self._consume_input_key(event)

    async def handle_circuit_source_enter(self, flow: ManagementFlow) -> None:
        row = flow.results[flow.selected_index] if 0 <= flow.selected_index < len(flow.results) else {}
        row_type = row.get("type")
        if row_type == "save_continue":
            await self.commit_management_flow()
            return
        if row_type == "group":
            self.toggle_circuit_source_group(flow, flow.selected_index)
            self.refresh_circuit_source_rows(flow)
            return
        if flow.selected_index in flow.selected_indices:
            flow.selected_indices.remove(flow.selected_index)
        else:
            if row_type == "all_downloaded":
                flow.selected_indices = {flow.selected_index}
            elif row_type == "collection":
                flow.selected_indices.discard(0)
                flow.selected_indices.add(flow.selected_index)
        self.refresh_circuit_source_rows(flow)

    def refresh_circuit_source_rows(self, flow: ManagementFlow) -> None:
        collections = self.collection_manager.get_all_collections()
        flow.options, flow.results = self.circuit_source_options(collections, flow.selected_indices)
        self.query_one("#management_flow", ManagementFlowScreen).refresh_row_labels()

    def toggle_circuit_source_group(self, flow: ManagementFlow, group_index: int) -> None:
        collection_indices = []
        for index in range(group_index + 1, len(flow.results)):
            row_type = flow.results[index].get("type")
            if row_type in ("group", "save_continue"):
                break
            if row_type == "collection":
                collection_indices.append(index)
        if not collection_indices:
            return
        if all(index in flow.selected_indices for index in collection_indices):
            flow.selected_indices.difference_update(collection_indices)
        else:
            flow.selected_indices.discard(0)
            flow.selected_indices.update(collection_indices)

    async def refresh_management_after_text_edit(self) -> None:
        flow = self.management_flow
        if flow and flow.kind in ("new_media_form", "penalty_form", "collection_group_select"):
            screen = self.query_one("#management_flow", ManagementFlowScreen)
            screen.refresh_row_labels()
            screen.set_selection_index()
        else:
            await self.render_management_flow()

    def edit_management_text(self, *, character: str | None = None, text: str | None = None, backspace: bool = False) -> None:
        flow = self.management_flow
        if not flow:
            return
        addition = text if text is not None else (character or "")
        if flow.kind in ("collection_name", "vibe_collection_name", "copy_collection_name", "existing_media_search", "youtube_radio_track_search", "youtube_radio_count", "external_collection_url", "local_folder_path", "external_media_url", "local_file_path", "setting_value", "setting_list_value", "export_target_path", "export_shuffle_count", "circuit_name", "circuit_target_path", "circuit_target_count", "circuit_edit_target_count"):
            text = flow.query
            flow.query = text[:-1] if backspace else text + addition
        if flow.kind == "collection_group_select" and flow.selected_index == len(flow.results):
            text = flow.query
            flow.query = text[:-1] if backspace else text + addition
        if flow.kind == "existing_media_search":
            self.update_existing_media_results()
            return
        if flow.kind == "youtube_radio_track_search":
            self.update_youtube_radio_seed_results(flow)
            flow.selected_index = 0
            flow.anchor_index = 0
            flow.selected_indices = set()
            return
        if flow.kind == "new_media_form" and flow.fields:
            label, value = flow.fields[flow.field_index]
            value = value[:-1] if backspace else value + addition
            flow.fields[flow.field_index] = (label, value)
        if flow.kind == "penalty_form" and flow.fields:
            label, value = flow.fields[flow.field_index]
            value = value[:-1] if backspace else value + addition
            flow.fields[flow.field_index] = (label, value)

    def management_arrow_direction(self, event: events.Key) -> str | None:
        key = (event.key or "").lower().replace("_", "+")
        if key in ("up", "shift+up") or key.endswith("+up"):
            return "up"
        if key in ("down", "shift+down") or key.endswith("+down"):
            return "down"
        return None

    def management_shift_pressed(self, event: events.Key) -> bool:
        key = (event.key or "").lower().replace("_", "+")
        if "shift" in key:
            return True
        modifiers = getattr(event, "modifiers", None)
        return bool(modifiers and any(str(modifier).lower() == "shift" for modifier in modifiers))

    def format_management_row(self, flow: ManagementFlow, idx: int, row: str) -> str:
        if flow.kind == "youtube_radio_track_search" and idx < len(flow.results):
            return self.format_media_list_row(flow.results[idx])
        if flow.kind == "existing_media_search" and flow.results:
            mark = "x" if idx in flow.selected_indices else " "
            return f"[{mark}] {row}"
        if flow.kind == "collection_group_select" and idx == len(flow.results):
            cursor = "_" if idx == flow.selected_index else ""
            return f"Create New Group: {flow.query}{cursor}"
        return row

    async def render_management_flow(self) -> None:
        await self.query_one("#management_flow", ManagementFlowScreen).render_flow()

    def describe_management_flow(self, flow: ManagementFlow) -> tuple[str, str, list[str]]:
        if flow.kind == "add_collection_choice":
            return "Add Collection", "Choose a collection type.", flow.options
        if flow.kind == "collection_name":
            return "Add Manual Collection", f"Name: {flow.query}_", []
        if flow.kind == "vibe_collection_name":
            return "Add Vibe Collection", f"Name: {flow.query}_", []
        if flow.kind == "vibe_preset_select":
            return "Vibe Preset", flow.collection_name or "Vibe", flow.options
        if flow.kind == "copy_collection_name":
            source = flow.collection_name or "Collection"
            action = "Restore Snapshot As Copy" if flow.setting_path else "Copy Collection"
            return action, f"{source}\nNew name: {flow.query}_", []
        if flow.kind == "collection_archive_choice":
            action = flow.payload.get("action")
            title = "Unarchive Collection" if action == "unarchive" else "Archive Collection"
            exports = flow.payload.get("managed_exports") or []
            if action == "archive" and exports:
                body = f"{flow.collection_name or 'Collection'} has {len(exports)} managed export(s). Tracks and files stay in the catalog."
            elif action == "archive":
                body = f"{flow.collection_name or 'Collection'} will be hidden from normal collection workflows."
            else:
                body = f"{flow.collection_name or 'Collection'} will return to normal collection workflows."
            return title, body, flow.options
        if flow.kind == "collection_group_select":
            rows = [group.get("name") or "Ungrouped" for group in flow.results]
            rows.append("Create New Group: ")
            return "Move To Group", flow.collection_name or "Collection", rows
        if flow.kind == "collection_group_delete_confirm":
            return "Delete Group", f"Delete {flow.collection_name or 'group'}? Collections inside it will move to Ungrouped. (Y/N)", flow.options
        if flow.kind == "external_collection_url":
            return "Add External Collection", f"URL or billboard:chart: {flow.query}_", []
        if flow.kind == "local_folder_path":
            return "Add Local Folder", f"Folder path: {flow.query}_", []
        if flow.kind == "local_folder_import_confirm":
            top_level = int(flow.payload.get("top_level_count", 0) or 0)
            subfolder_tracks = int(flow.payload.get("subfolder_track_count", 0) or 0)
            total = int(flow.payload.get("total_count", 0) or 0)
            subfolder_collections = int(flow.payload.get("subfolder_collection_count", 0) or 0)
            body = (
                f"{flow.payload.get('path', '')}\n"
                f"Top-level tracks: {top_level}\n"
                f"Tracks in immediate subfolders: {subfolder_tracks}\n"
                f"Import all as one collection: {total} tracks, 1 collection\n"
                f"Import each immediate subfolder: {subfolder_collections} collections"
            )
            return "Import Local Folder", body, flow.options
        if flow.kind == "youtube_radio_track_search":
            rows = [
                f"{self.status_icon(item)} {item.get('title', 'Unknown'):<32} {item.get('artist', 'Unknown Artist')}"
                for item in flow.results
            ]
            total = len(flow.source_results) if flow.source_results else len(flow.results)
            start = flow.page_offset + 1 if total and flow.results else 0
            end = flow.page_offset + len(flow.results)
            page = (flow.page_offset // max(1, flow.page_size)) + 1
            pages = ((total - 1) // max(1, flow.page_size)) + 1 if total else 1
            body = f"Choose seed track. Search: {flow.query}_\nLeft/Right Page {page}/{pages} ({start}-{end} of {total})"
            return "YouTube Radio Collection", body, rows or ["No YouTube-backed matched media"]
        if flow.kind == "youtube_radio_count":
            return "YouTube Radio Collection", f"Number of songs: {flow.query}_", []
        if flow.kind == "add_media_choice":
            return "Add Media", "Choose how to add media.", flow.options
        if flow.kind == "add_track_choice":
            return f"Add Track\n{flow.collection_name or ''}", "Choose how to add media.", flow.options
        if flow.kind == "external_media_url":
            return "Add Media From URL", f"URL: {flow.query}_", []
        if flow.kind == "local_file_path":
            return "Add Local File", f"File path: {flow.query}_", []
        if flow.kind == "existing_media_search":
            rows = [
                f"{self.status_icon(item)} {item.get('title', 'Unknown'):<32} {item.get('artist', 'Unknown Artist')}"
                for item in flow.results
            ]
            return "Add Existing Media", f"Search: {flow.query}_", rows or ["No matching media"]
        if flow.kind == "export_collection_select":
            rows = [item.get("name", "Unknown Collection") for item in flow.results]
            return "Create Export", "Select Collection:", rows or ["No collections available"]
        if flow.kind == "circuit_source_select":
            return "Create Circuit", "Enter toggles checkboxes. Shift+Enter saves.", flow.options or ["No sources available"]
        if flow.kind == "circuit_name":
            source = flow.payload.get("source_label") or "Source"
            return "Create Circuit", f"{source}\nName: {flow.query}_", []
        if flow.kind == "circuit_target_count":
            return "Create Circuit", f"Target track count: {flow.query}_", []
        if flow.kind == "circuit_root_select":
            rows = [item.get("display", item.get("path", "Unknown Root")) for item in flow.results]
            return "Circuit Root", "Select root:", rows or ["No export roots configured"]
        if flow.kind == "circuit_target_path":
            root = flow.payload.get("export_root")
            root_text = f"{root}\n" if root else ""
            return "Create Circuit", f"{root_text}Sub-folder: {flow.query}_", []
        if flow.kind == "circuit_edit_source_select":
            return "Edit Circuit", "Enter toggles checkboxes. Shift+Enter saves.", flow.options or ["No sources available"]
        if flow.kind == "circuit_edit_target_count":
            return "Edit Circuit", f"Target track count: {flow.query}_", []
        if flow.kind == "circuit_circulate_confirm":
            return "Circulate", flow.collection_name or "Circuit", flow.options
        if flow.kind == "circuit_delete_confirm":
            return "Delete Circuit", "Stop managing this circuit? Files will remain. (Y/N)", flow.options
        if flow.kind == "export_mode_select":
            return "Export Type", "Static is a one-time copy. Managed is tracked by MusicSync.", flow.options
        if flow.kind == "export_root_select":
            rows = [item.get("display", item.get("path", "Unknown Root")) for item in flow.results]
            return "Export Root", "Select Export Root:", rows or ["No export roots configured"]
        if flow.kind == "export_missing_confirm":
            missing = int(flow.payload.get("missing", 0) or 0)
            downloaded = int(flow.payload.get("downloaded", 0) or 0)
            total = int(flow.payload.get("total", 0) or 0)
            body = f"{missing} of {total} unarchived tracks are not downloaded and will be missing. {downloaded} tracks can be exported."
            return "Incomplete Export", body, flow.options
        if flow.kind == "update_export_missing_confirm":
            missing = int(flow.payload.get("missing", 0) or 0)
            downloaded = int(flow.payload.get("downloaded", 0) or 0)
            total = int(flow.payload.get("total", 0) or 0)
            body = f"{missing} of {total} unarchived tracks are not downloaded and will be missing. {downloaded} tracks can be exported."
            return "Incomplete Export Update", body, flow.options
        if flow.kind == "export_target_path":
            mode = "Managed" if flow.setting_type == "MANAGED" else "Static"
            root = flow.payload.get("export_root")
            root_text = f"{root}\n" if root else ""
            return f"{mode} Export", f"{root_text}Sub-folder: {flow.query}_", []
        if flow.kind == "export_delete_choice":
            return "Delete Managed Export", "Choose what to delete.", flow.options
        if flow.kind == "export_delete_confirm":
            if flow.setting_type == "delete_folder":
                return "Delete Export Folder", "Delete export folder and copied files? Library remains safe.", ["Yes", "No"]
            return "Stop Managing Export", "Stop managing this export? Files will remain.", ["Yes", "No"]
        if flow.kind == "export_clear_confirm":
            return "Clear Export", "Remove copied MP3 files from this export? Folder and manifest will remain.", ["Yes", "No"]
        if flow.kind == "export_shuffle_choice":
            missing = int(flow.payload.get("missing", 0) or 0)
            downloaded = int(flow.payload.get("downloaded", 0) or 0)
            total = int(flow.payload.get("total", 0) or 0)
            body = f"{downloaded} of {total} tracks are available to shuffle."
            if missing:
                body += f"\n{missing} unarchived tracks are not downloaded and cannot be included."
            return "Shuffle Export", body, flow.options
        if flow.kind == "export_shuffle_count":
            downloaded = int(flow.payload.get("downloaded", 0) or 0)
            return "Shuffle Export", f"Tracks to include, max {downloaded}: {flow.query}_", []
        if flow.kind == "command_palette":
            return "Command Palette", "Select command:", flow.options
        if flow.kind == "new_media_form":
            rows = []
            for idx, (label, value) in enumerate(flow.fields):
                cursor = "_" if idx == flow.field_index else ""
                rows.append(f"{label}: {value}{cursor}")
            return "New Media Item", "Enter Save | Esc Cancel | Arrows Move Field", rows
        if flow.kind == "setting_value":
            if flow.setting_type == "choice":
                options = flow.payload.get("options", [])
                rows = [
                    option.get("label", str(option.get("value"))) if isinstance(option, dict) else str(option)
                    for option in options
                ]
                return "Edit Setting", f"{flow.setting_path}: choose profile", rows
            return "Edit Setting", f"{flow.setting_path}: {flow.query}_", []
        if flow.kind == "setting_list_value":
            return "Edit List Item", f"{flow.setting_path}: {flow.query}_", []
        if flow.kind == "penalty_form":
            rows = []
            for idx, (label, value) in enumerate(flow.fields):
                cursor = "_" if idx == flow.field_index else ""
                rows.append(f"{label}: {value}{cursor}")
            return "Matching Penalty", "Enter Save | Esc Cancel | Arrows Move Field", rows
        if flow.kind == "remove_track_confirm":
            return "Remove Track", "Remove track from this collection? (Y/N)", ["Yes", "No"]
        if flow.kind == "delete_standalone_confirm":
            return "Delete Media", "Delete standalone media item? (Y/N)", ["Yes", "No"]
        return "MusicSync", "", []

    async def start_management_flow(self, flow: ManagementFlow) -> None:
        if self.query_one(ContentSwitcher).current != "management_flow":
            flow.origin_actions = self.current_context_actions
        self.management_flow = flow
        self.update_context_actions("Enter  Select/Save\nEsc    Cancel")
        self.query_one(ContentSwitcher).current = "management_flow"
        await self.render_management_flow()

    async def cancel_management_flow(self, message: str = "Cancelled.") -> None:
        flow = self.management_flow
        if not flow:
            return
        origin = flow.origin_screen_id
        actions = flow.origin_actions
        self.management_flow = None
        self.query_one(ContentSwitcher).current = origin
        self._focus_main_content(origin)
        self.update_current_context_actions()
        self.show_status(message)

    async def commit_management_flow(self) -> None:
        flow = self.management_flow
        if not flow:
            return
        try:
            if flow.kind == "add_collection_choice":
                if flow.selected_index == 0:
                    flow.kind = "collection_name"
                    flow.query = ""
                    await self.render_management_flow()
                elif flow.selected_index == 1:
                    flow.kind = "external_collection_url"
                    flow.query = ""
                    await self.render_management_flow()
                elif flow.selected_index == 2:
                    flow.kind = "local_folder_path"
                    flow.query = ""
                    await self.render_management_flow()
                elif flow.selected_index == 3:
                    flow.kind = "youtube_radio_track_search"
                    flow.query = ""
                    self.update_youtube_radio_seed_results(flow)
                    flow.selected_index = 0
                    await self.render_management_flow()
                else:
                    flow.kind = "vibe_collection_name"
                    flow.query = ""
                    await self.render_management_flow()
                return

            if flow.kind == "external_collection_url":
                await self.commit_external_collection_url(flow)
                return

            if flow.kind == "local_folder_path":
                await self.commit_local_folder_path(flow)
                return

            if flow.kind == "local_folder_import_confirm":
                await self.commit_local_folder_import_confirm(flow)
                return

            if flow.kind == "collection_name":
                name = flow.query.strip()
                if not name:
                    self.show_status("Collection name cannot be empty.")
                    return
                new_id = self.collection_manager.create_custom_collection(name)
                collection = next(
                    (item for item in self.collection_manager.get_all_collections(include_archived=True) if item.get("collection_id") == new_id),
                    {"collection_id": new_id, "name": name, "group_id": None},
                )
                await self.start_collection_group_flow(collection, after_create=True, origin_actions=flow.origin_actions)
                return

            if flow.kind == "vibe_collection_name":
                name = flow.query.strip()
                if not name:
                    self.show_status("Vibe name cannot be empty.")
                    return
                presets = self.vibe_presets()
                flow.kind = "vibe_preset_select"
                flow.collection_name = name
                flow.options = [label for label, _rule in presets]
                flow.results = [{"name": label, "rule": rule} for label, rule in presets]
                flow.selected_index = 0
                await self.render_management_flow()
                return

            if flow.kind == "vibe_preset_select":
                if not flow.results:
                    await self.start_create_vibe_flow(flow.collection_name or "Vibe")
                    return
                index = max(0, min(flow.selected_index, len(flow.results) - 1))
                preset = flow.results[index]
                await self.start_create_vibe_flow(flow.collection_name or "Vibe", preset.get("rule"))
                return

            if flow.kind == "copy_collection_name":
                await self.commit_copy_collection_flow(flow)
                return

            if flow.kind == "collection_archive_choice":
                await self.commit_collection_archive_flow(flow)
                return

            if flow.kind == "collection_group_select":
                await self.commit_collection_group_flow(flow)
                return

            if flow.kind == "collection_group_delete_confirm":
                await self.commit_delete_collection_group_flow(flow)
                return

            if flow.kind == "youtube_radio_track_search":
                if not flow.results:
                    self.show_status("No YouTube-backed matched media selected.")
                    return
                index = max(0, min(flow.selected_index, len(flow.results) - 1))
                item = flow.results[index]
                flow.song_id = item.get("song_id")
                flow.collection_name = f"{item.get('title', 'Track')} Radio"
                flow.setting_path = self.youtube_id_from_item(item)
                flow.kind = "youtube_radio_count"
                flow.query = str(self.config.get("youtube.max_results", 25) or 25)
                flow.selected_index = 0
                await self.render_management_flow()
                return

            if flow.kind == "youtube_radio_count":
                await self.commit_youtube_radio_collection(flow)
                return

            if flow.kind == "add_media_choice":
                if flow.selected_index == 0:
                    flow.kind = "new_media_form"
                    flow.fields = [("Title", ""), ("Artist", ""), ("Media Type", "music"), ("URL", "")]
                    flow.field_index = 0
                    flow.selected_index = 0
                    await self.render_management_flow()
                elif flow.selected_index == 1:
                    flow.kind = "external_media_url"
                    flow.query = ""
                    await self.render_management_flow()
                else:
                    flow.kind = "local_file_path"
                    flow.query = ""
                    await self.render_management_flow()
                return

            if flow.kind == "local_file_path":
                await self.commit_local_file_path(flow)
                return

            if flow.kind == "add_track_choice":
                if flow.selected_index == 0:
                    flow.kind = "existing_media_search"
                    flow.query = ""
                    flow.results = []
                    flow.selected_index = 0
                    await self.render_management_flow()
                elif flow.selected_index == 1:
                    flow.kind = "new_media_form"
                    flow.fields = [("Title", ""), ("Artist", ""), ("Media Type", "music"), ("URL", "")]
                    flow.field_index = 0
                    flow.selected_index = 0
                    await self.render_management_flow()
                elif flow.selected_index == 2:
                    flow.kind = "external_media_url"
                    flow.query = ""
                    await self.render_management_flow()
                else:
                    flow.kind = "local_file_path"
                    flow.query = ""
                    await self.render_management_flow()
                return

            if flow.kind == "external_media_url":
                await self.commit_external_media_url(flow)
                return

            if flow.kind == "circuit_source_select":
                if not flow.results:
                    self.show_status("No circuit source selected.")
                    return
                source_config, source_label = self.source_config_from_flow(flow)
                default_name = f"{source_label} Circuit"
                flow.kind = "circuit_name"
                flow.payload = {"source_config": source_config, "source_label": source_label}
                flow.collection_name = default_name
                flow.query = default_name
                flow.selected_index = 0
                await self.render_management_flow()
                return

            if flow.kind == "circuit_name":
                name = flow.query.strip()
                if not name:
                    self.show_status("Circuit name cannot be empty.")
                    return
                flow.collection_name = name
                flow.kind = "circuit_target_count"
                flow.query = str(flow.payload.get("target_count") or 25)
                flow.selected_index = 0
                await self.render_management_flow()
                return

            if flow.kind == "circuit_target_count":
                try:
                    target_count = int(flow.query.strip())
                except ValueError:
                    self.show_status("Enter a number of tracks.")
                    return
                if target_count <= 0:
                    self.show_status("Track count must be greater than zero.")
                    return
                flow.payload["target_count"] = target_count
                roots = self.configured_export_roots()
                if not roots:
                    self.show_status("No export roots configured.")
                    return
                flow.kind = "circuit_root_select"
                flow.results = [{"path": str(root), "display": str(root)} for root in roots]
                flow.selected_index = 0
                await self.render_management_flow()
                return

            if flow.kind == "circuit_root_select":
                if not flow.results:
                    self.show_status("No export roots configured.")
                    return
                index = max(0, min(flow.selected_index, len(flow.results) - 1))
                selected = flow.results[index]
                flow.payload["export_root"] = selected.get("path")
                flow.kind = "circuit_target_path"
                flow.query = ""
                flow.selected_index = 0
                await self.render_management_flow()
                return

            if flow.kind == "circuit_target_path":
                await self.commit_circuit_target_path(flow)
                return

            if flow.kind == "circuit_edit_source_select":
                source_config, _source_label = self.source_config_from_flow(flow)
                flow.payload["source_config"] = source_config
                policy = (flow.payload.get("circuit") or {}).get("fill_policy") or {}
                flow.kind = "circuit_edit_target_count"
                flow.query = str(policy.get("target_count") or policy.get("limit") or 25)
                flow.selected_index = 0
                await self.render_management_flow()
                return

            if flow.kind == "circuit_edit_target_count":
                try:
                    target_count = int(flow.query.strip())
                except ValueError:
                    self.show_status("Enter a number of tracks.")
                    return
                if target_count <= 0:
                    self.show_status("Track count must be greater than zero.")
                    return
                self.circuit_manager.update_circuit(
                    flow.setting_path,
                    source_config=flow.payload.get("source_config"),
                    target_count=target_count,
                )
                self.management_flow = None
                self.query_one(ContentSwitcher).current = "circuits" if flow.origin_screen_id == "circuit_details" else flow.origin_screen_id
                await self.refresh_circuits_view()
                self._focus_main_content(self.query_one(ContentSwitcher).current)
                self.update_current_context_actions()
                self.show_status(f"Updated circuit: {flow.collection_name or 'Circuit'}.")
                return

            if flow.kind == "circuit_circulate_confirm":
                self.management_flow = None
                self.query_one(ContentSwitcher).current = flow.origin_screen_id
                self._focus_main_content(flow.origin_screen_id)
                self.update_current_context_actions()
                self.submit_circulate_job(flow.setting_path, flow.collection_name or "Circuit", flow.payload.get("plan"))
                await self.refresh_jobs_view()
                return

            if flow.kind == "circuit_delete_confirm":
                await self.commit_delete_circuit_flow(flow)
                return

            if flow.kind == "export_collection_select":
                if not flow.results:
                    self.show_status("No collection selected.")
                    return
                index = max(0, min(flow.selected_index, len(flow.results) - 1))
                collection = flow.results[index]
                flow.collection_id = collection.get("collection_id")
                flow.collection_name = collection.get("name", "Collection")
                await self.prepare_export_mode_or_confirm(flow)
                await self.render_management_flow()
                return

            if flow.kind == "export_mode_select":
                flow.setting_type = "STATIC" if flow.selected_index == 0 else "MANAGED"
                if flow.setting_type == "STATIC" and flow.collection_id:
                    preflight = self.export_manager.export_preflight(flow.collection_id)
                    if preflight.get("missing", 0) > 0:
                        flow.kind = "export_missing_confirm"
                        flow.payload = preflight
                        flow.options = ["Continue Export", "Cancel"]
                        flow.selected_index = 0
                        await self.render_management_flow()
                        return
                if not await self.prepare_export_root_select(flow):
                    return
                await self.render_management_flow()
                return

            if flow.kind == "export_root_select":
                if not flow.results:
                    self.show_status("No export roots configured.")
                    return
                index = max(0, min(flow.selected_index, len(flow.results) - 1))
                selected = flow.results[index]
                flow.payload["export_root"] = selected.get("path")
                flow.kind = "export_target_path"
                flow.query = ""
                flow.selected_index = 0
                await self.render_management_flow()
                return

            if flow.kind == "export_missing_confirm":
                if flow.selected_index == 0:
                    if not await self.prepare_export_root_select(flow):
                        return
                    flow.selected_index = 0
                    await self.render_management_flow()
                else:
                    await self.cancel_management_flow()
                return

            if flow.kind == "update_export_missing_confirm":
                if flow.selected_index == 0:
                    self.management_flow = None
                    self.query_one(ContentSwitcher).current = flow.origin_screen_id
                    self._focus_main_content(flow.origin_screen_id)
                    self.update_current_context_actions()
                    self.submit_update_export_job(
                        flow.setting_path,
                        flow.payload.get("collection_id"),
                        flow.payload.get("collection_name") or "Managed Export",
                    )
                    await self.refresh_jobs_view()
                else:
                    await self.cancel_management_flow()
                return

            if flow.kind == "export_target_path":
                await self.commit_export_target_path(flow)
                return

            if flow.kind == "export_shuffle_choice":
                if flow.selected_index == 2:
                    await self.cancel_management_flow()
                    return
                if flow.selected_index == 0:
                    self.management_flow = None
                    self.query_one(ContentSwitcher).current = flow.origin_screen_id
                    self._focus_main_content(flow.origin_screen_id)
                    self.update_current_context_actions()
                    self.submit_shuffle_export_job(flow.setting_path, flow.collection_id, flow.collection_name or "Managed Export", None)
                    await self.refresh_jobs_view()
                    return
                flow.kind = "export_shuffle_count"
                flow.query = ""
                flow.selected_index = 0
                await self.render_management_flow()
                return

            if flow.kind == "export_shuffle_count":
                try:
                    limit = int(flow.query.strip())
                except ValueError:
                    self.show_status("Enter a whole number of tracks.")
                    return
                downloaded = int(flow.payload.get("downloaded", 0) or 0)
                if limit < 1:
                    self.show_status("Shuffle count must be at least 1.")
                    return
                if limit > downloaded:
                    self.show_status(f"Only {downloaded} downloaded tracks are available.")
                    return
                self.management_flow = None
                self.query_one(ContentSwitcher).current = flow.origin_screen_id
                self._focus_main_content(flow.origin_screen_id)
                self.update_current_context_actions()
                self.submit_shuffle_export_job(flow.setting_path, flow.collection_id, flow.collection_name or "Managed Export", limit)
                await self.refresh_jobs_view()
                return

            if flow.kind == "export_delete_choice":
                if flow.selected_index == 2:
                    await self.cancel_management_flow()
                    return
                flow.setting_type = "unmanage" if flow.selected_index == 0 else "delete_folder"
                flow.kind = "export_delete_confirm"
                flow.selected_index = 0
                await self.render_management_flow()
                return

            if flow.kind == "export_delete_confirm":
                if flow.selected_index == 0:
                    await self.commit_export_delete(flow)
                else:
                    await self.cancel_management_flow()
                return

            if flow.kind == "export_clear_confirm":
                if flow.selected_index == 0:
                    await self.commit_export_clear(flow)
                else:
                    await self.cancel_management_flow()
                return

            if flow.kind == "command_palette":
                await self.execute_palette_command(flow)
                return

            if flow.kind == "existing_media_search":
                if not flow.results:
                    self.show_status("No media item selected.")
                    return
                selected = sorted(flow.selected_indices) or [max(0, min(flow.selected_index, len(flow.results) - 1))]
                added_count = 0
                for idx in selected:
                    if 0 <= idx < len(flow.results):
                        self.collection_manager.add_item_to_collection(flow.collection_id, flow.results[idx]["song_id"])
                        added_count += 1
                message = "Added track to collection." if added_count == 1 else f"Added {added_count} tracks to collection."
                await self.finish_collection_details_mutation(message)
                return

            if flow.kind == "new_media_form":
                await self.commit_new_media_flow(flow)
                return

            if flow.kind == "setting_value":
                await self.commit_setting_value_flow(flow)
                return

            if flow.kind == "setting_list_value":
                await self.commit_setting_list_value_flow(flow)
                return

            if flow.kind == "penalty_form":
                await self.commit_penalty_form(flow)
                return

            if flow.kind == "remove_track_confirm":
                if flow.selected_index == 0:
                    self.collection_manager.remove_item_from_collection(flow.collection_id, flow.song_id)
                    await self.finish_collection_details_mutation("Removed track from collection.", flow.origin_index)
                else:
                    await self.cancel_management_flow()
                return

            if flow.kind == "delete_standalone_confirm":
                if flow.selected_index == 0:
                    self.collection_manager.delete_standalone_item(flow.song_id)
                    await self.finish_media_deleted()
                else:
                    await self.cancel_management_flow()
        except Exception as exc:
            self.show_status(f"Operation failed: {exc}")

    async def commit_new_media_flow(self, flow: ManagementFlow) -> None:
        values = {label: value.strip() for label, value in flow.fields}
        title = values.get("Title", "")
        artist = values.get("Artist", "")
        media_type = values.get("Media Type", "") or "music"
        url = values.get("URL", "") or None
        if not title:
            self.show_status("Title cannot be empty.")
            return
        if not artist:
            self.show_status("Artist cannot be empty.")
            return

        song_id = self.collection_manager.add_standalone_item(title, artist, media_type, url)
        if flow.collection_id:
            self.collection_manager.add_item_to_collection(flow.collection_id, song_id)
            await self.finish_collection_details_mutation("Created and added media item.")
        else:
            await self.finish_media_created(song_id)

    async def finish_media_created(self, song_id: str) -> None:
        self.management_flow = None
        self.query_one(ContentSwitcher).current = "media"
        screen = self.query_one("#media", MediaScreen)
        await screen.refresh_list()
        for idx, item in enumerate(getattr(screen, "_display_data", [])):
            if item.get("song_id") == song_id:
                screen.set_list_index(idx)
                break
        self._focus_main_content("media")
        self.update_current_context_actions()
        self.show_status("Created media item.")

    async def finish_media_deleted(self) -> None:
        flow = self.management_flow
        origin = flow.origin_screen_id if flow else "media"
        self.management_flow = None
        target = "media" if origin == "media_details" else origin
        self.query_one(ContentSwitcher).current = target
        if target == "media":
            screen = self.query_one("#media", MediaScreen)
            await screen.refresh_list()
            if flow:
                screen.set_list_index(flow.origin_index)
        self._focus_main_content(target)
        self.update_current_context_actions()
        self.show_status("Deleted standalone media item.")

    # --- Navigation & Utilities ---
    def default_filter_ui_state(self) -> dict:
        return dict(DEFAULT_FILTER_UI_STATE)

    def normalize_filter_ui_state(self, state: dict | None) -> dict:
        normalized = self.default_filter_ui_state()
        if state:
            normalized.update(state)
        normalized["text_contains"] = str(normalized.get("text_contains") or "")
        return normalized

    def query_from_filter_state(self, state: dict | None, context: str | None = None) -> MediaQuery:
        state = self.normalize_filter_ui_state(state)
        query = MediaQuery()
        query.status_groups = [
            group for key, group in STATUS_FILTER_GROUPS.items()
            if state.get(key)
        ]
        query.media_types = [
            media_type for key, media_type in MEDIA_TYPE_FILTERS.items()
            if state.get(key)
        ]
        query.feedback_ratings = [
            rating for key, rating in FEEDBACK_FILTERS.items()
            if state.get(key)
        ]
        analysis_statuses = [
            status for key, status in AUDIO_ANALYSIS_FILTERS.items()
            if state.get(key)
        ]
        if analysis_statuses:
            query.audio_analysis_statuses = analysis_statuses
        feature_filters: dict[str, list[int]] = {}
        for key, (field, score) in AUDIO_FEATURE_FILTER_KEYS.items():
            if state.get(key):
                feature_filters.setdefault(field, []).append(score)
        if feature_filters:
            query.audio_feature_filters = feature_filters
        audio_rule = state.get("audio_rule")
        if isinstance(audio_rule, dict):
            query.audio_feature_rule = copy.deepcopy(audio_rule)
        if state.get("flag_file_missing"):
            query.file_missing = True
        if context == "media" and state.get("flag_orphaned"):
            query.orphaned = True
        if context == "collection_details":
            query.sort_by = "collection_order"
        text_val = str(state.get("text_contains") or "").strip()
        if text_val:
            query.text = text_val
        return query

    def active_count_query(self, base_query: MediaQuery | None = None) -> MediaQuery:
        query = copy.copy(base_query) if base_query else MediaQuery()
        query.status_groups = ["discovered", "matched", "failed", "downloaded", "review"]
        query.downloaded = None
        query.matched = None
        query.archived = None
        query.in_review_queue = None
        query.file_missing = None
        query.orphaned = None
        query.audio_analysis_statuses = None
        query.audio_feature_filters = None
        query.audio_feature_rule = None
        query.feedback_ratings = None
        query.text = None
        return query

    def filter_count_text(self, shown_count: int, active_count: int, context: str) -> str:
        if self.is_default_filter(context):
            return f"{active_count} active"
        return f"{active_count} active, showing {shown_count}"

    def is_default_filter(self, context: str) -> bool:
        state = self.media_filter_ui_state if context == "media" else self.collection_filter_ui_state
        return self.normalize_filter_ui_state(state) == self.default_filter_ui_state()

    def action_navigate(self, target_id: str) -> None:
        self.commit_collection_draft_if_leaving(target_id)
        self.clear_media_selection_if_outside_lists(target_id)
        if target_id != "media":
            self.media_filter_ui_state = self.default_filter_ui_state()
            self.media_filter = self.query_from_filter_state(self.media_filter_ui_state, "media")
            
        if target_id not in ("collections", "collection_details"):
            self.collection_filter_ui_state = self.default_filter_ui_state()
            self.collection_filter = self.query_from_filter_state(self.collection_filter_ui_state, "collection_details")

        if target_id in self.NAV_MAP:
            self._nav_stack = [target_id]
            self.query_one(ContentSwitcher).current = target_id
            self.query_one("#sidebar-list", ListView).index = self.NAV_MAP[target_id][1]
            self._focus_main_content(target_id)
            self.update_current_context_actions()

    def _show_screen(self, target_id: str, *, preserve_stack: bool = False) -> None:
        self.commit_collection_draft_if_leaving(target_id)
        self.clear_media_selection_if_outside_lists(target_id)
        if not preserve_stack:
            self._nav_stack.append(target_id)
        self.query_one(ContentSwitcher).current = target_id

        self._focus_main_content(target_id)
        self.update_current_context_actions()

    def open_details_screen(self, target_id: str) -> None:
        self._show_screen(target_id)

    def action_go_back(self) -> None:
        if len(self._nav_stack) > 1:
            self._nav_stack.pop()
            previous_target = self._nav_stack[-1]
            self._show_screen(previous_target, preserve_stack=True)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        self.update_current_context_actions()

    def update_current_context_actions(self) -> None:
        if self.management_flow or self.input_mode != "NORMAL" or self.review_session:
            return
        current = self.query_one(ContentSwitcher).current
        if current == "collections":
            self.update_context_actions(self.collections_context_actions())
        elif current == "collection_details":
            self.update_context_actions(self.collection_details_actions())
        elif current == "media":
            self.update_context_actions(self.media_context_actions())
        elif current == "media_details":
            self.update_context_actions(self.media_details_actions())
        elif current in ("circuits", "circuit_details"):
            self.update_circuits_context_actions()
        elif current == "circuit_preview":
            self.update_context_actions(self.circuit_preview_context_actions())
        elif current == "circuit_track_feedback":
            self.update_context_actions("Esc    Back\nEnter  Details\nL      Love\nB      Boo\nG      Global/Here")
        elif current == "circuit_feedback_debug":
            self.update_context_actions("Esc    Back\nR      Refresh")
        elif current in ("exports", "export_details"):
            self.update_exports_context_actions()
        elif current == "jobs":
            self.update_jobs_context_actions()
        elif current == "job_details":
            self.update_context_actions(self.job_details_context_actions())
        elif current == "settings":
            self.update_settings_actions()
        elif current == "collection_history":
            self.update_context_actions("Esc    Back\nEnter  Open Snapshot\nC      Restore As Copy")
        elif current == "collection_snapshot":
            self.update_context_actions("Esc    Back\nC      Restore As Copy")
        elif current == "filter":
            self.update_context_actions(self.filter_context_actions())
        elif current == "vibe_editor":
            self.update_context_actions(self.vibe_editor_actions())
        elif current == "vibe_preview":
            self.update_context_actions("Enter  Open\nLeft/Right Page\nBracket Keys  Jump 10 Pages\nP      Play\nEsc    Back")
        elif current == "audio_analysis_summary":
            self.update_context_actions("Esc    Back\nR      Refresh")
        elif current == "audio_matrix":
            self.update_context_actions("Esc    Back\nEnter  Open Cell\nR      Reset")
        elif current in self.NAV_MAP:
            self.update_context_actions(self.NAV_MAP[current][2])

    def collections_context_actions(self) -> str:
        try:
            screen = self.query_one("#collections", CollectionsScreen)
            kind = screen.selected_row_kind()
            group = screen.get_selected_group()
        except Exception:
            kind = None
            group = None
        if kind == "group":
            lines = ["Enter  Collapse/Expand", "A      Add Collection"]
            if group and group.get("group_id") is not None:
                lines.append("X      Delete Group")
            lines.extend(["J      Jump", "F      Find", "S      Sort"])
            return "\n".join(lines)
        collection = self.get_selected_collection()
        if not collection:
            return "A      Add Collection\nJ      Jump\nF      Find\nS      Sort"
        if self.collection_is_archived(collection):
            return "\n".join(["Enter  Open", "C      Copy", "H      History", "X      Unarchive", "J      Jump", "F      Find", "S      Sort"])
        lines = ["Enter  Open", "A      Add Collection", "C      Copy", "G      Group", "H      History", "P      Play Collection"]
        if not self.is_manual_collection(collection):
            lines.append("R      Refresh")
        lines.extend(["D      Download", "E      Export", "X      Archive", "J      Jump", "F      Find", "S      Sort"])
        return "\n".join(lines)

    def media_context_actions(self) -> str:
        item = self.get_selected_media_item()
        lines = ["Enter  Open", "Left/Right Page", "Bracket Keys  Jump 10 Pages", "A      Add Media", "T      Filter", "J      Jump", "F      Find", "S      Sort"]
        if not item:
            return "\n".join(lines)
        lines.extend(["L      Love", "B      Boo"])
        lines.extend(self.media_item_action_lines(item, include_edit=False))
        return "\n".join(lines)

    def filter_context_actions(self) -> str:
        try:
            screen = self.query_one("#filter", FilterScreen)
            highlighted = screen.highlighted_child()
        except Exception:
            screen = None
            highlighted = None
        if isinstance(highlighted, FilterText):
            return "Type   Edit Text\nBackspace Delete\nLeft/Right Column\nEsc    Cancel"
        if screen and screen.active_column == "right":
            return "T      Apply Filter\nA      Add Rule\nG      New Group\nE/Enter Edit Rule\nD      Delete\nL      Loosen\nC      Clear Filter\nLeft/Right Column\nEsc    Cancel"
        return "T      Apply Filter\nC      Clear Filter\nEnter  Toggle/Edit\nLeft/Right Column\nEsc    Cancel"

    def filter_rule_hint_actions(self) -> str:
        forced_field = None
        if self.input_mode == "FILTER_RULE_LEVEL":
            forced_field = (getattr(self, "vibe_pending_rule", None) or {}).get("field")
        return "\n".join(self.vibe_rule_hint_tokens(self.input_query, forced_field))

    def filter_rule_input_active(self) -> bool:
        return self.input_mode in ("FILTER_RULE_FEATURE", "FILTER_RULE_LEVEL")

    async def append_filter_rule_input_character(self, character: str) -> None:
        self.input_query += character
        await self._update_input_state()

    def media_details_actions(self) -> str:
        item = self.get_selected_media_item()
        lines = ["Esc    Back"]
        if item:
            lines.extend(self.media_item_action_lines(item, include_edit=False))
        return "\n".join(lines)

    def job_details_context_actions(self) -> str:
        job_id = self.get_selected_job_id()
        if not job_id:
            return "Esc    Back"
        lines = ["Esc    Back"]
        key_map = {"Pause": "P", "Resume": "R", "Cancel": "C", "Retry": "T", "Remove": "D"}
        for action in self.job_manager.get_available_actions(job_id):
            key = key_map.get(action)
            if key:
                lines.append(f"{key}      {action}")
        return "\n".join(lines)

    def collection_is_archived(self, collection: dict | None) -> bool:
        return str((collection or {}).get("status") or "").upper() == "ARCHIVED"

    def media_is_archived(self, item: dict | None) -> bool:
        return str((item or {}).get("song_status") or (item or {}).get("status") or "").upper() == "ARCHIVED"

    def media_has_file(self, item: dict | None) -> bool:
        return bool((item or {}).get("filepath") or (item or {}).get("file_path"))

    def media_is_matched(self, item: dict | None) -> bool:
        if not item:
            return False
        status = str(item.get("song_status") or item.get("status") or "").upper()
        if status in ("REVIEW", "MATCH_FAILED"):
            return False
        return status == "MATCHED" or bool(item.get("youtube_id") or item.get("acquisition_info"))

    def media_is_match_failed(self, item: dict | None) -> bool:
        return str((item or {}).get("song_status") or (item or {}).get("status") or "").upper() == "MATCH_FAILED"

    def media_is_download_ready(self, item: dict | None, *, force: bool = False) -> bool:
        if not item or self.media_has_file(item):
            return False
        if self.media_is_match_failed(item):
            return force
        return self.media_is_matched(item)

    def media_item_action_lines(self, item: dict, *, include_edit: bool = False) -> list[str]:
        if self.media_is_archived(item):
            lines = ["X      Unarchive"]
            if include_edit:
                lines.append("E      Edit")
            return lines
        lines = []
        if self.media_has_file(item):
            lines.append("P      Play")
        if self.media_is_match_failed(item):
            lines.append("M      Force Rematch for Review")
            lines.append("D      Force Download")
        else:
            lines.append(f"M      {'Rematch' if self.media_is_matched(item) else 'Match'}")
            if self.media_is_matched(item):
                lines.append("D      Download")
        if str(item.get("status") or item.get("song_status") or "").upper() == "REVIEW":
            lines.append("V      Review")
        lines.append("X      Archive")
        if include_edit:
            lines.append("E      Edit")
        return lines

    async def action_action_a(self) -> None:
        if self.filter_rule_input_active():
            await self.append_filter_rule_input_character("a")
            return
        current = self.query_one(ContentSwitcher).current
        if current == "filter":
            screen = self.query_one("#filter", FilterScreen)
            if screen.text_filter_focused():
                screen.append_text_filter_character("a")
            else:
                screen.focus_column("right")
                screen.start_rule_input()
        elif current == "settings":
            await self.start_settings_add_flow()
        elif current == "circuits":
            await self.start_circuit_flow()
        elif current == "exports":
            await self.start_export_flow()
        elif current == "collections":
            await self.start_management_flow(
                ManagementFlow(
                    kind="add_collection_choice",
                    origin_screen_id="collections",
                    origin_actions=self.current_context_actions,
                    options=["Manual Collection", "Spotify/YouTube/Billboard URL" if "spotify" in self.plugins else "YouTube/Billboard URL", "Local Folder", "YouTube Radio From Library Track", "Vibe Collection"],
                )
            )

        elif current == "collection_details":
            await self.start_add_track_flow()
        elif current == "vibe_editor":
            await self.start_vibe_add_rule()
        elif current == "media":
            await self.start_management_flow(
                ManagementFlow(
                    kind="add_media_choice",
                    origin_screen_id="media",
                    origin_actions=self.current_context_actions,
                    options=["Manual Media Item", "Spotify/YouTube URL", "Local File"],
                )
            )
        elif current == "media_details":
            await self.start_archive_confirmation()
        else:
            self.show_status("Add is not available here.")

    async def action_action_c(self) -> None:
        if self.filter_rule_input_active():
            await self.append_filter_rule_input_character("c")
            return
        current = self.query_one(ContentSwitcher).current
        if current == "filter":
            screen = self.query_one("#filter", FilterScreen)
            if screen.text_filter_focused():
                screen.append_text_filter_character("c")
            else:
                screen.execute_action("clear")
            return
        if current in ("jobs", "job_details"):
            self.run_job_action("Cancel")
            return
        if current in ("circuits", "circuit_details"):
            await self.start_circulate_flow()
            return
        if current in ("exports", "export_details"):
            await self.start_clear_export_flow()
            return
        if current == "vibe_editor":
            await self.start_vibe_rule_transfer("copy")
            return
        if current in ("collections", "collection_details"):
            await self.start_copy_collection_flow()
            return
        if current in ("collection_history", "collection_snapshot"):
            await self.start_restore_collection_copy_flow()
            return
        self.show_status("Copy/Clear is not available here.")

    async def action_action_b(self) -> None:
        if self.filter_rule_input_active():
            await self.append_filter_rule_input_character("b")
            return
        current = self.query_one(ContentSwitcher).current
        if current == "circuit_preview":
            self.query_one("#circuit_preview", CircuitCirculationPreviewScreen).toggle_selected_feedback("disliked")
        elif current == "filter":
            screen = self.query_one("#filter", FilterScreen)
            if screen.text_filter_focused():
                screen.append_text_filter_character("b")
            else:
                self.show_status("Boo is only available in track lists.")
        elif current in ("media", "collection_details"):
            await self.toggle_selected_track_global_feedback("disliked")
        elif current == "circuit_track_feedback":
            await self.query_one("#circuit_track_feedback", CircuitTrackFeedbackScreen).toggle_selected_feedback("disliked")
        else:
            self.show_status("Boo is only available in track lists.")

    async def action_action_d(self) -> None:
        if self.filter_rule_input_active():
            await self.append_filter_rule_input_character("d")
            return
        current = self.query_one(ContentSwitcher).current
        if current in ("circuit_preview", "circuit_track_feedback"):
            self.show_status("Use B to boo.")
        elif current == "filter":
            screen = self.query_one("#filter", FilterScreen)
            if screen.text_filter_focused():
                screen.append_text_filter_character("d")
            else:
                screen.delete_selected_rule()
        elif current == "settings":
            await self.remove_selected_setting_item()
        elif current in ("circuits", "circuit_details"):
            await self.start_delete_circuit_flow()
        elif current in ("exports", "export_details"):
            await self.start_export_delete_flow()
        elif current in ("jobs", "job_details"):
            self.run_job_action("Remove")
        elif current == "vibe_editor":
            await self.delete_selected_vibe_item()
        else:
            await self.create_download_job()

    async def action_action_h(self) -> None:
        if self.filter_rule_input_active():
            await self.append_filter_rule_input_character("h")
            return
        current = self.query_one(ContentSwitcher).current
        if current == "collections":
            collection = self.get_selected_collection()
            if not collection:
                self.show_status("No collection selected.")
                return
            self.current_collection_id = collection.get("collection_id")
            self.current_revision_id = None
            self.open_details_screen("collection_history")
        elif current == "collection_details":
            if not getattr(self, "current_collection_id", None):
                self.show_status("No collection selected.")
                return
            self.current_revision_id = None
            self.open_details_screen("collection_history")
        else:
            self.show_status("History is only available for collections.")

    async def action_action_g(self) -> None:
        if self.filter_rule_input_active():
            await self.append_filter_rule_input_character("g")
            return
        current = self.query_one(ContentSwitcher).current
        if current == "circuit_preview":
            self.query_one("#circuit_preview", CircuitCirculationPreviewScreen).toggle_feedback_scope()
        elif current == "circuit_track_feedback":
            self.query_one("#circuit_track_feedback", CircuitTrackFeedbackScreen).toggle_feedback_scope()
        elif current == "filter":
            screen = self.query_one("#filter", FilterScreen)
            if screen.text_filter_focused():
                screen.append_text_filter_character("g")
            else:
                screen.focus_column("right")
                await screen.add_group_and_rule()
        elif current == "collections":
            await self.start_collection_group_flow()
        elif current == "vibe_editor":
            await self.start_vibe_new_group()
        else:
            self.show_status("Group is only available from Collections.")

    async def action_action_m(self) -> None:
        if self.filter_rule_input_active():
            await self.append_filter_rule_input_character("m")
            return
        current = self.query_one(ContentSwitcher).current
        if current == "vibe_editor":
            await self.start_vibe_rule_transfer("move")
        else:
            await self.create_match_job()
    def action_action_p(self) -> None:
        if self.filter_rule_input_active():
            asyncio.create_task(self.append_filter_rule_input_character("p"))
            return
        current = self.query_one(ContentSwitcher).current
        if current in ("jobs", "job_details"):
            self.run_job_action("Pause")
        elif current == "vibe_editor":
            asyncio.create_task(self.preview_vibe_matches())
        elif current == "collections":
            self.play_selected_collection()
        else:
            self.play_selected_track()

    async def action_action_o(self) -> None:
        if self.filter_rule_input_active():
            await self.append_filter_rule_input_character("o")
            return
        await self.start_remove_track_flow()

    async def action_action_r(self) -> None:
        if self.filter_rule_input_active():
            await self.append_filter_rule_input_character("r")
            return
        current = self.query_one(ContentSwitcher).current
        if current in ("jobs", "job_details"):
            self.run_job_action("Resume")
        elif current == "audio_analysis_summary":
            await self.query_one("#audio_analysis_summary", AudioAnalysisSummaryScreen).refresh_summary()
        elif current == "audio_matrix":
            await self.query_one("#audio_matrix", AudioMatrixScreen).refresh_report()
        elif current == "circuit_feedback_debug":
            await self.query_one("#circuit_feedback_debug", CircuitFeedbackDebugScreen).refresh_feedback()
        elif current == "settings":
            await self.reset_selected_settings_category()
        elif current in ("exports", "export_details"):
            await self.create_recover_exports_job()
        else:
            await self.create_refresh_job()

    def action_action_t(self) -> None:
        if self.filter_rule_input_active():
            asyncio.create_task(self.append_filter_rule_input_character("t"))
            return
        current = self.query_one(ContentSwitcher).current
        if current in ("jobs", "job_details"):
            self.run_job_action("Retry")
        elif current in ("media", "collection_details", "media_details"):
            self.action_action_t_filter()
        else:
            self.action_action_t_filter()

    def action_action_t_filter(self) -> None:
        current = self.query_one(ContentSwitcher).current
        if current == "media":
            self.active_filter_context = "media"
            self.open_details_screen("filter")  
        elif current == "collection_details":
            self.active_filter_context = "collection_details"
            self.open_details_screen("filter")  
        elif current == "filter":
            screen = self.query_one("#filter", FilterScreen)
            if screen.text_filter_focused():
                screen.append_text_filter_character("t")
            else:
                screen.execute_action("apply")
        else:
            self.show_status("Filters only apply to Media and Collection Details.")

    async def action_action_u(self) -> None:
        if self.filter_rule_input_active():
            await self.append_filter_rule_input_character("u")
            return
        current = self.query_one(ContentSwitcher).current
        if current in ("exports", "export_details"):
            await self.create_update_export_job()
        else:
            self.show_status("Update is not available here.")

    async def action_action_l(self) -> None:
        if self.filter_rule_input_active():
            await self.append_filter_rule_input_character("l")
            return
        current = self.query_one(ContentSwitcher).current
        if current == "circuit_preview":
            self.query_one("#circuit_preview", CircuitCirculationPreviewScreen).toggle_selected_feedback("liked")
        elif current == "filter":
            screen = self.query_one("#filter", FilterScreen)
            if screen.text_filter_focused():
                screen.append_text_filter_character("l")
            else:
                screen.loosen_selected_rule()
        elif current in ("media", "collection_details"):
            await self.toggle_selected_track_global_feedback("liked")
        elif current == "circuit_track_feedback":
            await self.query_one("#circuit_track_feedback", CircuitTrackFeedbackScreen).toggle_selected_feedback("liked")
        elif current == "vibe_editor":
            await self.loosen_selected_vibe_rule()
        else:
            self.show_status("Loosen is only available in the Vibe editor.")
    async def action_action_e(self) -> None:
        if self.filter_rule_input_active():
            await self.append_filter_rule_input_character("e")
            return
        current = self.query_one(ContentSwitcher).current
        if current == "filter":
            screen = self.query_one("#filter", FilterScreen)
            if screen.text_filter_focused():
                screen.append_text_filter_character("e")
            else:
                screen.focus_column("right")
                screen.start_rule_input(edit=True)
        elif current == "vibe_editor":
            await self.start_vibe_edit_rule()
        elif current == "settings":
            self.start_selected_setting_edit_flow()
        elif current in ("circuits", "circuit_details"):
            await self.start_edit_circuit_flow()
        elif current in ("collections", "collection_details"):
            collection = self.get_selected_collection()
            if not collection:
                self.show_status("Export is only available for collections.")
                return
            await self.start_export_flow(collection)
        elif current == "media_details":
            self.show_status("Media metadata editing requires a core update method.")
        else:
            self.show_status("Export/Edit is not available here.")

    async def action_action_v(self) -> None:
        if self.filter_rule_input_active():
            await self.append_filter_rule_input_character("v")
            return
        await self.start_review_session()

    async def action_action_x(self) -> None:
        if self.filter_rule_input_active():
            await self.append_filter_rule_input_character("x")
            return
        current = self.query_one(ContentSwitcher).current
        if current == "collections":
            screen = self.query_one("#collections")
            if screen.get_selected_group() is not None:
                await self.start_delete_collection_group_flow()
            else:
                await self.start_archive_collection_flow()
        elif current == "collection_details":
            await self.start_archive_collection_flow()
        elif current in ("media", "media_details"):
            await self.start_archive_confirmation()
        else:
            self.show_status("Archive/Delete is not available here.")

    async def action_action_y(self) -> None:
        if self.filter_rule_input_active():
            await self.append_filter_rule_input_character("y")
            return
        current = self.query_one(ContentSwitcher).current
        self.show_status("Y is not assigned here.")

    async def toggle_selected_track_global_feedback(self, rating: str) -> None:
        item = self.get_selected_media_item()
        song_id = (item or {}).get("song_id")
        if not song_id:
            self.show_status("No track selected.")
            return
        current_rating = (item or {}).get("feedback_rating") or "neutral"
        new_rating = "neutral" if current_rating == rating else rating
        self.circuit_manager.set_feedback_rating(song_id, new_rating, scope="global", source="track_list")
        item["feedback_rating"] = new_rating
        current = self.query_one(ContentSwitcher).current
        if current == "media":
            self.query_one("#media", MediaScreen).refresh_selection_labels()
        elif current == "collection_details":
            self.query_one("#collection_details", CollectionDetailsScreen).refresh_selection_labels()
        self.show_status(f"Preference set to {feedback_display_label(new_rating)}.")

    async def action_next_media_page(self) -> None:
        await self.turn_media_page(1)

    async def action_previous_media_page(self) -> None:
        await self.turn_media_page(-1)

    async def action_next_media_pages_10(self) -> None:
        await self.turn_media_page(10)

    async def action_previous_media_pages_10(self) -> None:
        await self.turn_media_page(-10)

    async def turn_media_page(self, direction: int) -> None:
        if self._media_page_turn_running:
            return
        self._media_page_turn_running = True
        try:
            await self._turn_media_page(direction)
        finally:
            self._media_page_turn_running = False
            self._media_page_cooldown_until = monotonic() + 0.18

    async def _turn_media_page(self, direction: int) -> None:
        if self.management_flow and self.management_flow.kind == "youtube_radio_track_search":
            flow = self.management_flow
            page_size = max(1, flow.page_size)
            last_offset = max(0, ((len(flow.source_results) - 1) // page_size) * page_size) if flow.source_results else 0
            page_jump = max(1, abs(direction))
            target = min(last_offset, flow.page_offset + (page_size * page_jump)) if direction > 0 else max(0, flow.page_offset - (page_size * page_jump))
            if target == flow.page_offset:
                self.show_status("Already on the last page." if direction > 0 else "Already on the first page.")
                return
            flow.page_offset = target
            flow.selected_index = 0
            await self.render_management_flow()
            return
        current = self.query_one(ContentSwitcher).current
        screen = None
        if current == "media":
            screen = self.query_one("#media", MediaScreen)
        elif current == "collection_details":
            screen = self.query_one("#collection_details", CollectionDetailsScreen)
        elif current == "vibe_preview":
            screen = self.query_one("#vibe_preview", VibePreviewScreen)
        else:
            return
        page_jump = max(1, abs(direction))
        target_offset = screen._offset + (screen._page_size * page_jump * (1 if direction > 0 else -1))
        turned = await screen.go_to_offset(target_offset)
        if screen and turned:
            self.clear_media_selection()
            self.update_current_context_actions()
            return
        self.show_status("Already on the last page." if direction > 0 else "Already on the first page.")

    async def refresh_current_list(self) -> None:
        current = self.query_one(ContentSwitcher).current
        if current == "collections":
            await self.query_one("#collections", CollectionsScreen).refresh_list()
        elif current == "collection_details":
            await self.query_one("#collection_details", CollectionDetailsScreen).refresh_details()
        elif current == "media":
            await self.query_one("#media", MediaScreen).refresh_list()
        elif current == "media_details":
            await self.query_one("#media_details", MediaDetailsScreen).refresh_details()
        elif current in ("circuits", "circuit_details"):
            await self.refresh_circuits_view()
        elif current == "audio_analysis_summary":
            await self.query_one("#audio_analysis_summary", AudioAnalysisSummaryScreen).refresh_summary()
        elif current == "audio_matrix":
            await self.query_one("#audio_matrix", AudioMatrixScreen).refresh_report()
        elif current in ("exports", "export_details"):
            await self.refresh_exports_view()

    def refresh_dynamic_views(self) -> None:
        self.refresh_catalog_views_for_completed_jobs()
        current = self.query_one(ContentSwitcher).current
        if current == "home":
            try:
                asyncio.create_task(self.query_one("#home", HomeScreen).refresh_summary())
            except Exception:
                pass
        elif current == "jobs":
            asyncio.create_task(self.query_one("#jobs", JobsScreen).refresh_jobs())
        elif current == "job_details":
            try:
                self.query_one("#job_details", JobDetailsScreen).refresh_details()
            except Exception:
                pass
        elif current == "exports":
            try:
                asyncio.create_task(self.query_one("#exports", ExportsScreen).refresh_if_status_changed())
            except Exception:
                pass
        elif current == "export_details":
            try:
                asyncio.create_task(self.refresh_export_details_if_status_changed())
            except Exception:
                pass
        elif current == "audio_analysis_summary":
            try:
                asyncio.create_task(self.query_one("#audio_analysis_summary", AudioAnalysisSummaryScreen).refresh_summary())
            except Exception:
                pass

    def job_status_icon(self, job) -> str:
        status = job.status
        if status == JobStatus.QUEUED:
            return "Q"
        if status == JobStatus.RUNNING:
            return ">"
        if status == JobStatus.PAUSED:
            return "P"
        if status == JobStatus.COMPLETED:
            return "+"
        if status == JobStatus.FAILED:
            return "X"
        if status == JobStatus.CANCELLED:
            return "O"
        return "?"

    def format_history_row(self, row: dict) -> str:
        label = row.get("label") or "Snapshot"
        if label != "Current":
            label = self.format_history_timestamp(label)
        added = int(row.get("added_count") or 0)
        removed = int(row.get("removed_count") or 0)
        count = int(row.get("track_count") or 0)
        reason = str(row.get("reason") or "")
        reason_text = "" if reason == "current" else f"  {reason.replace('_', ' ').title()}"
        return f"{label:<22} +{added:<3} -{removed:<3} {count:>4} tracks{reason_text}"

    def format_history_timestamp(self, value: str) -> str:
        dt = parse_iso_datetime(value)
        if not dt:
            return value
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")

    def format_job_row(self, job) -> tuple[str, str]:
        status_name = job.status.name if hasattr(job.status, "name") else str(job.status)
        title = f"{self.job_status_icon(job)} {job.description}"
        if job.progress_total > 0:
            bar = self.format_progress_bar(job.progress_current, job.progress_total, width=12)
            detail = f"{bar} {job.progress_current}/{job.progress_total}"
        else:
            detail = status_name.title()
        return title, detail

    def format_progress_bar(self, current: int, total: int, width: int = 12) -> str:
        if total <= 0:
            return "[" + (" " * width) + "]"
        filled = max(0, min(width, int(width * current / total)))
        return "[" + ("#" * filled) + ("." * (width - filled)) + "]"

    def _focus_main_content(self, screen_id: str) -> None:
        list_ids = {
            "home": "#home-action-list", "collections": "#collection-list", 
            "collection_details": "#cd-track-list", "media": "#media-list", 
            "circuits": "#circuit-list",
            "circuit_track_feedback": "#circuit-track-feedback-list",
            "circuit_feedback_debug": "#circuit-feedback-debug-list",
            "exports": "#export-list", "jobs": "#jobs-list", "settings": "#settings-list",
            "filter": "#filter-options-list", "review_session": "#review-candidate-list", "management_flow": "#management-list",
            "collection_history": "#collection-history-list", "collection_snapshot": "#collection-snapshot-list",
            "circuit_preview": "#circuit-preview-list",
        }
        try:
            if screen_id in list_ids:
                list_view = self.query_one(list_ids[screen_id], ListView)
                if list_view.index is None: list_view.index = 0
                list_view.focus()
            else:
                self.query_one(f"#{screen_id}").focus()
        except Exception:
            self.query_one("#main-pane").focus()

    def update_context_actions(self, actions_text: str) -> None:
        self.current_context_actions = actions_text
        self.query_one("#context-actions", Label).update(actions_text)

    def status_icon(self, item: dict) -> str:
        status = (item.get('song_status') or item.get('status') or '').upper()
        if status == 'ARCHIVED':
            return 'A'
        if item.get('filepath') or item.get('file_path'):
            return "✓"

        status = (item.get('song_status') or item.get('status') or '').upper()
        if status == 'MATCH_FAILED': return 'M!'
        if status == 'REVIEW': return 'R'
        if status == 'MATCHED': return 'M'
        return '?'

    def selection_status_icon(self, item: dict) -> str:
        marker = "*" if self.is_media_selected(item.get("song_id")) else " "
        return f"{marker}{self.status_icon(item)}"

    def feedback_text_style(self, rating: str | None) -> str:
        if rating == "liked":
            return "magenta bold"
        if rating == "disliked":
            return "red bold"
        return "dim"

    def format_media_list_row(self, item: dict) -> Text:
        marker = "*" if self.is_media_selected(item.get("song_id")) else " "
        icon = self.status_icon(item)
        rating = item.get("feedback_rating") or item.get("global_rating") or "neutral"
        width = self.current_media_row_width()
        available = max(24, width - 6)
        artist_width = max(12, int(available * 0.30))
        title_width = max(12, available - artist_width - 1)
        if title_width + artist_width + 1 > available:
            title_width = max(8, available - artist_width - 1)
        title = self.fit_text(item.get("title") or "Unknown", title_width)
        artist = self.fit_text(item.get("artist") or "Unknown Artist", artist_width)
        row = Text(no_wrap=True, overflow="crop")
        row.append(marker)
        row.append(icon, style=self.status_text_style(item))
        row.append(" ")
        row.append(feedback_icon(rating), style=self.feedback_text_style(rating))
        row.append(f"  {title:<{title_width}} ")
        row.append(artist, style="dim")
        return row

    def current_media_row_width(self) -> int:
        try:
            current = self.query_one(ContentSwitcher).current
        except Exception:
            current = None
        selectors = {
            "media": "#media-list",
            "collection_details": "#cd-track-list",
            "vibe_preview": "#vibe-preview-list",
        }
        selector = selectors.get(str(current or ""))
        if selector:
            try:
                width = self.query_one(selector, ListView).size.width
                if width:
                    return max(24, int(width))
            except Exception:
                pass
        return 92

    def status_text_style(self, item: dict) -> str:
        status = (item.get('song_status') or item.get('status') or '').upper()
        if status == 'ARCHIVED':
            return "dim bold"
        if item.get('filepath') or item.get('file_path'):
            return "green bold"
        if status == 'MATCH_FAILED':
            return "red bold"
        if status == 'REVIEW':
            return "cyan bold"
        if status == 'MATCHED':
            return "yellow bold"
        return "dim"

    def fit_text(self, value: str, width: int) -> str:
        text = " ".join(str(value).split())
        if len(text) <= width:
            return text
        return text[: max(0, width - 3)].rstrip() + "..."

    def display_text(self, value: str) -> str:
        return " ".join(str(value).split())

    def status_class(self, item: dict) -> str:
        status = (item.get('song_status') or item.get('status') or '').upper()
        if status == 'ARCHIVED':
            return 'archived'
        if item.get('filepath') or item.get('file_path'):
            return 'success'

        status = (item.get('song_status') or item.get('status') or '').upper()
        if status == 'MATCH_FAILED': return 'error'
        if status == 'REVIEW': return 'review'
        if status == 'MATCHED': return 'warning'
        return 'discovered'

    def format_duration(self, duration_ms: int | None) -> str:
        try:
            total_seconds = int(duration_ms or 0) // 1000
        except (TypeError, ValueError):
            return 'Unknown'
        minutes, seconds = divmod(total_seconds, 60)
        return f"{minutes}:{seconds:02d}"

    def show_status(self, message: str, persistent: bool = False) -> None:
        bar = self.query_one("#status-bar", Label)
        message = self.format_status_message(message)
        bar.update(message)
        self._status_persistent = persistent
        if self._status_timer: self._status_timer.stop()
        if not persistent:
            self._status_timer = self.set_timer(2.0, self._reset_status_bar)

    def format_status_message(self, message: str, max_length: int = 160) -> str:
        message = re.sub(r"\x1b\[[0-9;]*m", "", str(message))
        message = " ".join(message.split())
        if len(message) > max_length:
            return message[: max_length - 3].rstrip() + "..."
        return message

    def _reset_status_bar(self) -> None:
        self._status_persistent = False
        # A pending timer callback may run while the screen is being unmounted.
        for bar in self.query("#status-bar").results(Label):
            bar.update("Status: Ready")

if __name__ == "__main__":
    try:
        print("Starting MusicSync TUI...", flush=True)
        app = MusicSyncApp()
        print("Launching interface...", flush=True)
        app.run()
    except Exception as exc:
        print("\nMusicSync failed to start.")
        print(f"{type(exc).__name__}: {exc}")
        try:
            input("\nPress Enter to exit...")
        except EOFError:
            pass
        raise
