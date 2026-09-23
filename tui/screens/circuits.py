import asyncio
from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Label, ListItem, ListView, Rule
from rich.text import Text


def circuit_folder_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    try:
        for child in path.rglob("*"):
            try:
                if child.is_file():
                    total += child.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


def format_circuit_size(size_bytes: int) -> str:
    if size_bytes >= 1024 ** 3:
        return f"{size_bytes / (1024 ** 3):.1f} GB"
    return f"{size_bytes / (1024 ** 2):.0f} MB"


def circuit_status(circuit: dict[str, Any]) -> dict[str, str]:
    target_dir = Path(circuit.get("target_path") or "")
    manifest = target_dir / ".musiccircuit.json"
    if not target_dir.exists():
        return {"label": "Folder Missing", "icon": "!", "class": "warning", "manifest": "Missing"}
    if not manifest.exists():
        return {"label": "Needs Manifest", "icon": "!", "class": "warning", "manifest": "Missing"}
    if int(circuit.get("last_track_count") or 0) <= 0:
        return {"label": "Ready", "icon": "○", "class": "empty", "manifest": "Present"}
    return {"label": "In Loop", "icon": "↻", "class": "loop", "manifest": "Present"}


def circuit_target_count(circuit: dict[str, Any]) -> int:
    policy = circuit.get("fill_policy") or {}
    return int(policy.get("target_count") or policy.get("limit") or 25)


def circuit_source_label(circuit: dict[str, Any]) -> str:
    source = circuit.get("source_config") or {}
    if source.get("type") == "all_downloaded":
        return "All Downloaded"
    if source.get("type") == "collections":
        names = source.get("collection_names") or []
        if names:
            return ", ".join(str(name) for name in names[:2]) + (f" +{len(names) - 2}" if len(names) > 2 else "")
        ids = source.get("collection_ids") or []
        return f"{len(ids)} collections" if ids else "Collections"
    if source.get("collection_name"):
        return str(source.get("collection_name"))
    if source.get("type") in ("collection", "collections"):
        return "Collection"
    return "Source"


def circuit_secondary_text(circuit: dict[str, Any]) -> str:
    return circuit_progress_label(circuit)


def circuit_size_text(circuit: dict[str, Any]) -> str:
    status = circuit_status(circuit)
    target_dir = Path(circuit.get("target_path") or "")
    return format_circuit_size(circuit_folder_size(target_dir)) if target_dir.exists() else status["label"]


def circuit_progress_label(circuit: dict[str, Any]) -> str:
    title = circuit.get("current_marker_title")
    if title:
        return f"Last heard: {title}"
    if circuit.get("current_marker_position"):
        return "Last heard: marker found"
    if int(circuit.get("last_track_count") or 0) > 0:
        return "Last heard: none yet"
    return "Ready for first circulate"


def circuit_fill_text(circuit: dict[str, Any]) -> str:
    fresh = int(circuit.get("current_fresh_count") or 0)
    target = circuit_target_count(circuit)
    return f"{fresh} of {target} fresh tracks"


def circuit_summary_text(circuits: list[dict[str, Any]]) -> str:
    if not circuits:
        return "No circuits yet. Add one to create a listening loop for a player folder."
    ready = sum(1 for circuit in circuits if int(circuit.get("last_track_count") or 0) <= 0 and circuit_status(circuit)["label"] == "Ready")
    active = sum(1 for circuit in circuits if int(circuit.get("last_track_count") or 0) > 0)
    missing = sum(1 for circuit in circuits if circuit_status(circuit)["label"] in ("Folder Missing", "Needs Manifest"))
    parts = [f"{len(circuits)} listening loop{'s' if len(circuits) != 1 else ''}"]
    if active:
        parts.append(f"{active} carrying music")
    if ready:
        parts.append(f"{ready} ready to fill")
    if missing:
        parts.append(f"{missing} need attention")
    return " | ".join(parts)


def feedback_icon(rating: str | None) -> str:
    if rating == "liked":
        return "♥"
    if rating == "disliked":
        return "✕"
    return "·"


def feedback_label(rating: str | None) -> str:
    if rating == "liked":
        return "loved"
    if rating == "disliked":
        return "booed"
    return "neutral"


class CircuitsScreen(Vertical):
    can_focus = False

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._display_data: list[dict] = []
        self._find_backup: list[dict] = []
        self._needs_refresh = True
        self._render_generation = 0

    def compose(self) -> ComposeResult:
        yield Label("Circuits", id="circuits-header", classes="section-header")
        yield Label("", id="circuits-summary", classes="circuit-summary")
        yield ListView(id="circuit-list")

    async def on_show(self) -> None:
        if self._needs_refresh:
            await self.refresh_circuits()
            self._needs_refresh = False

    async def refresh_circuits(self) -> None:
        previous_index = self.get_list_index()
        circuits = self.app.circuit_manager.get_circuits()
        self._display_data = circuits
        self.query_one("#circuits-header", Label).update(f"Circuits ({len(circuits)})")
        self.query_one("#circuits-summary", Label).update(circuit_summary_text(circuits))
        await self.render_circuits(previous_index=previous_index)
        self.app.update_circuits_context_actions()

    async def render_circuits(self, previous_index: int | None = None) -> None:
        self._render_generation += 1
        generation = self._render_generation
        list_view = self.query_one("#circuit-list", ListView)
        if previous_index is None:
            previous_index = self.get_list_index()
        self.reset_list_scroll(list_view)
        list_view.index = None
        await list_view.clear()
        if generation != self._render_generation:
            return
        if not self._display_data:
            await list_view.mount(ListItem(Label("No circuits yet", classes="empty-list-text")))
            if generation != self._render_generation:
                return
            list_view.index = 0
            list_view.focus()
            self.reset_list_scroll(list_view)
            self._sync_current_circuit()
            return
        for idx, circuit in enumerate(self._display_data):
            if generation != self._render_generation:
                return
            status = circuit_status(circuit)
            await list_view.mount(
                ListItem(
                    Vertical(
                        Horizontal(
                            Label(status["icon"], classes=f"status-icon {status['class']}"),
                            Label(circuit.get("name", "Circuit"), classes="list-primary"),
                            Label(circuit_fill_text(circuit), classes="circuit-fill"),
                            classes="list-row",
                        ),
                        Horizontal(
                            Label("", classes="status-icon"),
                            Label(circuit_secondary_text(circuit), classes="circuit-secondary"),
                            Label(circuit_size_text(circuit), classes="list-secondary"),
                            classes="list-row",
                        ),
                        classes="circuit-row",
                    ),
                    classes="circuit-item",
                )
            )
        if generation != self._render_generation:
            return
        target = max(0, min(previous_index, len(self._display_data) - 1))
        list_view.index = target
        list_view.focus()
        self.reset_list_scroll(list_view) if target == 0 else self.reveal_list_index(list_view, target)
        await asyncio.sleep(0)
        self.reset_list_scroll(list_view) if target == 0 else self.reveal_list_index(list_view, target)
        self._sync_current_circuit()

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
            except TypeError:
                try:
                    method()
                except Exception:
                    pass
            except Exception:
                pass

    def reveal_list_index(self, list_view: ListView, index: int) -> None:
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

    def get_list_index(self) -> int:
        return self.query_one("#circuit-list", ListView).index or 0

    def set_list_index(self, idx: int) -> None:
        list_view = self.query_one("#circuit-list", ListView)
        if list_view.children:
            list_view.index = max(0, min(idx, len(list_view.children) - 1))
            self._sync_current_circuit()

    def save_original_ordering(self) -> None:
        self._find_backup = list(self._display_data)

    async def restore_original_ordering(self) -> None:
        self._display_data = list(self._find_backup)
        await self.render_circuits()

    async def execute_jump(self, query: str) -> None:
        if not query:
            return
        q = query.lower()
        for idx, item in enumerate(self._display_data):
            if item.get("name", "").lower().startswith(q):
                self.set_list_index(idx)
                return

    async def execute_find(self, query: str) -> None:
        q = query.lower()
        self._display_data = [
            item for item in self._find_backup or self._display_data
            if q in item.get("name", "").lower() or q in circuit_source_label(item).lower()
        ] if q else list(self._find_backup)
        await self.render_circuits()
        self.set_list_index(0)

    def get_selected_circuit(self) -> dict | None:
        index = self.get_list_index()
        return self._display_data[index] if 0 <= index < len(self._display_data) else None

    def _sync_current_circuit(self) -> None:
        selected = self.get_selected_circuit()
        self.app.current_circuit_id = selected.get("circuit_id") if selected else None

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        self._sync_current_circuit()
        if self.app.current_circuit_id:
            try:
                self.app.query_one("#circuit_track_feedback", CircuitTrackFeedbackScreen)._needs_refresh = True
            except Exception:
                pass
            self.app.open_details_screen("circuit_track_feedback")

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        self._sync_current_circuit()
        self.app.update_circuits_context_actions()

class CircuitTrackFeedbackScreen(Vertical):
    can_focus = False

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._rows: list[dict[str, Any]] = []
        self._needs_refresh = True
        self._feedback_scope = "global"

    def compose(self) -> ComposeResult:
        yield Label("Circuit Tracks", id="circuit-track-feedback-title", classes="section-header")
        yield Label("", id="circuit-track-feedback-summary", classes="circuit-summary")
        with Horizontal(id="circuit-track-feedback-header", classes="circuit-preview-row"):
            yield Label("Global", id="circuit-track-global-header", classes="preview-feedback-cell")
            yield Label("Here", id="circuit-track-here-header", classes="preview-feedback-cell")
            yield Label("Song", classes="preview-song")
        yield ListView(id="circuit-track-feedback-list")

    async def on_show(self) -> None:
        if self._needs_refresh:
            await self.refresh_tracks()
            self._needs_refresh = False

    async def refresh_tracks(self) -> None:
        circuit_id = getattr(self.app, "current_circuit_id", None)
        circuit = self.app.circuit_manager.get_circuit(circuit_id) if circuit_id else None
        self._rows = self.app.circuit_manager.get_circuit_track_feedback(circuit_id) if circuit_id else []
        title = (circuit or {}).get("name") or "Circuit"
        self.query_one("#circuit-track-feedback-title", Label).update(f"Tracks: {title}")
        self.update_summary()
        self.update_header()
        list_view = self.query_one("#circuit-track-feedback-list", ListView)
        previous_index = list_view.index or 0
        await list_view.clear()
        if not self._rows:
            await list_view.mount(ListItem(Label("No downloaded tracks match this circuit source.", classes="empty-list-text")))
            list_view.index = 0
            list_view.focus()
            return
        for row in self._rows:
            await list_view.mount(ListItem(Label(self.format_track_row(row), classes="media-row-text"), id=f"circuit-track-{row.get('song_id')}"))
        list_view.index = max(0, min(previous_index, len(list_view.children) - 1))
        list_view.focus()

    def update_summary(self) -> None:
        scope = "Global" if self._feedback_scope == "global" else "Here"
        self.query_one("#circuit-track-feedback-summary", Label).update(f"{len(self._rows)} tracks | Editing {scope} feedback")

    def update_header(self) -> None:
        self.query_one("#circuit-track-global-header", Label).set_class(self._feedback_scope == "global", "active-feedback-column")
        self.query_one("#circuit-track-here-header", Label).set_class(self._feedback_scope == "here", "active-feedback-column")

    def format_track_row(self, row: dict[str, Any]) -> Text:
        width = self.current_row_width()
        available = max(24, width - 20)
        artist_width = max(12, int(available * 0.30))
        title_width = max(12, available - artist_width - 1)
        if title_width + artist_width + 1 > available:
            title_width = max(8, available - artist_width - 1)
        title = self.app.fit_text(row.get("title") or "Unknown", title_width)
        artist = self.app.fit_text(row.get("artist") or "Unknown Artist", artist_width)
        global_rating = row.get("global_rating") or "neutral"
        here_rating = row.get("here_rating") or "neutral"
        text = Text(no_wrap=True, overflow="crop")
        text.append(f"{feedback_icon(global_rating):^9}", style=self.app.feedback_text_style(global_rating))
        text.append(f"{feedback_icon(here_rating):^9}", style=self.app.feedback_text_style(here_rating))
        text.append(f"  {title:<{title_width}} ")
        text.append(artist, style="dim")
        return text

    def current_row_width(self) -> int:
        try:
            width = self.query_one("#circuit-track-feedback-list", ListView).size.width
            if width:
                return max(24, int(width))
        except Exception:
            pass
        return 92

    def get_list_index(self) -> int:
        return self.query_one("#circuit-track-feedback-list", ListView).index or 0

    def get_selected_item(self) -> dict[str, Any] | None:
        index = self.get_list_index()
        return self._rows[index] if 0 <= index < len(self._rows) else None

    def refresh_row_labels(self) -> None:
        list_view = self.query_one("#circuit-track-feedback-list", ListView)
        for index, child in enumerate(list_view.children):
            if 0 <= index < len(self._rows):
                try:
                    child.query(Label).first().update(self.format_track_row(self._rows[index]))
                except Exception:
                    pass
        self.update_summary()
        self.update_header()

    def toggle_feedback_scope(self) -> None:
        self._feedback_scope = "here" if self._feedback_scope == "global" else "global"
        self.refresh_row_labels()

    async def toggle_selected_feedback(self, rating: str) -> None:
        row = self.get_selected_item()
        song_id = (row or {}).get("song_id")
        circuit_id = getattr(self.app, "current_circuit_id", None)
        if not song_id:
            self.app.show_status("No track selected.")
            return
        current_key = "global_rating" if self._feedback_scope == "global" else "here_rating"
        current_rating = row.get(current_key) or "neutral"
        new_rating = "neutral" if current_rating == rating else rating
        self.app.circuit_manager.set_feedback_rating(
            song_id,
            new_rating,
            scope=self._feedback_scope,
            circuit_id=circuit_id,
            source="circuit_track_list",
        )
        row[current_key] = new_rating
        if self._feedback_scope == "global":
            row["feedback_rating"] = new_rating
        self.refresh_row_labels()
        scope = "Global" if self._feedback_scope == "global" else "Here"
        self.app.show_status(f"{scope} preference set to {feedback_label(new_rating)}.")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        row = self.get_selected_item()
        song_id = (row or {}).get("song_id")
        if not song_id:
            return
        if getattr(self.app, "current_media_id", None) != song_id:
            try:
                self.app.query_one("#media_details")._needs_refresh = True
            except Exception:
                pass
        self.app.current_media_id = song_id
        self.app.current_collection_track_id = song_id
        self.app.open_details_screen("media_details")


class CircuitCirculationPreviewScreen(Vertical):
    can_focus = False

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._rows: list[dict[str, Any]] = []
        self._refreshing = False

    def compose(self) -> ComposeResult:
        yield Label("Circulate", id="circuit-preview-title", classes="section-header")
        yield Label("", id="circuit-preview-summary", classes="circuit-preview-summary")
        with Horizontal(id="circuit-preview-header", classes="circuit-preview-row"):
            yield Label("", classes="preview-marker")
            yield Label("Global", id="circuit-preview-global-header", classes="preview-feedback-cell")
            yield Label("Here", id="circuit-preview-here-header", classes="preview-feedback-cell")
            yield Label("Song", classes="preview-song")
        with ListView(id="circuit-preview-list"):
            pass

    async def on_show(self) -> None:
        pass

    async def refresh_preview(self) -> None:
        plan = getattr(self.app, "current_circuit_plan", None) or {}
        circuit = self.app.circuit_manager.get_circuit(getattr(self.app, "current_circuit_preview_id", None))
        list_view = self.query_one("#circuit-preview-list", ListView)
        self._refreshing = True
        try:
            await list_view.clear()
            self._rows = [{"type": "none", "position": 0}]
            previous_items = plan.get("previous_items") or []
            detected = plan.get("detected_marker") or {}
            selected = plan.get("stop_marker") or {}
            detected_position = int(detected.get("position") or 0)
            selected_position = int(selected.get("position") or 0)

            title = f"Circulate: {(circuit or {}).get('name') or 'Circuit'}"
            self.query_one("#circuit-preview-title", Label).update(title)
            self.update_summary(plan)
            self.update_header(plan)
            await list_view.mount(self.make_no_marker_item(detected_position))
            for item in previous_items:
                position = int(item.get("position") or 0)
                self._rows.append({"type": "item", "position": position, "song_id": item.get("song_id")})
                await list_view.mount(self.make_track_item(item, position, detected_position))
            target_index = next(
                (index for index, row in enumerate(self._rows) if int(row.get("position") or 0) == selected_position),
                0,
            )
            list_view.index = max(0, min(target_index, len(list_view.children) - 1)) if list_view.children else 0
            list_view.focus()
        finally:
            self._refreshing = False

    def update_summary(self, plan: dict[str, Any]) -> None:
        keep_count = len(plan.get("keep_items") or [])
        add_count = int(plan.get("add_count") or 0)
        remove_count = len(plan.get("remove_items") or [])
        target_count = int(plan.get("target_count") or 0)
        current_count = int(plan.get("current_count") or 0)
        missing_count = int(plan.get("missing_count") or 0)
        feedback = plan.get("feedback_changes") or {}
        global_count = sum(1 for scopes in feedback.values() if (scopes or {}).get("global") in ("liked", "disliked"))
        here_count = sum(1 for scopes in feedback.values() if (scopes or {}).get("here") in ("liked", "disliked"))
        scope = "Global" if plan.get("feedback_scope") != "here" else "Here"
        summary = (
            f"Fresh: {keep_count}/{target_count} | Heard: {remove_count} | Add: {add_count} surprises | Editing: {scope}\n"
            f"Love/Boo changes: {global_count} global, {here_count} here | Missing markers: {missing_count}"
        )
        self.query_one("#circuit-preview-summary", Label).update(summary)

    def update_header(self, plan: dict[str, Any]) -> None:
        scope = plan.get("feedback_scope") or "global"
        global_header = self.query_one("#circuit-preview-global-header", Label)
        here_header = self.query_one("#circuit-preview-here-header", Label)
        global_header.set_class(scope == "global", "active-feedback-column")
        here_header.set_class(scope == "here", "active-feedback-column")

    def make_no_marker_item(self, detected_position: int) -> ListItem:
        detected = "▶" if detected_position == 0 else " "
        return ListItem(
            Horizontal(
                Label(detected, classes="preview-marker"),
                Label("", classes="preview-feedback-cell"),
                Label("", classes="preview-feedback-cell"),
                Label("No progress marker", classes="preview-song"),
                classes="circuit-preview-row",
            )
        )

    def make_track_item(self, item: dict[str, Any], position: int, detected_position: int) -> ListItem:
        plan = getattr(self.app, "current_circuit_plan", None) or {}
        detected = "▶" if detected_position == position else " "
        title = item.get("title") or item.get("filename") or "Unknown"
        artist = item.get("artist") or "Unknown Artist"
        ratings = (plan.get("feedback") or {}).get(item.get("song_id"), {})
        scope = plan.get("feedback_scope") or "global"
        global_label = Label(feedback_icon(ratings.get("global")), classes="preview-feedback-cell")
        here_label = Label(feedback_icon(ratings.get("here")), classes="preview-feedback-cell")
        global_label.set_class(scope == "global", "active-feedback-column")
        here_label.set_class(scope == "here", "active-feedback-column")
        return ListItem(
            Horizontal(
                Label(detected, classes="preview-marker"),
                global_label,
                here_label,
                Label(f"{artist} - {title}", classes="preview-song"),
                classes="circuit-preview-row",
            )
        )

    def selected_marker_position(self) -> int:
        list_view = self.query_one("#circuit-preview-list", ListView)
        index = list_view.index or 0
        if not (0 <= index < len(self._rows)):
            return 0
        return int(self._rows[index].get("position") or 0)

    def refresh_row_labels(self) -> None:
        plan = getattr(self.app, "current_circuit_plan", None) or {}
        detected = plan.get("detected_marker") or {}
        selected = plan.get("stop_marker") or {}
        detected_position = int(detected.get("position") or 0)
        selected_position = int(selected.get("position") or 0)
        previous_items = plan.get("previous_items") or []
        list_view = self.query_one("#circuit-preview-list", ListView)
        self.update_header(plan)
        for index, child in enumerate(list_view.children):
            if index == 0:
                labels = list(child.query(Label))
                if labels:
                    labels[0].update("▶" if detected_position == 0 else " ")
                continue
            item_index = index - 1
            if item_index >= len(previous_items):
                continue
            item = previous_items[item_index]
            position = int(item.get("position") or 0)
            ratings = (plan.get("feedback") or {}).get(item.get("song_id"), {})
            labels = list(child.query(Label))
            if len(labels) < 4:
                continue
            labels[0].update("▶" if detected_position == position else " ")
            labels[1].update(feedback_icon(ratings.get("global")))
            labels[2].update(feedback_icon(ratings.get("here")))
            scope = plan.get("feedback_scope") or "global"
            labels[1].set_class(scope == "global", "active-feedback-column")
            labels[2].set_class(scope == "here", "active-feedback-column")

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        await self.app.confirm_circuit_preview_selection(self.selected_marker_position())

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if self._refreshing:
            return
        position = self.selected_marker_position()
        plan = getattr(self.app, "current_circuit_plan", None)
        circuit_id = getattr(self.app, "current_circuit_preview_id", None)
        if plan and circuit_id:
            if plan.get("marker_override") and int(plan.get("marker_position") or 0) == position:
                return
            next_plan = self.app.circuit_manager.plan_circulation(
                circuit_id,
                marker_position=position,
                use_marker_override=True,
                include_add_items=False,
                feedback_changes=plan.get("feedback_changes") or {},
            )
            self.preserve_feedback_state(plan, next_plan)
            self.app.current_circuit_plan = next_plan
            self.update_summary(self.app.current_circuit_plan)
            self.refresh_row_labels()

    def preserve_feedback_state(self, old_plan: dict[str, Any], next_plan: dict[str, Any]) -> None:
        for key in ("feedback", "base_feedback", "feedback_changes", "feedback_scope"):
            if key in old_plan:
                next_plan[key] = old_plan[key]

    def toggle_feedback_scope(self) -> None:
        plan = getattr(self.app, "current_circuit_plan", None)
        if not plan:
            return
        plan["feedback_scope"] = "here" if plan.get("feedback_scope") == "global" else "global"
        self.update_summary(plan)
        self.refresh_row_labels()

    def toggle_selected_feedback(self, rating: str) -> None:
        plan = getattr(self.app, "current_circuit_plan", None)
        if not plan:
            return
        list_view = self.query_one("#circuit-preview-list", ListView)
        index = list_view.index or 0
        if not (0 <= index < len(self._rows)):
            return
        row = self._rows[index]
        song_id = row.get("song_id")
        if not song_id:
            self.app.show_status("Feedback applies to songs, not the marker row.")
            return
        scope = plan.get("feedback_scope") or "global"
        feedback = plan.setdefault("feedback", {})
        base = plan.setdefault("base_feedback", {})
        changes = plan.setdefault("feedback_changes", {})
        feedback.setdefault(song_id, {"global": "neutral", "here": "neutral"})
        base.setdefault(song_id, {"global": "neutral", "here": "neutral"})
        current = feedback[song_id].get(scope) or "neutral"
        new_rating = "neutral" if current == rating else rating
        feedback[song_id][scope] = new_rating
        if new_rating == (base.get(song_id, {}).get(scope) or "neutral"):
            if song_id in changes:
                changes[song_id].pop(scope, None)
                if not changes[song_id]:
                    changes.pop(song_id, None)
        else:
            changes.setdefault(song_id, {})[scope] = new_rating
        self.update_summary(plan)
        self.refresh_row_labels()


class CircuitDetailsScreen(VerticalScroll):
    can_focus = True

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._needs_refresh = True

    def compose(self) -> ComposeResult:
        yield Label("", id="circuit-details-title", classes="details-title")
        yield Rule()
        for key, value_id in (
            ("Status:", "circuit-details-status"),
            ("Source:", "circuit-details-source"),
            ("Path:", "circuit-details-path"),
            ("Size:", "circuit-details-size"),
            ("Target:", "circuit-details-target"),
            ("Last Sent:", "circuit-details-sent"),
            ("Last Scanned:", "circuit-details-scanned"),
            ("Progress:", "circuit-details-progress"),
            ("Manifest:", "circuit-details-manifest"),
        ):
            with Horizontal(classes="dense-row"):
                yield Label(key, classes="dense-key")
                yield Label("", id=value_id, classes="dense-value")

    async def on_show(self) -> None:
        if self._needs_refresh:
            self.refresh_details()
            self._needs_refresh = False

    def refresh_details(self) -> None:
        circuit = self.app.get_current_circuit()
        if not circuit:
            self.query_one("#circuit-details-title", Label).update("No circuit selected")
            for value_id in (
                "#circuit-details-status",
                "#circuit-details-source",
                "#circuit-details-path",
                "#circuit-details-size",
                "#circuit-details-target",
                "#circuit-details-sent",
                "#circuit-details-scanned",
                "#circuit-details-progress",
                "#circuit-details-manifest",
            ):
                self.query_one(value_id, Label).update("")
            self.app.update_current_context_actions()
            return
        status = circuit_status(circuit)
        target_dir = Path(circuit.get("target_path") or "")
        target = circuit_target_count(circuit)
        self.query_one("#circuit-details-title", Label).update(circuit.get("name", "Circuit"))
        self.query_one("#circuit-details-status", Label).update(status["label"])
        self.query_one("#circuit-details-source", Label).update(circuit_source_label(circuit))
        self.query_one("#circuit-details-path", Label).update(str(target_dir))
        self.query_one("#circuit-details-size", Label).update(format_circuit_size(circuit_folder_size(target_dir)))
        self.query_one("#circuit-details-target", Label).update(str(target))
        self.query_one("#circuit-details-sent", Label).update(str(circuit.get("last_sent_at") or "Never"))
        self.query_one("#circuit-details-scanned", Label).update(str(circuit.get("last_scanned_at") or "Never"))
        self.query_one("#circuit-details-progress", Label).update(circuit_progress_label(circuit))
        self.query_one("#circuit-details-manifest", Label).update(status["manifest"])
        self.app.update_current_context_actions()


class CircuitFeedbackDebugScreen(Vertical):
    can_focus = False

    def compose(self) -> ComposeResult:
        yield Label("Circuit Feedback Debug", id="circuit-feedback-debug-title", classes="section-header")
        yield Label("", id="circuit-feedback-debug-summary", classes="circuit-summary")
        yield ListView(id="circuit-feedback-debug-list")

    async def on_show(self) -> None:
        await self.refresh_feedback()

    async def refresh_feedback(self) -> None:
        circuit_id = getattr(self.app, "current_circuit_id", None)
        rows = self.app.circuit_manager.get_feedback_debug_rows(circuit_id=circuit_id)
        summary = "Showing selected circuit feedback events" if circuit_id else "Showing recent feedback events"
        self.query_one("#circuit-feedback-debug-summary", Label).update(summary)
        list_view = self.query_one("#circuit-feedback-debug-list", ListView)
        await list_view.clear()
        if not rows:
            await list_view.mount(ListItem(Label("No feedback captured yet", classes="empty-list-text")))
            list_view.index = 0
            list_view.focus()
            return
        for row in rows:
            await list_view.mount(
                ListItem(
                    Vertical(
                        Horizontal(
                            Label(str(row.get("title") or "Unknown"), classes="list-primary"),
                            Label(str(row.get("event_at") or ""), classes="list-secondary"),
                            classes="list-row",
                        ),
                        Label(self.format_debug_row(row), classes="circuit-secondary"),
                        classes="circuit-row",
                    ),
                    classes="circuit-item",
                )
            )
        list_view.index = 0
        list_view.focus()

    def format_debug_row(self, row: dict[str, Any]) -> str:
        circuit = row.get("circuit_name") or "-"
        event = f"{row.get('event_scope') or '-'} {feedback_label(row.get('event_rating'))} from {row.get('event_source') or '-'}"
        current = f"global {feedback_label(row.get('global_rating'))}, here {feedback_label(row.get('here_rating'))}"
        heard = row.get("last_heard_at") or "not heard"
        return f"{circuit} | {event} | {current} | heard {heard}"
