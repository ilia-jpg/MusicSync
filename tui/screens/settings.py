import asyncio
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Label, ListItem, ListView, Rule


class SettingsScreen(VerticalScroll):
    can_focus = False

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.view = "main"
        self.category: str | None = None
        self.list_path: str | None = None
        self.render_generation = 0
        self.setting_paths: dict[str, str] = {}
        self.setting_categories: dict[str, str] = {}
        self.list_indices: dict[str, int] = {}
        self.penalty_keywords: dict[str, str] = {}
        self._refresh_lock = asyncio.Lock()

    def compose(self) -> ComposeResult:
        yield Label("Settings", id="settings-title", classes="section-header")
        yield Rule()
        with ListView(id="settings-list"):
            pass

    async def on_show(self) -> None:
        await self.refresh_settings()

    async def refresh_settings(self) -> None:
        async with self._refresh_lock:
            title = self.query_one("#settings-title", Label)
            list_view = self.query_one("#settings-list", ListView)
            previous_index = list_view.index or 0
            await list_view.clear()
            self.render_generation += 1
            generation = self.render_generation
            self.setting_paths = {}
            self.setting_categories = {}
            self.list_indices = {}
            self.penalty_keywords = {}

            if self.view == "main":
                await self._render_main_settings(title, list_view, generation)
            elif self.view == "list" and self.list_path:
                await self._render_list_setting(title, list_view, generation)
            elif self.view == "penalties":
                await self._render_penalties(title, list_view, generation)

            if list_view.children:
                list_view.index = max(0, min(previous_index, len(list_view.children) - 1))
                list_view.focus()
            self.app.update_settings_actions()

    async def _render_main_settings(self, title: Label, list_view: ListView, generation: int) -> None:
        title.update("Settings")
        idx = 0
        for category in self.app.settings.get_categories():
            for label, path, meta in self.app.settings.iter_schema_entries(category):
                item_id = f"settings-value-{generation}-{idx}"
                self.setting_paths[item_id] = path
                self.setting_categories[item_id] = category
                value = self.app.settings.get(path, meta.get("default"))
                display_value = self._format_setting_value(value, meta)
                await list_view.mount(
                    ListItem(
                        Horizontal(
                            Label(f"{category.capitalize()} / {label}", classes="list-primary"),
                            Label(display_value, classes="list-secondary"),
                            classes="list-row",
                        ),
                        id=item_id,
                    )
                )
                idx += 1

        penalty_count = len(self.app.settings.get_penalties())
        penalties_id = f"settings-penalties-{generation}"
        self.setting_categories[penalties_id] = "matching"
        await list_view.mount(
            ListItem(
                Horizontal(
                    Label("Matching / penalties", classes="list-primary"),
                    Label(f"{penalty_count} items", classes="list-secondary"),
                    classes="list-row",
                ),
                id=penalties_id,
            )
        )

    async def _render_list_setting(self, title: Label, list_view: ListView, generation: int) -> None:
        title.update(f"Settings: {self.list_path}")
        values = self.app.settings.get(self.list_path, []) or []
        if values:
            for idx, value in enumerate(values):
                item_id = f"settings-list-item-{generation}-{idx}"
                self.list_indices[item_id] = idx
                await list_view.mount(ListItem(Label(str(value)), id=item_id))
        else:
            await list_view.mount(ListItem(Label("(empty)"), id=f"settings-empty-{generation}"))

    async def _render_penalties(self, title: Label, list_view: ListView, generation: int) -> None:
        title.update("Settings: Matching Penalties")
        penalties = self.app.settings.get_penalties()
        if penalties:
            for idx, (keyword, value) in enumerate(penalties.items()):
                item_id = f"settings-penalty-{generation}-{idx}"
                self.penalty_keywords[item_id] = keyword
                await list_view.mount(ListItem(Label(f"{keyword}: {value}"), id=item_id))
        else:
            await list_view.mount(ListItem(Label("(empty)"), id=f"settings-empty-{generation}"))

    def _format_setting_value(self, value: Any, meta: dict[str, Any]) -> str:
        if meta.get("type") == "list":
            return f"{len(value or [])} items"
        if meta.get("type") == "choice":
            for option in meta.get("options", []):
                option_value = option.get("value") if isinstance(option, dict) else option
                if str(option_value) == str(value):
                    return option.get("label", str(option_value)) if isinstance(option, dict) else str(option_value)
        return str(value)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        item_id = getattr(event.item, "id", None)
        if not isinstance(item_id, str):
            return
        if item_id.startswith("settings-penalties-"):
            self.view = "penalties"
            self.category = "matching"
            asyncio.create_task(self.refresh_settings())
        elif item_id.startswith("settings-value-"):
            path = self.setting_paths.get(item_id)
            if path:
                asyncio.create_task(self.app.start_setting_edit_flow(path))
        elif item_id.startswith("settings-list-item-"):
            asyncio.create_task(self.app.start_setting_list_edit_flow())
        elif item_id.startswith("settings-penalty-"):
            keyword = self.penalty_keywords.get(item_id)
            if keyword:
                asyncio.create_task(self.app.start_penalty_edit_flow(keyword))

    async def go_back(self) -> bool:
        if self.view == "main":
            return False
        self.view = "main"
        self.category = None
        self.list_path = None
        await self.refresh_settings()
        return True

    def selected_item_id(self) -> str | None:
        list_view = self.query_one("#settings-list", ListView)
        item = list_view.highlighted_child
        item_id = getattr(item, "id", None)
        return item_id if isinstance(item_id, str) else None

    def selected_setting_path(self) -> str | None:
        item_id = self.selected_item_id()
        return self.setting_paths.get(item_id or "")

    def selected_setting_category(self) -> str | None:
        item_id = self.selected_item_id()
        if self.view == "penalties":
            return "matching"
        if self.view == "list":
            return self._category_for_path(self.list_path)
        return self.setting_categories.get(item_id or "")

    def selected_penalty_keyword(self) -> str | None:
        item_id = self.selected_item_id()
        return self.penalty_keywords.get(item_id or "")

    def selected_list_index(self) -> int | None:
        item_id = self.selected_item_id()
        return self.list_indices.get(item_id or "")

    def _category_for_path(self, path: str | None) -> str | None:
        if not path:
            return None
        for category in self.app.settings.get_categories():
            for _label, entry_path, _meta in self.app.settings.iter_schema_entries(category):
                if entry_path == path:
                    return category
        return None
