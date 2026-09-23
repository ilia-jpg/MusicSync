import re
from pathlib import Path

from textual.widgets import ContentSwitcher

from core.jobs import JobType
from tui.flows.management import ManagementFlow
from tui.screens.circuits import CircuitCirculationPreviewScreen, CircuitDetailsScreen, CircuitsScreen, circuit_status


class CircuitsWorkflowMixin:
    async def start_circuit_flow(self) -> None:
        collections = self.collection_manager.get_all_collections()
        options, results = self.circuit_source_options(collections, {0})
        await self.start_management_flow(
            ManagementFlow(
                kind="circuit_source_select",
                origin_screen_id=self.query_one(ContentSwitcher).current,
                origin_actions=self.current_context_actions,
                options=options,
                results=results,
                selected_indices={0},
            )
        )

    async def commit_circuit_target_path(self, flow: ManagementFlow) -> None:
        name = (flow.collection_name or "").strip()
        if not name:
            self.show_status("Circuit name cannot be empty.")
            return
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
            safe_name = re.sub(r'[<>:"/\\|?*]', "", name).strip() or "Circuit"
            target_dir = Path(export_root).expanduser()
            if relative_parts:
                target_dir = target_dir.joinpath(*relative_parts)
            target_path = str(target_dir / safe_name)
        else:
            target_path = flow.query.strip()
            if not target_path:
                self.show_status("Target path cannot be empty.")
                return
            target_path = str(Path(target_path).expanduser())

        source_config = flow.payload.get("source_config") or {"type": "all_downloaded"}
        target_count = int(flow.payload.get("target_count") or 25)
        circuit_id = self.circuit_manager.create_circuit(
            name,
            target_path,
            source_config,
            {"mode": "shuffle", "target_count": target_count, "limit": target_count},
        )
        self.management_flow = None
        self.query_one(ContentSwitcher).current = "circuits"
        await self.refresh_circuits_view()
        screen = self.query_one("#circuits")
        for index, item in enumerate(getattr(screen, "_display_data", [])):
            if item.get("circuit_id") == circuit_id:
                screen.set_list_index(index)
                break
        self._focus_main_content("circuits")
        self.update_current_context_actions()
        self.show_status(f"Created circuit: {name}.")

    async def start_circulate_flow(self) -> None:
        circuit = self.get_current_circuit()
        if not circuit:
            self.show_status("No circuit selected.")
            return
        status = circuit_status(circuit)
        if status["label"] == "Missing":
            self.show_status("Circuit folder is missing.")
            return
        plan = self.circuit_manager.plan_circulation(circuit.get("circuit_id"), include_add_items=False)
        self.current_circuit_plan = plan
        self.current_circuit_preview_id = circuit.get("circuit_id")
        self.current_circuit_preview_origin = self.query_one(ContentSwitcher).current
        self.current_circuit_preview_name = circuit.get("name") or "Circuit"
        self._show_screen("circuit_preview")
        await self.query_one("#circuit_preview", CircuitCirculationPreviewScreen).refresh_preview()

    async def confirm_circuit_preview_selection(self, marker_position: int) -> None:
        circuit_id = getattr(self, "current_circuit_preview_id", None)
        circuit_name = getattr(self, "current_circuit_preview_name", None) or "Circuit"
        origin = getattr(self, "current_circuit_preview_origin", None) or "circuits"
        if not circuit_id:
            self.show_status("No circuit selected.")
            return
        preview_plan = getattr(self, "current_circuit_plan", None) or {}
        plan = self.circuit_manager.plan_circulation(
            circuit_id,
            marker_position=marker_position,
            use_marker_override=True,
            include_add_items=True,
            feedback_changes=preview_plan.get("feedback_changes") or {},
        )
        for key in ("feedback", "base_feedback", "feedback_changes", "feedback_scope"):
            if key in preview_plan:
                plan[key] = preview_plan[key]
        self.current_circuit_plan = None
        self.current_circuit_preview_id = None
        self.current_circuit_preview_origin = None
        self.current_circuit_preview_name = None
        self._remove_circuit_preview_from_stack()
        self.query_one(ContentSwitcher).current = origin
        self._focus_main_content(origin)
        self.update_current_context_actions()
        self.submit_circulate_job(circuit_id, circuit_name, plan)
        await self.refresh_jobs_view()

    def cancel_circuit_preview(self) -> None:
        origin = getattr(self, "current_circuit_preview_origin", None) or "circuits"
        self.current_circuit_plan = None
        self.current_circuit_preview_id = None
        self.current_circuit_preview_origin = None
        self.current_circuit_preview_name = None
        self._remove_circuit_preview_from_stack()
        self.query_one(ContentSwitcher).current = origin
        self._focus_main_content(origin)
        self.update_current_context_actions()
        self.show_status("Cancelled.")

    def _remove_circuit_preview_from_stack(self) -> None:
        nav_stack = getattr(self, "_nav_stack", [])
        while nav_stack and nav_stack[-1] == "circuit_preview":
            nav_stack.pop()

    def submit_circulate_job(self, circuit_id: str | None, circuit_name: str, plan: dict | None) -> None:
        if not circuit_id:
            self.show_status("No circuit selected.")
            return
        self.job_manager.submit(
            job_type=JobType.UPDATE_CIRCUIT,
            description=f"Circulate: {circuit_name}",
            target_func=self.circuit_manager.circulate_circuit,
            circuit_id=circuit_id,
            plan=plan,
            metadata={"operation": "circuit_circulate", "circuit_id": circuit_id},
        )
        self.show_status(f"Circulating: {circuit_name}.")

    def circulation_preview_rows(self, plan: dict) -> list[str]:
        stop = plan.get("stop_marker")
        marker = stop.get("filename") if stop else "None"
        rows = [
            f"Target: {plan.get('target_count', 0)} tracks",
            f"Currently on device: {plan.get('current_count', 0)}",
            f"Progress marker: {marker}",
            f"Will remove: {len(plan.get('remove_items') or [])} heard tracks",
            f"Will keep: {len(plan.get('keep_items') or [])} pending tracks",
            f"Will add: {plan.get('add_count', 0)} new tracks",
            "",
            "Enter  Circulate",
            "Esc    Cancel",
        ]
        remove_items = plan.get("remove_items") or []
        keep_items = plan.get("keep_items") or []
        if remove_items:
            rows.extend(["", "Heard / will remove:"])
            rows.extend(f"  {item.get('filename')}" for item in remove_items[:12])
            if len(remove_items) > 12:
                rows.append(f"  +{len(remove_items) - 12} more")
        if keep_items:
            rows.extend(["", "Still pending / will keep:"])
            rows.extend(f"  {item.get('filename')}" for item in keep_items[:12])
            if len(keep_items) > 12:
                rows.append(f"  +{len(keep_items) - 12} more")
        return rows

    async def start_edit_circuit_flow(self) -> None:
        circuit = self.get_current_circuit()
        if not circuit:
            self.show_status("No circuit selected.")
            return
        collections = self.collection_manager.get_all_collections()
        source = circuit.get("source_config") or {}
        selected_indices = set()
        if source.get("type") == "all_downloaded":
            selected_indices.add(0)
        else:
            selected_ids = set(source.get("collection_ids") or [source.get("collection_id")])
            options, results = self.circuit_source_options(collections, set())
            for index, row in enumerate(results):
                collection = row.get("collection") or {}
                if row.get("type") == "collection" and collection.get("collection_id") in selected_ids:
                    selected_indices.add(index)
        options, results = self.circuit_source_options(collections, selected_indices or {0})
        await self.start_management_flow(
            ManagementFlow(
                kind="circuit_edit_source_select",
                origin_screen_id=self.query_one(ContentSwitcher).current,
                origin_actions=self.current_context_actions,
                setting_path=circuit.get("circuit_id"),
                collection_name=circuit.get("name"),
                options=options,
                results=results,
                selected_indices=selected_indices or {0},
                payload={"circuit": circuit},
            )
        )

    def circuit_source_options(self, collections: list[dict], selected_indices: set[int]) -> tuple[list[str], list[dict]]:
        rows: list[str] = []
        results: list[dict] = []

        def add_row(row: dict, label: str) -> None:
            results.append(row)
            rows.append(label)

        all_mark = "☑" if 0 in selected_indices else "☐"
        add_row({"type": "all_downloaded", "name": "All Downloaded"}, f"{all_mark} All Downloaded")

        collections_by_group: dict[str | None, list[dict]] = {None: []}
        for group in self.collection_manager.get_collection_groups():
            collections_by_group[group.get("group_id")] = []
        for collection in collections:
            collections_by_group.setdefault(collection.get("group_id"), []).append(collection)

        group_defs = [{"group_id": None, "name": "Ungrouped"}, *self.collection_manager.get_collection_groups()]
        for group in group_defs:
            group_id = group.get("group_id")
            group_collections = sorted(
                collections_by_group.get(group_id, []),
                key=lambda item: str(item.get("name") or "").lower(),
            )
            if not group_collections:
                continue
            add_row(
                {"type": "group", "group_id": group_id, "name": group.get("name") or "Ungrouped"},
                f"- {group.get('name') or 'Ungrouped'}",
            )
            for collection in group_collections:
                row_index = len(results)
                mark = "☑" if row_index in selected_indices else "☐"
                count = int(collection.get("track_count") or 0)
                suffix = f"  {count} track{'s' if count != 1 else ''}"
                add_row(
                    {"type": "collection", "collection": collection},
                    f"    {mark} {collection.get('name', 'Collection')}{suffix}",
                )
        add_row({"type": "save_continue"}, "Save and Continue")
        return rows, results

    def source_config_from_flow(self, flow: ManagementFlow) -> tuple[dict, str]:
        if 0 in flow.selected_indices or not flow.selected_indices:
            return {"type": "all_downloaded"}, "All Downloaded"
        ids = []
        names = []
        for index in sorted(flow.selected_indices):
            if index <= 0 or index >= len(flow.results):
                continue
            row = flow.results[index]
            if row.get("type") != "collection":
                continue
            collection = row.get("collection") or {}
            ids.append(collection.get("collection_id"))
            names.append(collection.get("name", "Collection"))
        if not ids:
            return {"type": "all_downloaded"}, "All Downloaded"
        return {"type": "collections", "collection_ids": ids, "collection_names": names}, ", ".join(names)

    async def start_delete_circuit_flow(self) -> None:
        circuit = self.get_current_circuit()
        if not circuit:
            self.show_status("No circuit selected.")
            return
        await self.start_management_flow(
            ManagementFlow(
                kind="circuit_delete_confirm",
                origin_screen_id=self.query_one(ContentSwitcher).current,
                origin_actions=self.current_context_actions,
                setting_path=circuit.get("circuit_id"),
                collection_name=circuit.get("name"),
                options=["Yes", "No"],
            )
        )

    async def commit_delete_circuit_flow(self, flow: ManagementFlow) -> None:
        if flow.selected_index != 0:
            await self.cancel_management_flow()
            return
        circuit_id = flow.setting_path
        name = flow.collection_name or "Circuit"
        self.circuit_manager.delete_circuit(circuit_id)
        self.management_flow = None
        self.query_one(ContentSwitcher).current = "circuits"
        self.current_circuit_id = None
        await self.refresh_circuits_view()
        self._focus_main_content("circuits")
        self.update_current_context_actions()
        self.show_status(f"Deleted circuit: {name}.")

    def get_current_circuit(self) -> dict | None:
        current = self.query_one(ContentSwitcher).current
        if current == "circuits":
            try:
                return self.query_one("#circuits", CircuitsScreen).get_selected_circuit()
            except Exception:
                pass
        circuit_id = getattr(self, "current_circuit_id", None)
        if not circuit_id:
            return None
        return self.circuit_manager.get_circuit(circuit_id)

    async def refresh_circuits_view(self) -> None:
        current = self.query_one(ContentSwitcher).current
        try:
            circuits_screen = self.query_one("#circuits", CircuitsScreen)
            circuits_screen._needs_refresh = True
            if current == "circuits":
                await circuits_screen.refresh_circuits()
        except Exception:
            pass

    def circuit_preview_context_actions(self) -> str:
        return "Enter  Circulate\nL      Love\nB      Boo\nG      Global/Here\nEsc    Cancel"

    def update_circuits_context_actions(self) -> None:
        current = self.query_one(ContentSwitcher).current
        circuit = self.get_current_circuit()
        if current == "circuits":
            lines = ["A      Add"]
            if circuit:
                lines.insert(0, "Enter  Open")
                lines.extend(self.circuit_action_lines(circuit))
            self.update_context_actions("\n".join(lines))
        elif current == "circuit_details":
            lines = ["Esc    Back"]
            if circuit:
                lines.extend(self.circuit_action_lines(circuit))
            self.update_context_actions("\n".join(lines))

    def circuit_action_lines(self, circuit: dict) -> list[str]:
        status = circuit_status(circuit)
        lines = []
        if status["label"] != "Missing":
            lines.append("C      Circulate")
            lines.append("E      Edit")
        lines.append("D      Delete")
        return lines
