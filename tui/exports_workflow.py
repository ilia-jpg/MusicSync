import re
from pathlib import Path

from textual.widgets import ContentSwitcher

from core.jobs import JobType
from tui.flows.management import ManagementFlow
from tui.screens.exports import ExportDetailsScreen, ExportsScreen, export_status


class ExportWorkflowMixin:
    async def commit_export_target_path(self, flow: ManagementFlow) -> None:
        if not flow.collection_id:
            self.show_status("No collection selected.")
            return

        mode = flow.setting_type or "STATIC"
        collection_name = flow.collection_name or "Collection"
        export_root = flow.payload.get("export_root")
        if export_root:
            rel_path = flow.query.strip()
            if Path(rel_path).is_absolute():
                self.show_status("Sub-folder must be relative.")
                return
            relative_parts = [part for part in Path(rel_path).parts if part not in ("", ".")]
            if any(part == ".." for part in relative_parts):
                self.show_status("Sub-folder cannot leave the export root.")
                return
            safe_col_name = re.sub(r'[<>:"/\\|?*]', '', collection_name).strip() or "Collection"
            target_dir = Path(export_root).expanduser()
            if relative_parts:
                target_dir = target_dir.joinpath(*relative_parts)
            target_path = str(target_dir / safe_col_name)
        else:
            target_path = flow.query.strip()
            if not target_path:
                self.show_status("Target path cannot be empty.")
                return
            target_path = str(Path(target_path).expanduser())

        origin = flow.origin_screen_id
        actions = flow.origin_actions
        self.management_flow = None
        self.query_one(ContentSwitcher).current = origin
        self._focus_main_content(origin)
        self.update_current_context_actions()
        dependencies = [] if mode == "MANAGED" else self.active_download_dependencies(flow.collection_id)
        self.job_manager.submit(
            job_type=JobType.UPDATE_EXPORT,
            description=f"{'Create Managed Export' if mode == 'MANAGED' else 'Export'}: {collection_name}",
            target_func=self.export_manager.create_export,
            collection_id=flow.collection_id,
            target_path=target_path,
            mode=mode,
            depends_on=dependencies,
            metadata={"operation": "export", "collection_id": flow.collection_id},
        )
        await self.refresh_exports_view()
        if dependencies:
            self.show_status(f"Export queued after download: {collection_name}.")
        elif mode == "MANAGED":
            self.show_status(f"Creating managed export: {collection_name}.")
        else:
            self.show_status(f"Exporting {collection_name}.")

    async def commit_export_delete(self, flow: ManagementFlow) -> None:
        export_id = flow.setting_path
        if not export_id:
            await self.cancel_management_flow()
            return
        delete_folder = flow.setting_type == "delete_folder"
        origin = flow.origin_screen_id
        self.management_flow = None
        self.query_one(ContentSwitcher).current = "exports" if origin == "export_details" else origin
        if delete_folder:
            self.job_manager.submit(
                job_type=JobType.UPDATE_EXPORT,
                description="Delete Export Folder",
                target_func=self.export_manager.delete_managed_export,
                export_id=export_id,
                delete_folder=True,
            )
            self.show_status("Deleting export folder.")
        else:
            self.export_manager.delete_managed_export(export_id, delete_folder=False)
            self.show_status("Stopped managing export.")
        self.current_export_id = None
        await self.refresh_exports_view()
        self._focus_main_content("exports")
        self.update_current_context_actions()

    async def start_export_flow(self, collection: dict | None = None) -> None:
        current = self.query_one(ContentSwitcher).current
        if collection:
            flow = ManagementFlow(
                kind="export_mode_select",
                origin_screen_id=current,
                origin_actions=self.current_context_actions,
                collection_id=collection.get("collection_id"),
                collection_name=collection.get("name", "Collection"),
                options=["Static Export", "Managed Export"],
            )
            await self.prepare_export_mode_or_confirm(flow)
            await self.start_management_flow(flow)
            return

        collections = self.collection_manager.get_all_collections()
        await self.start_management_flow(
            ManagementFlow(
                kind="export_collection_select",
                origin_screen_id=current,
                origin_actions=self.current_context_actions,
                results=collections,
            )
        )

    async def prepare_export_mode_or_confirm(self, flow: ManagementFlow) -> None:
        flow.kind = "export_mode_select"
        flow.options = ["Static Export", "Managed Export"]
        flow.selected_index = 0

    def configured_export_roots(self) -> list[Path]:
        roots = self.config.get("exports.roots", ["./exports_root"])
        if isinstance(roots, str):
            roots = [roots]
        return [Path(root).expanduser().resolve() for root in roots if root]

    async def prepare_export_root_select(self, flow: ManagementFlow) -> bool:
        roots = self.configured_export_roots()
        if not roots:
            self.show_status("No export roots configured.")
            return False
        flow.kind = "export_root_select"
        flow.results = [{"path": str(root), "display": str(root)} for root in roots]
        flow.selected_index = 0
        return True

    def active_download_dependencies(self, collection_id: str | None) -> list[str]:
        if not collection_id:
            return []
        return [
            job.id
            for job in self.job_manager.find_active_jobs(
                job_type=JobType.DOWNLOAD_COLLECTION,
                metadata={"operation": "download", "collection_id": collection_id},
            )
        ]

    async def create_update_export_job(self) -> None:
        export = self.get_current_export()
        if not export:
            self.show_status("No export selected.")
            return
        status = export_status(export, self.configured_export_roots())
        if status["label"] == "Device Disconnected":
            self.show_status("Connect the export device before updating.")
            return
        if status["label"] == "Missing":
            self.show_status("Export folder is missing. Recover or recreate the export.")
            return
        collection_id = export.get("collection_id")
        preflight = self.export_manager.export_preflight(collection_id) if collection_id else {"missing": 0}
        if preflight.get("missing", 0) > 0:
            current = self.query_one(ContentSwitcher).current
            await self.start_management_flow(
                ManagementFlow(
                    kind="update_export_missing_confirm",
                    origin_screen_id=current,
                    origin_actions=self.current_context_actions,
                    setting_path=export.get("export_id"),
                    payload={
                        **preflight,
                        "collection_id": collection_id,
                        "collection_name": export.get("collection_name", "Managed Export"),
                    },
                    options=["Continue Update", "Cancel"],
                )
            )
            return
        self.submit_update_export_job(export.get("export_id"), collection_id, export.get("collection_name", "Managed Export"))
        await self.refresh_jobs_view()

    async def start_shuffle_export_flow(self) -> None:
        export = self.get_current_export()
        if not export:
            self.show_status("No export selected.")
            return
        status = export_status(export, self.configured_export_roots())
        if status["label"] == "Device Disconnected":
            self.show_status("Connect the export device before shuffling.")
            return
        if status["label"] == "Missing":
            self.show_status("Export folder is missing. Recover or recreate the export.")
            return
        collection_id = export.get("collection_id")
        preflight = self.export_manager.export_preflight(collection_id) if collection_id else {"downloaded": 0, "total": 0, "missing": 0}
        if preflight.get("downloaded", 0) <= 0:
            self.show_status("No downloaded tracks available to shuffle.")
            return
        await self.start_management_flow(
            ManagementFlow(
                kind="export_shuffle_choice",
                origin_screen_id=self.query_one(ContentSwitcher).current,
                origin_actions=self.current_context_actions,
                setting_path=export.get("export_id"),
                collection_id=collection_id,
                collection_name=export.get("collection_name", "Managed Export"),
                payload=preflight,
                options=["Shuffle All", "Shuffle Limited Number", "Cancel"],
            )
        )

    def submit_update_export_job(self, export_id: str | None, collection_id: str | None, collection_name: str) -> None:
        dependencies = self.active_download_dependencies(collection_id)
        self.job_manager.submit(
            job_type=JobType.UPDATE_EXPORT,
            description=f"Update Export: {collection_name}",
            target_func=self.export_manager.update_managed_export,
            export_id=export_id,
            depends_on=dependencies,
            metadata={"operation": "export", "collection_id": collection_id},
        )
        if dependencies:
            self.show_status(f"Export update queued after download: {collection_name}.")
        else:
            self.show_status(f"Updating export: {collection_name}.")

    def submit_shuffle_export_job(self, export_id: str | None, collection_id: str | None, collection_name: str, limit: int | None) -> None:
        dependencies = self.active_download_dependencies(collection_id)
        description = f"Shuffle Export: {collection_name}" if limit is None else f"Shuffle Export ({limit}): {collection_name}"
        self.job_manager.submit(
            job_type=JobType.UPDATE_EXPORT,
            description=description,
            target_func=self.export_manager.shuffle_managed_export,
            export_id=export_id,
            limit=limit,
            depends_on=dependencies,
            metadata={"operation": "export", "collection_id": collection_id},
        )
        if dependencies:
            self.show_status(f"Export shuffle queued after download: {collection_name}.")
        else:
            self.show_status(f"Shuffling export: {collection_name}.")

    async def start_clear_export_flow(self) -> None:
        export = self.get_current_export()
        if not export:
            self.show_status("No export selected.")
            return
        status = export_status(export, self.configured_export_roots())
        if status["label"] == "Device Disconnected":
            self.show_status("Connect the export device before clearing.")
            return
        if status["label"] == "Missing":
            self.show_status("Export folder is missing. Recover or recreate the export.")
            return
        await self.start_management_flow(
            ManagementFlow(
                kind="export_clear_confirm",
                origin_screen_id=self.query_one(ContentSwitcher).current,
                origin_actions=self.current_context_actions,
                setting_path=export.get("export_id"),
                collection_id=export.get("collection_id"),
                collection_name=export.get("collection_name", "Managed Export"),
                options=["Yes", "No"],
            )
        )

    async def commit_export_clear(self, flow: ManagementFlow) -> None:
        export_id = flow.setting_path
        if not export_id:
            await self.cancel_management_flow()
            return
        collection_name = flow.collection_name or "Managed Export"
        origin = flow.origin_screen_id
        actions = flow.origin_actions
        self.management_flow = None
        self.query_one(ContentSwitcher).current = origin
        self._focus_main_content(origin)
        self.update_current_context_actions()
        self.job_manager.submit(
            job_type=JobType.UPDATE_EXPORT,
            description=f"Clear Export: {collection_name}",
            target_func=self.export_manager.clear_managed_export,
            export_id=export_id,
            metadata={"operation": "export", "collection_id": flow.collection_id},
        )
        self.show_status(f"Clearing export: {collection_name}.")
        await self.refresh_jobs_view()

    async def create_recover_exports_job(self) -> None:
        roots = self.config.get("exports.roots", ["./exports_root"])
        if not roots:
            self.show_status("No export roots configured.")
            return
        self.job_manager.submit(
            job_type=JobType.UPDATE_EXPORT,
            description="Recover Managed Exports",
            target_func=self.export_manager.scan_and_recover,
        )
        self.show_status("Recovering managed exports.")
        await self.refresh_jobs_view()

    async def start_export_delete_flow(self) -> None:
        export = self.get_current_export()
        if not export:
            self.show_status("No export selected.")
            return
        await self.start_management_flow(
            ManagementFlow(
                kind="export_delete_choice",
                origin_screen_id=self.query_one(ContentSwitcher).current,
                origin_actions=self.current_context_actions,
                setting_path=export.get("export_id"),
                collection_name=export.get("collection_name"),
                options=["Stop Managing Export", "Delete Export Folder", "Cancel"],
            )
        )

    def get_current_export(self) -> dict | None:
        current = self.query_one(ContentSwitcher).current
        if current == "exports":
            try:
                return self.query_one("#exports", ExportsScreen).get_selected_export()
            except Exception:
                pass
        export_id = getattr(self, "current_export_id", None)
        if not export_id:
            return None
        return next((item for item in self.export_manager.get_managed_exports() if item.get("export_id") == export_id), None)

    async def refresh_exports_view(self) -> None:
        current = self.query_one(ContentSwitcher).current
        try:
            exports_screen = self.query_one("#exports", ExportsScreen)
            exports_screen._needs_refresh = True
            if current == "exports":
                await exports_screen.refresh_exports()
        except Exception:
            pass
        if current == "export_details":
            try:
                details = self.query_one("#export_details", ExportDetailsScreen)
                details._needs_refresh = False
                details.refresh_details()
            except Exception:
                pass

    def update_exports_context_actions(self) -> None:
        current = self.query_one(ContentSwitcher).current
        export = self.get_current_export()
        if current == "exports":
            lines = ["A      Add", "J      Jump", "F      Find"]
            if export:
                lines.insert(0, "Enter  Open")
                lines.extend(self.export_action_lines(export))
            self.update_context_actions("\n".join(lines))
        elif current == "export_details":
            lines = ["Esc    Back"]
            if export:
                lines.extend(self.export_action_lines(export))
            self.update_context_actions("\n".join(lines))

    def export_action_lines(self, export: dict) -> list[str]:
        status = export_status(export, self.configured_export_roots())
        label = status.get("label")
        lines = []
        if label in ("Device Disconnected", "Missing"):
            lines.extend(["R      Recover", "D      Delete"])
            return lines
        lines.append("S      Shuffle")
        if label != "Empty":
            lines.append("C      Clear")
        lines.extend(["U      Update", "D      Delete"])
        return lines

    async def refresh_export_details_if_status_changed(self) -> None:
        self.query_one("#export_details", ExportDetailsScreen).refresh_if_status_changed()
