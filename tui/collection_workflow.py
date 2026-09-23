from textual.widgets import ContentSwitcher

from core.jobs import JobType
from tui.flows.management import ManagementFlow


class CollectionWorkflowMixin:
    async def finish_collection_created(self, collection_id: str, name: str) -> None:
        self.management_flow = None
        self.query_one(ContentSwitcher).current = "collections"
        screen = self.query_one("#collections")
        await screen.refresh_list()
        target_index = screen.index_for_collection_id(collection_id)
        if target_index is not None:
            screen.set_list_index(target_index)
        self._focus_main_content("collections")
        self.update_current_context_actions()
        self.show_status(f"Created collection: {name}")

    async def commit_copy_collection_flow(self, flow: ManagementFlow) -> None:
        name = flow.query.strip()
        if not name:
            self.show_status("Collection name cannot be empty.")
            return
        if not flow.collection_id:
            self.show_status("No collection selected.")
            return
        new_id = self.collection_manager.copy_collection(
            flow.collection_id,
            name,
            revision_id=flow.setting_path,
        )
        await self.finish_collection_created(new_id, name)

    def collection_group_options(self) -> tuple[list[str], list[dict]]:
        groups = [{"group_id": None, "name": "Ungrouped"}, *self.collection_manager.get_collection_groups()]
        rows = [group["name"] for group in groups]
        rows.append("Create New Group: ")
        return rows, groups

    async def start_collection_group_flow(self, collection: dict | None = None, *, after_create: bool = False, origin_actions: str | None = None) -> None:
        collection = collection or self.get_selected_collection()
        if not collection:
            self.show_status("No collection selected.")
            return
        rows, groups = self.collection_group_options()
        current_group_id = collection.get("group_id")
        selected_index = 0
        for idx, group in enumerate(groups):
            if group.get("group_id") == current_group_id:
                selected_index = idx
                break
        await self.start_management_flow(
            ManagementFlow(
                kind="collection_group_select",
                origin_screen_id="collections",
                origin_actions=origin_actions or self.current_context_actions,
                collection_id=collection.get("collection_id"),
                collection_name=collection.get("name"),
                options=rows,
                selected_index=selected_index,
                results=groups,
                payload={"after_create": after_create},
            )
        )

    async def commit_collection_group_flow(self, flow: ManagementFlow) -> None:
        if not flow.collection_id:
            self.show_status("No collection selected.")
            return
        create_index = len(flow.results)
        if flow.selected_index == create_index:
            name = flow.query.strip()
            if not name:
                self.show_status("Group name cannot be empty.")
                return
            group_id = self.collection_manager.create_collection_group(name)
            group_name = name
        else:
            index = max(0, min(flow.selected_index, len(flow.results) - 1))
            group = flow.results[index]
            group_id = group.get("group_id")
            group_name = group.get("name") or "Ungrouped"

        self.collection_manager.set_collection_group(flow.collection_id, group_id)
        message = f"Moved {flow.collection_name or 'Collection'} to {group_name}."
        self.management_flow = None
        self.query_one(ContentSwitcher).current = "collections"
        screen = self.query_one("#collections")
        await screen.refresh_list()
        target_index = screen.index_for_collection_id(flow.collection_id)
        if target_index is not None:
            screen.set_list_index(target_index)
        self._focus_main_content("collections")
        self.update_current_context_actions()
        self.show_status(message)

    async def start_delete_collection_group_flow(self) -> None:
        current = self.query_one(ContentSwitcher).current
        if current != "collections":
            self.show_status("Group deletion is only available from Collections.")
            return
        screen = self.query_one("#collections")
        group = screen.get_selected_group() if hasattr(screen, "get_selected_group") else None
        if not group:
            self.show_status("No group selected.")
            return
        if group.get("group_id") is None:
            self.show_status("Ungrouped cannot be deleted.")
            return
        await self.start_management_flow(
            ManagementFlow(
                kind="collection_group_delete_confirm",
                origin_screen_id="collections",
                origin_actions=self.current_context_actions,
                setting_path=group.get("group_id"),
                collection_name=group.get("name"),
                options=["Yes", "No"],
            )
        )

    async def commit_delete_collection_group_flow(self, flow: ManagementFlow) -> None:
        if flow.selected_index != 0:
            await self.cancel_management_flow()
            return
        group_name = flow.collection_name or "Group"
        self.collection_manager.delete_collection_group(flow.setting_path)
        self.management_flow = None
        self.query_one(ContentSwitcher).current = "collections"
        await self.query_one("#collections").refresh_list()
        self._focus_main_content("collections")
        self.update_current_context_actions()
        self.show_status(f"Deleted group: {group_name}. Collections moved to Ungrouped.")

    async def finish_collection_details_mutation(self, message: str, target_index: int | None = None) -> None:
        flow = self.management_flow
        self.management_flow = None
        self.query_one(ContentSwitcher).current = "collection_details"
        screen = self.query_one("#collection_details")
        await screen.refresh_details()
        if target_index is not None:
            screen.set_list_index(target_index)
        self._focus_main_content("collection_details")
        self.update_current_context_actions()
        self.show_status(message)

    def commit_collection_draft_if_leaving(self, target_id: str) -> None:
        current = self.query_one(ContentSwitcher).current
        if current == "collection_details" and target_id != "collection_details":
            collection_id = getattr(self, "current_collection_id", None)
            if collection_id:
                try:
                    self.collection_manager.commit_collection_draft(collection_id, "manual_edit")
                except Exception:
                    pass

    def get_selected_collection(self) -> dict | None:
        current = self.query_one(ContentSwitcher).current
        if current in ("collection_details", "collection_history", "collection_snapshot"):
            collection_id = getattr(self, "current_collection_id", None)
            return next(
                (item for item in self.collection_manager.get_all_collections(include_archived=True) if item.get("collection_id") == collection_id),
                None,
            )
        if current != "collections":
            return None

        screen = self.query_one("#collections")
        if hasattr(screen, "get_selected_item"):
            return screen.get_selected_item()
        items = getattr(screen, "_display_data", [])
        index = screen.get_list_index()
        return items[index] if 0 <= index < len(items) else None

    def is_manual_collection(self, collection: dict | None = None) -> bool:
        collection = collection or self.get_selected_collection()
        if not collection:
            return False
        if self.is_vibe_collection(collection):
            return False
        source_type = str(collection.get("source_type") or "").lower()
        collection_type = str(collection.get("collection_type") or "").lower()
        return source_type == "manual" or collection_type == "manual"

    async def start_copy_collection_flow(self) -> None:
        collection = self.get_selected_collection()
        if not collection:
            self.show_status("No collection selected.")
            return
        name = collection.get("name") or "Collection"
        await self.start_management_flow(
            ManagementFlow(
                kind="copy_collection_name",
                origin_screen_id=self.query_one(ContentSwitcher).current,
                origin_actions=self.current_context_actions,
                collection_id=collection.get("collection_id"),
                collection_name=name,
                query=f"{name} Copy",
            )
        )

    async def start_restore_collection_copy_flow(self) -> None:
        collection = self.get_selected_collection()
        if not collection:
            self.show_status("No collection selected.")
            return
        revision_id = self.selected_history_revision_id()
        name = collection.get("name") or "Collection"
        default_name = f"{name} Copy" if revision_id is None else f"{name} Snapshot Copy"
        await self.start_management_flow(
            ManagementFlow(
                kind="copy_collection_name",
                origin_screen_id=self.query_one(ContentSwitcher).current,
                origin_actions=self.current_context_actions,
                collection_id=collection.get("collection_id"),
                collection_name=name,
                setting_path=revision_id,
                query=default_name,
            )
        )

    async def start_archive_collection_flow(self) -> None:
        collection = self.get_selected_collection()
        if not collection:
            self.show_status("No collection selected.")
            return
        status = str(collection.get("status") or "ACTIVE").upper()
        if status == "ARCHIVED":
            await self.start_management_flow(
                ManagementFlow(
                    kind="collection_archive_choice",
                    origin_screen_id=self.query_one(ContentSwitcher).current,
                    origin_actions=self.current_context_actions,
                    collection_id=collection.get("collection_id"),
                    collection_name=collection.get("name"),
                    options=["Unarchive Collection", "Cancel"],
                    payload={"action": "unarchive"},
                )
            )
            return

        managed_exports = [
            export for export in self.export_manager.get_managed_exports()
            if export.get("collection_id") == collection.get("collection_id")
        ]
        options = ["Archive Collection"]
        if managed_exports:
            options.append("Archive + Stop Managed Exports")
        options.append("Cancel")
        await self.start_management_flow(
            ManagementFlow(
                kind="collection_archive_choice",
                origin_screen_id=self.query_one(ContentSwitcher).current,
                origin_actions=self.current_context_actions,
                collection_id=collection.get("collection_id"),
                collection_name=collection.get("name"),
                options=options,
                payload={"action": "archive", "managed_exports": managed_exports},
            )
        )

    async def commit_collection_archive_flow(self, flow: ManagementFlow) -> None:
        if not flow.collection_id:
            await self.cancel_management_flow()
            return
        if flow.selected_index >= len(flow.options) - 1:
            await self.cancel_management_flow()
            return

        action = flow.payload.get("action")
        stop_exports = action == "archive" and flow.options[flow.selected_index] == "Archive + Stop Managed Exports"
        if action == "unarchive":
            self.collection_manager.restore_collection(flow.collection_id)
            message = f"Unarchived collection: {flow.collection_name or 'Collection'}."
        else:
            self.collection_manager.archive_collection(flow.collection_id)
            if stop_exports:
                for export in flow.payload.get("managed_exports", []):
                    self.export_manager.delete_managed_export(export.get("export_id"), delete_folder=False)
            message = f"Archived collection: {flow.collection_name or 'Collection'}."

        origin = flow.origin_screen_id
        actions = flow.origin_actions
        self.management_flow = None
        if origin == "collection_details":
            self.query_one(ContentSwitcher).current = "collections"
            origin = "collections"
            actions = self.NAV_MAP["collections"][2]
        else:
            self.query_one(ContentSwitcher).current = origin
        await self.query_one("#collections").refresh_list()
        self._focus_main_content(origin)
        self.update_current_context_actions()
        self.show_status(message)

    def selected_history_revision_id(self) -> str | None:
        current = self.query_one(ContentSwitcher).current
        if current == "collection_snapshot":
            return getattr(self, "current_revision_id", None)
        if current == "collection_history":
            screen = self.query_one("#collection_history")
            rows = getattr(screen, "_rows", [])
            index = screen.get_list_index()
            if 0 <= index < len(rows):
                return rows[index].get("revision_id")
        return None

    def collection_details_actions(self) -> str:
        collection = self.get_selected_collection()
        if self.collection_is_archived(collection):
            return "Esc    Back\nC      Copy\nH      History\nX      Unarchive Collection"
        base = ["Esc    Back", "Left/Right Page", "Bracket Keys  Jump 10 Pages", "C      Copy", "H      History", "T      Filter", "J      Jump", "F      Find", "S      Sort"]
        if self.is_manual_collection():
            base.extend(["A      Add Track", "O      Remove Track"])
        else:
            base.extend(["R      Refresh"])
        item = self.get_selected_media_item()
        if item:
            base.extend(["L      Love", "B      Boo"])
            for line in self.media_item_action_lines(item, include_edit=False):
                if not line.startswith("X      "):
                    base.append(line)
        base.extend(["E      Export", "X      Archive Collection"])
        return "\n".join(base)

    async def start_add_track_flow(self) -> None:
        collection = self.get_selected_collection()
        if not self.is_manual_collection(collection):
            self.show_status("Only manual collections can be edited directly.")
            return
        await self.start_management_flow(
            ManagementFlow(
                kind="add_track_choice",
                origin_screen_id="collection_details",
                origin_actions=self.current_context_actions,
                collection_id=collection.get("collection_id"),
                collection_name=collection.get("name"),
                origin_index=self.query_one("#collection_details").get_list_index(),
                options=["Existing Media Item", "New Media Item", "Spotify/YouTube URL", "Local File"],
            )
        )

    async def start_remove_track_flow(self) -> None:
        current = self.query_one(ContentSwitcher).current
        if current != "collection_details":
            self.show_status("Remove track is only available in Collection Details.")
            return
        collection = self.get_selected_collection()
        if not self.is_manual_collection(collection):
            self.show_status("Only manual collections can be edited directly.")
            return
        item = self.get_selected_media_item()
        if not item:
            self.show_status("No track selected.")
            return
        index = self.query_one("#collection_details").get_list_index()
        await self.start_management_flow(
            ManagementFlow(
                kind="remove_track_confirm",
                origin_screen_id="collection_details",
                origin_actions=self.current_context_actions,
                collection_id=collection.get("collection_id"),
                collection_name=collection.get("name"),
                song_id=item.get("song_id"),
                origin_index=index,
                options=["Yes", "No"],
            )
        )

    def fetch_and_save_collection(self, collection: dict, job_context=None) -> None:
        source_type = collection.get("source_type")
        plugin = self.plugins.get(source_type)
        if not plugin:
            if source_type == "spotify":
                raise RuntimeError(self.spotify_unavailable_reason)
            raise RuntimeError(f"No source plugin available for {source_type or 'unknown source'}.")

        url = self.collection_manager.get_collection_source_url(collection.get("collection_id"), source_type)
        if not url:
            raise RuntimeError("Collection has no external source URL.")

        if job_context:
            job_context.update_current_item(f"Fetching {collection.get('name', 'collection')}")
        fetch_kwargs = {}
        if source_type == "youtube" and self.is_youtube_radio_url(url):
            max_results = self.youtube_radio_refresh_count(collection)
            if max_results:
                fetch_kwargs["max_results"] = max_results
                if job_context:
                    job_context.update_progress_message(f"Keeping radio size at {max_results} videos")
        fetched = plugin.fetch_collection(url, **fetch_kwargs)
        if fetched and not (job_context and job_context.is_cancelled):
            self.collection_manager.save_collection(fetched, job_context=job_context)

    def is_youtube_radio_url(self, url: str | None) -> bool:
        value = (url or "").lower()
        return "list=rd" in value or "start_radio=1" in value

    def youtube_radio_refresh_count(self, collection: dict) -> int:
        count = collection.get("track_count") or collection.get("item_count")
        try:
            count = int(count or 0)
        except (TypeError, ValueError):
            count = 0
        if count > 0:
            return count

        collection_id = collection.get("collection_id") or collection.get("id")
        if not collection_id:
            return 0
        return self.collection_manager.count_active_collection_tracks(collection_id)

    async def create_refresh_job(self) -> None:
        collection = self.get_selected_collection()
        if not collection:
            self.show_status("Refresh is only available for collections.")
            return
        if self.is_vibe_collection(collection):
            await self.create_refresh_vibe_job(collection)
            return
        if self.is_manual_collection(collection):
            self.show_status("Manual collections do not refresh from an external source.")
            return

        self.job_manager.submit(
            job_type=JobType.REFRESH_COLLECTION,
            description=f"Refresh {collection.get('name', 'Collection')}",
            target_func=self.fetch_and_save_collection,
            collection=collection,
        )
        self.show_status(f"Refreshing {collection.get('name', 'Collection')}.")
        await self.refresh_jobs_view()
