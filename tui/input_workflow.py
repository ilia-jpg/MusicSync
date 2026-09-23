from textual.widgets import ContentSwitcher

MEDIA_SORT_HELP = (
    "Sort options:\n"
    "track\n"
    "artist\n"
    "duration\n"
    "status\n"
    "downloaded\n"
    "preference\n"
    "tempo\n"
    "stability\n"
    "intensity\n"
    "dynamics\n"
    "activity\n"
    "tone\n"
    "texture\n"
    "vocals\n"
    "key"
)
COLLECTION_SORT_HELP = (
    "Sort options:\n"
    "name\n"
    "source\n"
    "refreshed\n"
    "count\n"
    "tracks"
)


class InputWorkflowMixin:
    def start_input_mode(self, mode: str) -> None:
        if self.input_mode != "NORMAL":
            return

        current_screen_id = self.query_one(ContentSwitcher).current
        if current_screen_id not in ("collections", "collection_details", "media"):
            self.show_status("Jump/Find/Sort only available in Media and Collections lists.")
            return

        self.input_mode = mode
        self.input_query = ""
        self.input_screen = self.query_one(f"#{current_screen_id}")
        self.input_cached_actions = self.current_context_actions
        self.update_context_actions("")

        self.input_original_index = self.input_screen.get_list_index()
        if mode in ("FIND", "SORT"):
            self.input_screen.save_original_ordering()

        if mode == "SORT":
            sort_help = COLLECTION_SORT_HELP if current_screen_id == "collections" else MEDIA_SORT_HELP
            self.update_context_actions(sort_help)
        self.show_status(f"{mode.capitalize()}: _", persistent=True)

    async def _update_input_state(self) -> None:
        if self.input_mode == "JUMP":
            await self.input_screen.execute_jump(self.input_query)
            self.show_status(f"Jump: {self.input_query}_", persistent=True)
        elif self.input_mode == "FIND":
            self.show_status(f"Find: {self.input_query}_", persistent=True)
        elif self.input_mode == "SORT":
            self.show_status(f"Sort: {self.input_query}_", persistent=True)
        elif self.input_mode == "REVIEW_URL":
            self.show_status(f"Enter URL: {self.input_query}_", persistent=True)
        elif self.input_mode in ("VIBE_RULE_FEATURE", "VIBE_RULE_LEVEL"):
            await self.update_vibe_input_status()
        elif self.input_mode in ("FILTER_RULE_FEATURE", "FILTER_RULE_LEVEL"):
            await self.query_one("#filter").update_rule_input_status()

    async def action_commit_input(self) -> None:
        if self.management_flow:
            if self.management_flow.kind in ("circuit_source_select", "circuit_edit_source_select"):
                await self.handle_circuit_source_enter(self.management_flow)
                return
            await self.commit_management_flow()
            return

        if self.review_session and self.input_mode == "NORMAL":
            await self.approve_review_selection()
            return

        if self.input_mode == "NORMAL" and self.query_one(ContentSwitcher).current == "vibe_editor":
            await self.save_vibe_collection()
            return

        if self.input_mode == "REVIEW_URL":
            await self.apply_review_custom_url(self.input_query.strip())
            return

        if self.input_mode in ("VIBE_RULE_FEATURE", "VIBE_RULE_LEVEL", "VIBE_RULE_TARGET"):
            await self.commit_vibe_input()
            return

        if self.input_mode in ("FILTER_RULE_FEATURE", "FILTER_RULE_LEVEL"):
            await self.query_one("#filter").commit_rule_input()
            return

        committed_ordering = False
        if self.input_mode == "FIND" and self.input_query:
            await self.input_screen.execute_find(self.input_query)
            committed_ordering = True
        elif self.input_mode == "SORT" and self.input_query:
            if not await self.input_screen.execute_sort(self.input_query):
                self.show_status(f"Unknown sort: {self.input_query}", persistent=True)
                return
            committed_ordering = True

        if committed_ordering:
            if self.order_revert_screen is not self.input_screen:
                self.order_revert_screen = self.input_screen
                self.order_revert_index = self.input_original_index
        self.input_mode = "NORMAL"
        self.update_current_context_actions()
        self.show_status("Status: Ready")

    async def action_commit_input_shift(self) -> None:
        if self.management_flow:
            await self.commit_management_flow()
            return

        await self.action_commit_input()

    async def action_abort_input(self) -> None:
        if self.input_mode == "JUMP":
            self.input_screen.set_list_index(self.input_original_index)
        elif self.input_mode in ("FIND", "SORT"):
            await self.input_screen.restore_original_ordering()
            self.input_screen.set_list_index(self.input_original_index)
        elif self.input_mode == "REVIEW_URL":
            self.input_mode = "NORMAL"
            self.input_query = ""
            self.show_status("Enter Approve | S Skip | Esc Exit Review", persistent=True)
            return
        elif self.input_mode in ("VIBE_RULE_FEATURE", "VIBE_RULE_LEVEL", "VIBE_RULE_TARGET"):
            await self.abort_vibe_input()
            return
        elif self.input_mode in ("FILTER_RULE_FEATURE", "FILTER_RULE_LEVEL"):
            await self.query_one("#filter").abort_rule_input()
            return
        elif self.input_mode == "ARCHIVE_CONFIRM":
            self.input_mode = "NORMAL"
            self.input_query = ""
            self.update_current_context_actions()
            self.show_status("Archive cancelled.")
            return

        self.input_mode = "NORMAL"
        self.update_current_context_actions()
        self.show_status("Status: Ready")

    async def revert_last_ordering(self) -> bool:
        screen = self.order_revert_screen
        if not screen:
            return False
        try:
            current_screen = self.query_one(ContentSwitcher).current
            if screen is not self.query_one(f"#{current_screen}"):
                self.order_revert_screen = None
                return False
            await screen.restore_original_ordering()
            screen.set_list_index(self.order_revert_index)
            self.order_revert_screen = None
            self.show_status("Order restored.")
            return True
        except Exception:
            self.order_revert_screen = None
            return False

    async def action_escape_key(self) -> None:
        if self.management_flow:
            await self.cancel_management_flow()
        elif self.input_mode != "NORMAL":
            await self.action_abort_input()
        elif self.review_session:
            await self.exit_review_session()
        elif self.query_one(ContentSwitcher).current == "circuit_preview":
            self.cancel_circuit_preview()
        elif self.query_one(ContentSwitcher).current == "vibe_editor":
            self.current_vibe_rule = None
            self.current_vibe_name = None
            self.action_go_back()
            self.show_status("Cancelled.")
        elif await self.revert_last_ordering():
            return
        elif self.selected_media_ids and self.current_media_list_context()[0]:
            self.clear_media_selection()
            self.show_status("Selection cleared.")
        elif self.query_one(ContentSwitcher).current == "settings" and await self.query_one("#settings").go_back():
            return
        else:
            self.action_go_back()

    def action_action_j(self) -> None:
        if self.filter_rule_input_active():
            import asyncio
            asyncio.create_task(self.append_filter_rule_input_character("j"))
            return
        self.start_input_mode("JUMP")

    def action_action_f(self) -> None:
        if self.filter_rule_input_active():
            import asyncio
            asyncio.create_task(self.append_filter_rule_input_character("f"))
            return
        self.start_input_mode("FIND")
