import asyncio

from textual.widgets import ContentSwitcher

from core.acquisition import AcquisitionManager
from core.audio_analysis import AudioAnalysisManager
from core.exports import ExportManager
from core.library import LibraryManager
from core.matching import MatchingEngine
from tui.flows.management import ManagementFlow
from tui.screens.settings import SettingsScreen


class SettingsWorkflowMixin:
    async def commit_setting_value_flow(self, flow: ManagementFlow) -> None:
        path = flow.setting_path
        if not path:
            await self.cancel_management_flow()
            return
        if flow.setting_type == "choice":
            options = flow.payload.get("options", [])
            if not options:
                self.show_status("No options available.")
                return
            index = max(0, min(flow.selected_index, len(options) - 1))
            selected = options[index]
            value = selected.get("value") if isinstance(selected, dict) else selected
        else:
            value = flow.query.strip()
        self.settings.set(path, value)
        self.save_settings_changes()
        await self.finish_settings_mutation("Setting updated.")

    async def commit_setting_list_value_flow(self, flow: ManagementFlow) -> None:
        path = flow.setting_path
        if not path:
            await self.cancel_management_flow()
            return
        value = flow.query.strip()
        if not value:
            self.show_status("Value cannot be empty.")
            return
        values = list(self.settings.get(path, []) or [])
        if flow.setting_type == "edit":
            index = flow.origin_index
            if 0 <= index < len(values):
                values[index] = value
        else:
            values.append(value)
        self.settings.set(path, values)
        self.save_settings_changes()
        await self.finish_settings_mutation("Setting updated.")

    async def commit_penalty_form(self, flow: ManagementFlow) -> None:
        values = {label: value.strip() for label, value in flow.fields}
        keyword = values.get("Keyword", "")
        penalty = values.get("Value", "")
        if not keyword:
            self.show_status("Keyword cannot be empty.")
            return
        if not penalty:
            self.show_status("Penalty value cannot be empty.")
            return
        if flow.setting_path and flow.setting_path != keyword:
            self.settings.remove_penalty(flow.setting_path)
        self.settings.add_penalty(keyword, penalty)
        self.save_settings_changes()
        await self.finish_settings_mutation("Penalty saved.")

    async def finish_settings_mutation(self, message: str) -> None:
        self.management_flow = None
        self.query_one(ContentSwitcher).current = "settings"
        await self.query_one("#settings", SettingsScreen).refresh_settings()
        self.update_settings_actions()
        self._focus_main_content("settings")
        self.show_status(message)

    def save_settings_changes(self) -> None:
        self.settings.save()
        self.config.config = self.settings._config
        self.rebuild_plugin_registries()
        self.matching_engine = MatchingEngine(self.config, self.db, self.resolver_plugins)
        self.export_manager = ExportManager(self.config, self.db)
        self.library_manager = LibraryManager(self.config, self.db)
        self.acquisition_manager = AcquisitionManager(self.config, self.db, self.library_manager)
        self.audio_analysis_manager = AudioAnalysisManager(self.db, self.config)

    def update_settings_actions(self) -> None:
        if self.query_one(ContentSwitcher).current != "settings":
            return
        screen = self.query_one("#settings", SettingsScreen)
        item_id = screen.selected_item_id()
        if screen.view == "main":
            if item_id and item_id.startswith("settings-penalties-"):
                self.update_context_actions("Enter  Open\nR      Reset Category")
            elif item_id and item_id.startswith("settings-value-"):
                self.update_context_actions("Enter  Edit/Open\nE      Edit\nR      Reset Category")
            else:
                self.update_context_actions("Enter  Edit/Open")
        elif screen.view == "list":
            lines = ["Esc    Back", "A      Add"]
            if item_id and item_id.startswith("settings-list-item-"):
                lines.extend(["Enter  Edit", "E      Edit", "D      Remove"])
            self.update_context_actions("\n".join(lines))
        elif screen.view == "penalties":
            lines = ["Esc    Back", "A      Add Penalty"]
            if item_id and item_id.startswith("settings-penalty-"):
                lines.extend(["Enter  Edit", "E      Edit", "D      Remove"])
            self.update_context_actions("\n".join(lines))

    async def start_setting_edit_flow(self, path: str) -> None:
        node = self.settings.get_schema_node(path) or {}
        setting_type = node.get("type")
        if setting_type == "list":
            screen = self.query_one("#settings", SettingsScreen)
            screen.view = "list"
            screen.list_path = path
            await screen.refresh_settings()
            return

        value = self.settings.get(path, node.get("default"))
        if setting_type == "choice":
            options = node.get("options", [])
            selected_index = 0
            for idx, option in enumerate(options):
                option_value = option.get("value") if isinstance(option, dict) else option
                if str(option_value) == str(value):
                    selected_index = idx
                    break
            await self.start_management_flow(
                ManagementFlow(
                    kind="setting_value",
                    origin_screen_id="settings",
                    origin_actions=self.current_context_actions,
                    setting_path=path,
                    setting_type=setting_type,
                    selected_index=selected_index,
                    payload={"options": options},
                )
            )
            return
        await self.start_management_flow(
            ManagementFlow(
                kind="setting_value",
                origin_screen_id="settings",
                origin_actions=self.current_context_actions,
                query="" if value is None else str(value),
                setting_path=path,
                setting_type=setting_type,
            )
        )

    def start_selected_setting_edit_flow(self) -> None:
        screen = self.query_one("#settings", SettingsScreen)
        item_id = screen.selected_item_id()
        if not item_id:
            self.show_status("No setting selected.")
            return
        if item_id.startswith("settings-value-"):
            path = screen.selected_setting_path()
            if path:
                asyncio.create_task(self.start_setting_edit_flow(path))
        elif item_id.startswith("settings-list-item-"):
            asyncio.create_task(self.start_setting_list_edit_flow())
        elif item_id.startswith("settings-penalty-"):
            asyncio.create_task(self.start_penalty_edit_flow(screen.selected_penalty_keyword()))

    async def start_setting_list_edit_flow(self) -> None:
        screen = self.query_one("#settings", SettingsScreen)
        path = screen.list_path
        item_id = screen.selected_item_id()
        index = screen.selected_list_index()
        if not path or not item_id or not item_id.startswith("settings-list-item-") or index is None:
            return
        values = list(self.settings.get(path, []) or [])
        if not (0 <= index < len(values)):
            return
        await self.start_management_flow(
            ManagementFlow(
                kind="setting_list_value",
                origin_screen_id="settings",
                origin_actions=self.current_context_actions,
                query=str(values[index]),
                origin_index=index,
                setting_path=path,
                setting_type="edit",
            )
        )

    async def start_settings_add_flow(self) -> None:
        screen = self.query_one("#settings", SettingsScreen)
        if screen.view == "list" and screen.list_path:
            await self.start_management_flow(
                ManagementFlow(
                    kind="setting_list_value",
                    origin_screen_id="settings",
                    origin_actions=self.current_context_actions,
                    setting_path=screen.list_path,
                    setting_type="add",
                )
            )
        elif screen.view == "penalties":
            await self.start_penalty_edit_flow(None)
        else:
            self.show_status("Add is only available for list settings.")

    async def start_penalty_edit_flow(self, keyword: str | None) -> None:
        penalties = self.settings.get_penalties()
        await self.start_management_flow(
            ManagementFlow(
                kind="penalty_form",
                origin_screen_id="settings",
                origin_actions=self.current_context_actions,
                setting_path=keyword,
                fields=[
                    ("Keyword", keyword or ""),
                    ("Value", "" if keyword is None else str(penalties.get(keyword, ""))),
                ],
            )
        )

    async def remove_selected_setting_item(self) -> None:
        screen = self.query_one("#settings", SettingsScreen)
        item_id = screen.selected_item_id()
        try:
            if screen.view == "list" and screen.list_path and item_id and item_id.startswith("settings-list-item-"):
                index = screen.selected_list_index()
                values = list(self.settings.get(screen.list_path, []) or [])
                if index is not None and 0 <= index < len(values):
                    values.pop(index)
                    self.settings.set(screen.list_path, values)
                    self.save_settings_changes()
                    await screen.refresh_settings()
                    self.show_status("Setting removed.")
            elif screen.view == "penalties" and item_id and item_id.startswith("settings-penalty-"):
                keyword = screen.selected_penalty_keyword()
                if keyword:
                    self.settings.remove_penalty(keyword)
                    self.save_settings_changes()
                    await screen.refresh_settings()
                    self.show_status("Penalty removed.")
            else:
                self.show_status("Remove is not available here.")
        except Exception as exc:
            self.show_status(f"Failed to remove setting: {exc}")

    async def reset_selected_settings_category(self) -> None:
        screen = self.query_one("#settings", SettingsScreen)
        category = screen.selected_setting_category()
        if not category:
            self.show_status("No settings category selected.")
            return
        try:
            self.settings.reset(category)
            self.save_settings_changes()
            await screen.refresh_settings()
            self.show_status(f"{category.capitalize()} reset to defaults.")
        except Exception as exc:
            self.show_status(f"Failed to reset settings: {exc}")
