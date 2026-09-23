from typing import Any

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Label, ListItem, ListView, Rule


class ManagementFlowScreen(VerticalScroll):
    can_focus = False

    def __init__(self, *args: Any, **kwargs: Any):
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

        if flow.kind == "youtube_radio_track_search":
            flow.page_size = self.visible_row_count(list_view)
            flow.page_offset = max(0, min(flow.page_offset, self.last_page_offset(flow)))
            flow.results = flow.source_results[flow.page_offset:flow.page_offset + flow.page_size]

        title, body, rows = self.app.describe_management_flow(flow)
        if generation != self._render_generation:
            return
        self.query_one("#management-title", Label).update(title)
        self.query_one("#management-body", Label).update(body)
        for idx, row in enumerate(rows):
            if generation != self._render_generation:
                return
            await list_view.mount(
                ListItem(
                    Label(self.app.format_management_row(flow, idx, row)),
                    id=f"management-row-{generation}-{idx}",
                )
            )
        if rows:
            target_index = flow.field_index if flow.kind in ("new_media_form", "penalty_form") else flow.selected_index
            list_view.index = max(0, min(target_index, len(rows) - 1))
            list_view.focus()

    def visible_row_count(self, list_view: ListView, row_height: int = 1) -> int:
        for attr_name in ("size", "region", "content_size"):
            value = getattr(list_view, attr_name, None)
            height = getattr(value, "height", None)
            if height:
                try:
                    return max(1, int(height) // max(1, row_height))
                except (TypeError, ValueError):
                    pass
        return max(1, len(getattr(list_view, "children", [])) or 20)

    def last_page_offset(self, flow: Any) -> int:
        total = len(getattr(flow, "source_results", []))
        page_size = max(1, int(getattr(flow, "page_size", 20) or 20))
        if total <= 0:
            return 0
        return max(0, ((total - 1) // page_size) * page_size)

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
            index = list_view.index or 0
            rows = self.app.describe_management_flow(flow)[2]
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
