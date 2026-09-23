from textual.widgets import ContentSwitcher

from core.models import MediaQuery
from tui.flows.review import ReviewSession


class ReviewWorkflowMixin:
    def _item_requires_review(self, item: dict) -> bool:
        status = item.get("song_status") or item.get("status")
        return str(status or "").upper() == "REVIEW"

    async def start_review_session(self) -> None:
        current = self.query_one(ContentSwitcher).current
        origin_screen = None
        if current == "collections":
            origin_screen = self.query_one("#collections")
            collection = self.get_selected_collection()
            if not collection:
                self.show_status("No collection selected.")
                return
            query = MediaQuery(collection_id=collection.get("collection_id"), archived=False)
            visible_items = self.collection_manager.search_media(query)
        elif current in ("collection_details", "media", "media_details"):
            origin_screen = self.query_one(f"#{current}") if current != "media_details" else None
            selected_items = self.get_selected_media_items()
            if not selected_items:
                self.show_status("No media item selected.")
                return
            visible_items = selected_items
        else:
            self.show_status("Review is only available in Collections and Media.")
            return

        review_items = [item for item in visible_items if self._item_requires_review(item)]
        if not review_items:
            self.show_status("No items require review.")
            return

        self.review_cached_actions = self.current_context_actions
        self.review_session = ReviewSession(
            items=review_items,
            origin_screen_id=current,
            origin_index=origin_screen.get_list_index() if origin_screen else 0,
            origin_actions=self.current_context_actions,
        )
        self.load_review_candidates()
        self.update_context_actions("")
        self.query_one(ContentSwitcher).current = "review_session"
        self.show_status("Enter Approve | S Skip | Esc Exit Review", persistent=True)

    async def start_review_all_session(self) -> None:
        review_items = self.collection_manager.search_media(MediaQuery(in_review_queue=True, archived=False))
        review_items = [item for item in review_items if self._item_requires_review(item)]
        if not review_items:
            self.show_status("No items require review.")
            return
        current = self.query_one(ContentSwitcher).current
        self.review_cached_actions = self.current_context_actions
        self.review_session = ReviewSession(
            items=review_items,
            origin_screen_id=current,
            origin_index=0,
            origin_actions=self.current_context_actions,
        )
        self.load_review_candidates()
        self.update_context_actions("")
        self.query_one(ContentSwitcher).current = "review_session"
        self.show_status("Enter Approve | S Skip | Esc Exit Review", persistent=True)

    def load_review_candidates(self) -> None:
        session = self.review_session
        item = session.current_item if session else None
        if not session or not item:
            return

        candidates = self.review.get_match_candidates(item.get("song_id"))
        candidates.append({"kind": "custom_url", "label": "Custom URL"})
        session.candidates = candidates
        session.candidate_index = 0

    async def approve_review_selection(self) -> None:
        session = self.review_session
        if not session:
            return
        if session.complete:
            await self.exit_review_session()
            return

        if not session.candidates:
            self.load_review_candidates()

        selected = session.candidates[max(0, min(session.candidate_index, len(session.candidates) - 1))]
        if selected.get("kind") == "custom_url":
            self.input_mode = "REVIEW_URL"
            self.input_query = ""
            self.show_status("Enter URL: _", persistent=True)
            return

        self.review.approve_match(session.current_item["song_id"], selected["match_id"])
        await self.advance_review_session(approved=True)

    async def action_skip_review(self) -> None:
        if self.filter_rule_input_active():
            self.input_query += "s"
            await self._update_input_state()
            return
        if self.review_session and self.input_mode == "NORMAL":
            await self.advance_review_session(skipped=True)
        elif self.input_mode == "NORMAL":
            current = self.query_one(ContentSwitcher).current
            if current in ("exports", "export_details"):
                await self.start_shuffle_export_flow()
                return
            self.start_input_mode("SORT")

    async def apply_review_custom_url(self, url: str) -> None:
        session = self.review_session
        if not session:
            self.input_mode = "NORMAL"
            return

        self.input_mode = "NORMAL"
        self.input_query = ""
        if not url:
            self.show_status("Enter Approve | S Skip | Esc Exit Review", persistent=True)
            return

        self.review.apply_manual_url(session.current_item["song_id"], url)
        await self.advance_review_session(approved=True)

    async def advance_review_session(self, *, approved: bool = False, skipped: bool = False) -> None:
        session = self.review_session
        if not session:
            return

        session.reviewed_count += 1
        if approved:
            session.approved_count += 1
        if skipped:
            session.skipped_count += 1

        if session.current_index >= session.total - 1:
            session.complete = True
            self.show_status("Review complete. Press Enter to return.", persistent=True)
        else:
            session.current_index += 1
            self.load_review_candidates()
            self.show_status("Enter Approve | S Skip | Esc Exit Review", persistent=True)

        await self.query_one("#review_session").render_session()

    async def exit_review_session(self) -> None:
        session = self.review_session
        if not session:
            return

        origin_screen_id = session.origin_screen_id
        origin_index = session.origin_index
        origin_actions = session.origin_actions
        self.review_session = None
        self.input_mode = "NORMAL"
        self.input_query = ""

        switcher = self.query_one(ContentSwitcher)
        switcher.current = origin_screen_id
        origin_screen = self.query_one(f"#{origin_screen_id}")
        if hasattr(origin_screen, "refresh_details"):
            await origin_screen.refresh_details()
        elif hasattr(origin_screen, "refresh_list"):
            await origin_screen.refresh_list()
        if hasattr(origin_screen, "set_list_index"):
            origin_screen.set_list_index(origin_index)
        self._focus_main_content(origin_screen_id)
        self.update_current_context_actions()
        self.show_status("Status: Ready")
