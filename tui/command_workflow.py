import asyncio

from textual.widgets import ContentSwitcher

from core.jobs import JobType
from tui.flows.management import ManagementFlow


class CommandWorkflowMixin:
    async def action_command_palette(self) -> None:
        if self.input_mode != "NORMAL" or self.review_session:
            return
        current = self.query_one(ContentSwitcher).current
        commands = self.available_palette_commands(current)
        await self.start_management_flow(
            ManagementFlow(
                kind="command_palette",
                origin_screen_id=current,
                origin_actions=self.current_context_actions,
                options=[label for _command_id, label in commands],
                results=[{"command_id": command_id, "label": label} for command_id, label in commands],
            )
        )

    def available_palette_commands(self, current: str) -> list[tuple[str, str]]:
        commands = [
            ("show_review_media", "Show Review Items"),
            ("show_ready_downloads", "Show Ready To Download"),
            ("show_archived_media", "Show Archived Media"),
            ("show_audio_summary", "Show Audio Analysis Summary"),
            ("show_audio_matrix", "Show Experimental Audio Matrix"),
            ("show_analyzed_tracks", "Show Analyzed Tracks"),
            ("show_missing_audio_analysis", "Show Missing Audio Analysis"),
            ("show_failed_audio_analysis", "Show Failed Audio Analysis"),
            ("show_stale_audio_analysis", "Show Stale Audio Analysis"),
            ("analyze_downloaded", "Analyze Downloaded Tracks"),
            ("add_circuit", "Add Circuit"),
            ("show_circuit_feedback_debug", "Show Circuit Feedback Debug"),
            ("add_collection", "Add Collection"),
            ("add_media", "Add Media"),
            ("add_export", "Add Export"),
            ("recover_exports", "Recover Exports"),
            ("show_archived_collections", "Show Archived Collections"),
        ]
        if current in ("collections", "collection_details"):
            collection = self.get_selected_collection()
            archive_label = "Unarchive Selected Collection" if str((collection or {}).get("status") or "").upper() == "ARCHIVED" else "Archive Selected Collection"
            commands.insert(0, ("archive_selected_collection", archive_label))
            commands.insert(1, ("export_current_collection", "Export Selected Collection"))
            commands.insert(2, ("analyze_selected_collection", "Analyze Selected Collection"))
        if current == "collections" and getattr(self, "show_archived_collections", False):
            commands.insert(0, ("show_active_collections", "Show Active Collections"))
        if current == "collection_details":
            commands.insert(1, ("add_track", "Add Track To Collection"))
        if current in ("media", "collection_details", "media_details"):
            item = self.get_selected_media_item()
            if item:
                selected_items = self.get_selected_media_items()
                selected_downloaded_count = len([selected for selected in selected_items if self.media_has_file(selected)])
                if selected_downloaded_count:
                    label = "Analyze Selected Tracks" if selected_downloaded_count != 1 else "Analyze Selected Track"
                    commands.insert(0, ("analyze_selected", label))
                if self.media_is_match_failed(item):
                    commands.insert(0, ("download_selected", "Force Download Selected"))
                    commands.insert(0, ("match_selected", "Force Rematch for Review"))
                else:
                    if self.media_is_matched(item):
                        commands.insert(0, ("download_selected", "Download Selected"))
                    commands.insert(0, ("match_selected", "Rematch Selected" if self.media_is_matched(item) else "Match Selected"))
                if str(item.get("status") or item.get("song_status") or "").upper() == "REVIEW":
                    commands.insert(1, ("review_selected", "Review Selected"))
        if current in ("exports", "export_details"):
            commands.insert(0, ("update_export", "Update Selected Export"))
            commands.insert(1, ("shuffle_export", "Shuffle Selected Export"))
            commands.insert(2, ("clear_export", "Clear Selected Export"))
            commands.insert(3, ("delete_export", "Delete Selected Export"))
        if current in ("circuits", "circuit_details"):
            commands.insert(0, ("circulate_circuit", "Circulate Selected Circuit"))
            commands.insert(1, ("edit_circuit", "Edit Selected Circuit"))
            commands.insert(2, ("delete_circuit", "Delete Selected Circuit"))
        return commands

    async def execute_palette_command(self, flow: ManagementFlow) -> None:
        if not flow.results:
            await self.cancel_management_flow()
            return
        selected = flow.results[max(0, min(flow.selected_index, len(flow.results) - 1))]
        command_id = selected.get("command_id")
        origin = flow.origin_screen_id
        actions = flow.origin_actions
        self.management_flow = None
        self.query_one(ContentSwitcher).current = origin
        self._focus_main_content(origin)
        self.update_current_context_actions()

        if command_id == "show_review_media":
            self.action_navigate("media")
            self.media_filter_ui_state = self.default_filter_ui_state()
            self.media_filter_ui_state.update({
                "status_discovered": False,
                "status_matched": False,
                "status_failed": False,
                "status_downloaded": False,
                "status_review": True,
                "flag_archived": False,
            })
            self.media_filter = self.query_from_filter_state(self.media_filter_ui_state, "media")
            asyncio.create_task(self.query_one("#media").refresh_list())
        elif command_id == "show_ready_downloads":
            self.action_navigate("media")
            self.media_filter_ui_state = self.default_filter_ui_state()
            self.media_filter_ui_state.update({
                "status_discovered": False,
                "status_matched": True,
                "status_failed": False,
                "status_downloaded": False,
                "status_review": False,
                "flag_archived": False,
            })
            self.media_filter = self.query_from_filter_state(self.media_filter_ui_state, "media")
            asyncio.create_task(self.query_one("#media").refresh_list())
        elif command_id == "show_archived_media":
            self.action_navigate("media")
            self.media_filter_ui_state = self.default_filter_ui_state()
            self.media_filter_ui_state.update({
                "status_discovered": False,
                "status_matched": False,
                "status_failed": False,
                "status_downloaded": False,
                "status_review": False,
                "flag_archived": True,
            })
            self.media_filter = self.query_from_filter_state(self.media_filter_ui_state, "media")
            asyncio.create_task(self.query_one("#media").refresh_list())
        elif command_id == "show_audio_summary":
            self.open_details_screen("audio_analysis_summary")
        elif command_id == "show_audio_matrix":
            self.open_details_screen("audio_matrix")
        elif command_id == "show_analyzed_tracks":
            self.show_audio_analysis_filter("audio_analyzed")
        elif command_id == "show_missing_audio_analysis":
            self.show_audio_analysis_filter("audio_missing")
        elif command_id == "show_failed_audio_analysis":
            self.show_audio_analysis_filter("audio_failed")
        elif command_id == "show_stale_audio_analysis":
            self.show_audio_analysis_filter("audio_stale")
        elif command_id == "show_archived_collections":
            self.show_archived_collections = True
            self.action_navigate("collections")
            asyncio.create_task(self.query_one("#collections").refresh_list())
        elif command_id == "show_active_collections":
            self.show_archived_collections = False
            self.action_navigate("collections")
            asyncio.create_task(self.query_one("#collections").refresh_list())
        elif command_id == "add_collection":
            self.action_navigate("collections")
            await self.action_action_a()
        elif command_id == "add_media":
            self.action_navigate("media")
            await self.action_action_a()
        elif command_id == "add_export":
            self.action_navigate("exports")
            await self.start_export_flow()
        elif command_id == "add_circuit":
            self.action_navigate("circuits")
            await self.start_circuit_flow()
        elif command_id == "show_circuit_feedback_debug":
            self.open_details_screen("circuit_feedback_debug")
            try:
                await self.query_one("#circuit_feedback_debug").refresh_feedback()
            except Exception:
                pass
        elif command_id == "recover_exports":
            await self.create_recover_exports_job()
        elif command_id == "export_current_collection":
            await self.start_export_flow(self.get_selected_collection())
        elif command_id == "analyze_selected_collection":
            await self.create_analyze_collection_job()
        elif command_id == "archive_selected_collection":
            await self.start_archive_collection_flow()
        elif command_id == "add_track":
            await self.start_add_track_flow()
        elif command_id == "download_selected":
            await self.create_download_job()
        elif command_id == "analyze_selected":
            await self.create_analyze_track_job()
        elif command_id == "analyze_downloaded":
            await self.create_analyze_track_job(force_all_downloaded=True)
        elif command_id == "match_selected":
            await self.create_match_job()
        elif command_id == "review_selected":
            await self.start_review_session()
        elif command_id == "update_export":
            await self.create_update_export_job()
        elif command_id == "shuffle_export":
            await self.start_shuffle_export_flow()
        elif command_id == "clear_export":
            await self.start_clear_export_flow()
        elif command_id == "delete_export":
            await self.start_export_delete_flow()
        elif command_id == "circulate_circuit":
            await self.start_circulate_flow()
        elif command_id == "edit_circuit":
            await self.start_edit_circuit_flow()
        elif command_id == "delete_circuit":
            await self.start_delete_circuit_flow()
        else:
            self.show_status("Unknown command.")

    def run_home_attention_action(self, action: str, payload: dict | None = None) -> None:
        payload = payload or {}
        if action == "download_ready":
            self.job_manager.submit(
                job_type=JobType.DOWNLOAD_COLLECTION,
                description="Download Ready Tracks",
                target_func=self.download_ready_tracks,
            )
            self.show_status("Downloading ready tracks.")
            asyncio.create_task(self.refresh_jobs_view())
            asyncio.create_task(self.query_one("#home").refresh_summary())
        elif action == "failed_matches":
            self.action_navigate("media")
            self.media_filter_ui_state = self.default_filter_ui_state()
            self.media_filter_ui_state.update({
                "status_discovered": False,
                "status_matched": False,
                "status_failed": True,
                "status_downloaded": False,
                "status_review": False,
                "flag_archived": False,
            })
            self.media_filter = self.query_from_filter_state(self.media_filter_ui_state, "media")
            asyncio.create_task(self.query_one("#media").refresh_list())
        elif action == "match_discovered":
            self.job_manager.submit(
                job_type=JobType.DISCOVER_MATCH,
                description="Match Discovered Tracks",
                target_func=self.matching_engine.process_discovered_tracks,
            )
            self.show_status("Matching discovered tracks.")
            asyncio.create_task(self.refresh_jobs_view())
            asyncio.create_task(self.query_one("#home").refresh_summary())
        elif action == "review":
            asyncio.create_task(self.start_review_all_session())
        elif action == "refresh_stale_collections":
            collections = payload.get("collections", [])
            queued = 0
            for collection in collections:
                source_type = collection.get("source_type")
                if source_type in self.plugins:
                    self.job_manager.submit(
                        job_type=JobType.REFRESH_COLLECTION,
                        description=f"Refresh: {collection.get('name', 'Collection')}",
                        target_func=self.fetch_and_save_collection,
                        collection=collection,
                    )
                    queued += 1
            self.show_status(f"Refreshing {queued} stale collections." if queued else "No refreshable collections.")
            asyncio.create_task(self.refresh_jobs_view())
            asyncio.create_task(self.query_one("#home").refresh_summary())
        elif action == "update_stale_exports":
            exports = payload.get("exports", [])
            for export in exports:
                collection_id = export.get("collection_id")
                self.job_manager.submit(
                    job_type=JobType.UPDATE_EXPORT,
                    description=f"Update Export: {export.get('collection_name', 'Managed Export')}",
                    target_func=self.export_manager.update_managed_export,
                    export_id=export.get("export_id"),
                    depends_on=self.active_download_dependencies(collection_id),
                    metadata={"operation": "export", "collection_id": collection_id},
                )
            self.show_status(f"Updating {len(exports)} stale exports.")
            asyncio.create_task(self.refresh_jobs_view())
            asyncio.create_task(self.query_one("#home").refresh_summary())
        elif action == "analyze_audio_features":
            count = self.audio_analysis_manager.count_missing_features()
            if not count:
                self.show_status("No downloaded tracks need audio analysis.")
                asyncio.create_task(self.query_one("#home").refresh_summary())
                return
            self.job_manager.submit(
                job_type=JobType.ANALYZE_TRACK,
                description=f"Analyze Audio Features ({count})",
                target_func=self.audio_analysis_manager.analyze_missing_features,
                metadata={"operation": "analysis"},
            )
            self.show_status(f"Analyzing audio features for {count} tracks.")
            asyncio.create_task(self.refresh_jobs_view())
            asyncio.create_task(self.query_one("#home").refresh_summary())
        elif action == "audio_analysis_summary":
            self.open_details_screen("audio_analysis_summary")

    def show_audio_analysis_filter(self, filter_key: str) -> None:
        self.action_navigate("media")
        self.media_filter_ui_state = self.default_filter_ui_state()
        self.media_filter_ui_state.update({
            "status_discovered": False,
            "status_matched": False,
            "status_failed": False,
            "status_downloaded": True,
            "status_review": False,
            "flag_archived": False,
            filter_key: True,
        })
        self.media_filter = self.query_from_filter_state(self.media_filter_ui_state, "media")
        asyncio.create_task(self.query_one("#media").refresh_list())
