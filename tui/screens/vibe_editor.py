from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual import events
from textual.widgets import Label, ListItem, ListView, Rule


class VibeEditorScreen(Vertical):
    can_focus = False

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._rows: list[dict] = []
        self._render_generation = 0

    def compose(self) -> ComposeResult:
        yield Label("", id="vibe-editor-title", classes="section-header")
        yield Label("", id="vibe-editor-summary", classes="details-meta")
        yield Rule()
        with ListView(id="vibe-editor-list"):
            pass

    async def on_show(self) -> None:
        await self.refresh_editor()

    async def refresh_editor(self) -> None:
        rule = self.app.current_vibe_rule or self.app.empty_vibe_rule()
        name = self.app.current_vibe_name or "Untitled Vibe"
        self.query_one("#vibe-editor-title", Label).update(f"Edit Vibe: {name}")
        counts, warnings = self.app.vibe_counts_and_warnings(rule)
        summary = f"Tracks match Required Rules and ANY rule group. Total: {counts.get('total', 0)} matches."
        if warnings:
            summary += "\n" + "\n".join(warnings[:3])
        self.query_one("#vibe-editor-summary", Label).update(summary)
        list_view = self.query_one("#vibe-editor-list", ListView)
        selected = list_view.index or 0
        self._render_generation += 1
        generation = self._render_generation
        await list_view.clear()
        self._rows = self.build_rows(rule)
        for index, row in enumerate(self._rows):
            if generation != self._render_generation:
                return
            await list_view.mount(ListItem(Label(self.format_row(row)), id=f"vibe-row-{generation}-{index}"))
        if generation != self._render_generation:
            return
        if self._rows:
            list_view.index = max(0, min(selected, len(self._rows) - 1))
            list_view.focus()

    def build_rows(self, rule: dict) -> list[dict]:
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

    def format_row(self, row: dict) -> str:
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

    def selected_row(self) -> dict:
        list_view = self.query_one("#vibe-editor-list", ListView)
        index = list_view.index or 0
        if 0 <= index < len(self._rows):
            return self._rows[index]
        return {"kind": "required_header"}

    def selected_target(self) -> tuple[str, int | None]:
        row = self.selected_row()
        if row.get("section") == "required" or row.get("kind") == "required_header":
            return "required", None
        group_index = row.get("group_index")
        if group_index is None:
            groups = (self.app.current_vibe_rule or {}).get("groups") or []
            group_index = max(0, len(groups) - 1)
        return "group", int(group_index)

    def selected_rule_location(self) -> tuple[str, int | None, int] | None:
        row = self.selected_row()
        if row.get("kind") != "rule":
            return None
        return row.get("section"), row.get("group_index"), int(row.get("rule_index") or 0)

    def set_list_index(self, index: int) -> None:
        list_view = self.query_one("#vibe-editor-list", ListView)
        if list_view.children:
            list_view.index = max(0, min(index, len(list_view.children) - 1))


class VibePreviewScreen(Vertical):
    can_focus = False

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._render_generation = 0
        self._display_data: list[dict] = []
        self._offset = 0
        self._page_size = 20
        self._total_count = 0

    def compose(self) -> ComposeResult:
        yield Label("Vibe Preview", id="vibe-preview-title", classes="section-header")
        yield Label("", id="vibe-preview-summary", classes="details-meta")
        yield Rule()
        with ListView(id="vibe-preview-list"):
            pass

    async def on_show(self) -> None:
        await self.refresh_preview()

    async def on_resize(self, event: events.Resize) -> None:
        try:
            current = getattr(self.app.query_one("#content-switcher"), "current", None)
        except Exception:
            return
        if current == "vibe_preview":
            await self.refresh_preview()

    async def refresh_preview(self) -> None:
        items = list(getattr(self.app, "current_vibe_preview_items", []) or [])
        list_view = self.query_one("#vibe-preview-list", ListView)
        self._total_count = len(items)
        self._page_size = self.visible_page_size(list_view)
        self._offset = max(0, min(self._offset, self.last_page_offset()))
        self._display_data = items[self._offset:self._offset + self._page_size]

        summary = f"{self._total_count} matching tracks"
        if self._total_count > self._page_size:
            start = self._offset + 1 if self._display_data else 0
            end = self._offset + len(self._display_data)
            summary = f"{summary}, page {self.current_page_number()}/{self.total_page_count()} ({start}-{end})"
        self.query_one("#vibe-preview-summary", Label).update(summary)

        selected = list_view.index or 0
        self._render_generation += 1
        generation = self._render_generation
        self.reset_list_scroll(list_view)
        await list_view.clear()
        for index, item in enumerate(self._display_data):
            if generation != self._render_generation:
                return
            await list_view.mount(
                ListItem(
                    Label(self.app.format_media_list_row(item), classes="media-row-text"),
                    id=f"vibe-preview-{item.get('song_id') or index}",
                )
            )
        if generation != self._render_generation:
            return
        if list_view.children:
            list_view.index = max(0, min(selected, len(list_view.children) - 1))
            list_view.focus()
            if list_view.index == 0:
                self.reset_list_scroll(list_view)

    def visible_page_size(self, list_view: ListView) -> int:
        for attr_name in ("size", "region", "content_size"):
            value = getattr(list_view, attr_name, None)
            height = getattr(value, "height", None)
            if height:
                try:
                    return max(1, int(height))
                except (TypeError, ValueError):
                    pass
        return max(1, len(getattr(list_view, "children", [])) or 20)

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
        await self.refresh_preview()
        return True

    async def next_page(self) -> bool:
        return await self.go_to_offset(self._offset + self._page_size)

    async def previous_page(self) -> bool:
        return await self.go_to_offset(self._offset - self._page_size)

    def get_list_index(self) -> int:
        return self.query_one("#vibe-preview-list", ListView).index or 0

    def set_list_index(self, index: int) -> None:
        list_view = self.query_one("#vibe-preview-list", ListView)
        if list_view.children:
            list_view.index = max(0, min(index, len(list_view.children) - 1))

    def get_selected_item(self) -> dict | None:
        index = self.get_list_index()
        return self._display_data[index] if 0 <= index < len(self._display_data) else None

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        item = self.get_selected_item()
        if not item:
            return
        song_id = item.get("song_id")
        if song_id:
            self.app.current_media_id = song_id
            self.app.current_collection_track_id = song_id
            try:
                self.app.query_one("#media_details")._needs_refresh = True
            except Exception:
                pass
        self.app.open_details_screen("media_details")
