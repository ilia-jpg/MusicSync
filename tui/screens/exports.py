from pathlib import Path
from datetime import datetime, timezone
from typing import Any
import asyncio
import json

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Label, ListItem, ListView, Rule


def export_folder_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    try:
        for child in path.rglob("*"):
            try:
                if child.is_file():
                    total += child.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


def format_export_size(size_bytes: int) -> str:
    if size_bytes >= 1024 ** 3:
        return f"{size_bytes / (1024 ** 3):.1f} GB"
    return f"{size_bytes / (1024 ** 2):.0f} MB"


def export_shuffle_label(export: dict[str, Any]) -> str | None:
    if str(export.get("last_operation") or "update").lower() != "shuffle":
        return None
    limit = export.get("shuffle_limit")
    if limit is None or str(limit).strip() == "":
        return "Shuffled All"
    try:
        return f"Shuffled {int(limit)}"
    except (TypeError, ValueError):
        return f"Shuffled {limit}"


def export_operation_label(export: dict[str, Any]) -> str | None:
    operation = str(export.get("last_operation") or "update").lower()
    if operation in ("clear", "create"):
        return "Empty"
    return export_shuffle_label(export)


def export_secondary_text(export: dict[str, Any], status: dict[str, str]) -> str:
    operation_label = export_operation_label(export)
    target_dir = Path(export.get("target_path") or "")
    if status["label"] == "Device Disconnected":
        detail = "Device disconnected"
    elif status["label"] == "Missing":
        detail = "Missing"
    elif status["label"] == "Missing Manifest":
        detail = format_export_size(export_folder_size(target_dir)) if target_dir.exists() else "Missing manifest"
    else:
        detail = format_export_size(export_folder_size(target_dir))
    return f"{operation_label} | {detail}" if operation_label else detail


def export_needs_attention(status: dict[str, str]) -> bool:
    return status["label"] not in ("Up To Date", "Shuffled", "Empty")


def export_status(export: dict[str, Any], export_roots: list[Path] | None = None) -> dict[str, str]:
    target_dir = Path(export.get("target_path") or "")
    manifest = target_dir / ".musicsync.json"
    if not target_dir.exists():
        matching_root = export_matching_root(target_dir, export_roots or [])
        if matching_root is not None and not matching_root.exists():
            return {"label": "Device Disconnected", "icon": "?", "class": "disconnected", "manifest": "Unknown"}
        return {"label": "Missing", "icon": "?", "class": "warning", "manifest": "Missing"}
    if not manifest.exists():
        return {"label": "Missing Manifest", "icon": "?", "class": "warning", "manifest": "Missing"}
    operation = str(export.get("last_operation") or "update").lower()
    if operation in ("clear", "create"):
        return {"label": "Empty", "icon": "E", "class": "empty", "manifest": "Present"}
    if operation == "shuffle":
        return {"label": "Shuffled", "icon": "S", "class": "shuffled", "manifest": "Present"}
    manifest_data = _read_manifest(manifest)
    exported_signature = manifest_data.get("track_signature")
    current_signature = export.get("current_track_signature")
    if exported_signature is not None and current_signature is not None:
        if exported_signature == current_signature:
            return {"label": "Up To Date", "icon": "OK", "class": "success", "manifest": "Present"}
        return {"label": "Out Of Date", "icon": "!", "class": "warning", "manifest": "Present"}
    collection_stamp = _parse_iso(export.get("collection_last_sync")) or _parse_iso(export.get("collection_updated_at"))
    export_stamp = _parse_iso(export.get("last_updated"))
    if collection_stamp and export_stamp and collection_stamp > export_stamp:
        return {"label": "Out Of Date", "icon": "!", "class": "warning", "manifest": "Present"}
    return {"label": "Up To Date", "icon": "OK", "class": "success", "manifest": "Present"}


def export_matching_root(target_dir: Path, export_roots: list[Path]) -> Path | None:
    try:
        resolved_target = target_dir.expanduser().resolve(strict=False)
    except OSError:
        resolved_target = target_dir.expanduser().absolute()
    best_match: Path | None = None
    for root in export_roots:
        try:
            resolved_root = root.expanduser().resolve(strict=False)
        except OSError:
            resolved_root = root.expanduser().absolute()
        try:
            resolved_target.relative_to(resolved_root)
        except ValueError:
            continue
        if best_match is None or len(str(resolved_root)) > len(str(best_match)):
            best_match = resolved_root
    return best_match

def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def export_search_score(query: str, text: str) -> float:
    q = query.lower()
    t = text.lower()
    if not q:
        return 1000.0
    if t.startswith(q):
        return 5000.0 - len(t)
    if q in t:
        return 1000.0 - len(t)
    return -float(len(t))


class ExportsScreen(VerticalScroll):
    can_focus = False

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._display_data: list[dict] = []
        self._find_backup: list[dict] = []
        self._render_generation = 0
        self._needs_refresh = True
        self._render_lock = asyncio.Lock()
        self._status_signature: tuple[tuple[str, str, str, str, str, str], ...] = ()

    def compose(self) -> ComposeResult:
        yield Label("Managed Exports", id="exports-header", classes="section-header")
        yield Label("", id="exports-summary", classes="filter-indicator")
        with ListView(id="export-list"):
            pass

    async def on_show(self) -> None:
        if self._needs_refresh:
            await self.refresh_exports()
            self._needs_refresh = False

    async def refresh_exports(self) -> None:
        list_view = self.query_one("#export-list", ListView)
        previous_index = list_view.index or 0
        exports = self.app.export_manager.get_managed_exports()
        self._display_data = exports
        self._status_signature = self.status_signature(exports)
        self.query_one("#exports-header", Label).update(f"Managed Exports ({len(exports)})")
        attention_count = sum(1 for item in exports if export_needs_attention(export_status(item, self.app.configured_export_roots())))
        self.query_one("#exports-summary", Label).update(f"Exports Needing Attention: {attention_count}" if attention_count else "")
        await self.render_exports(previous_index=previous_index)
        self.app.update_exports_context_actions()

    async def refresh_if_status_changed(self) -> None:
        exports = self.app.export_manager.get_managed_exports()
        signature = self.status_signature(exports)
        if signature != self._status_signature:
            await self.refresh_exports()

    def status_signature(self, exports: list[dict]) -> tuple[tuple[str, str, str, str, str, str], ...]:
        roots = self.app.configured_export_roots()
        return tuple(
            (
                str(export.get("export_id") or ""),
                str(export.get("target_path") or ""),
                export_status(export, roots)["label"],
                str(export.get("current_track_signature") or ""),
                str(export.get("last_operation") or "update"),
                str(export.get("shuffle_limit") or ""),
            )
            for export in exports
        )

    def get_list_index(self) -> int:
        list_view = self.query_one("#export-list", ListView)
        return list_view.index or 0

    def set_list_index(self, idx: int) -> None:
        list_view = self.query_one("#export-list", ListView)
        if list_view.children:
            list_view.index = max(0, min(idx, len(list_view.children) - 1))
            self._sync_current_export()

    def save_original_ordering(self) -> None:
        self._find_backup = list(self._display_data)

    async def restore_original_ordering(self) -> None:
        self._display_data = list(self._find_backup)
        await self.render_exports()

    async def execute_jump(self, query: str) -> None:
        if not query:
            return
        q = query.lower()
        for idx, item in enumerate(self._display_data):
            if item.get("collection_name", "").lower().startswith(q):
                self.set_list_index(idx)
                return

    async def execute_find(self, query: str) -> None:
        if not query:
            self._display_data = list(self._find_backup)
        else:
            self._display_data.sort(
                key=lambda item: export_search_score(query, item.get("collection_name", "")),
                reverse=True,
            )
        await self.render_exports()
        self.set_list_index(0)

    async def render_exports(self, previous_index: int | None = None) -> None:
        async with self._render_lock:
            list_view = self.query_one("#export-list", ListView)
            if previous_index is None:
                previous_index = list_view.index or 0
            self._render_generation += 1
            generation = self._render_generation
            await list_view.clear()
            exports = self._display_data
            if not exports:
                await list_view.mount(ListItem(Label("No managed exports"), id=f"export-empty-{generation}"))
                list_view.index = 0
                return
            for idx, export in enumerate(exports):
                status = export_status(export, self.app.configured_export_roots())
                secondary_text = export_secondary_text(export, status)
                await list_view.mount(
                    ListItem(
                        Horizontal(
                            Label(status["icon"], classes=f"status-icon {status['class']}"),
                            Label(export.get("collection_name", "Managed Export"), classes="list-primary"),
                            Label(secondary_text, classes="list-secondary"),
                            classes="list-row",
                        ),
                        id=f"export-{generation}-{idx}",
                    )
                )
            list_view.index = max(0, min(previous_index, len(exports) - 1))
            self._sync_current_export()

    def get_selected_export(self) -> dict | None:
        index = self.get_list_index()
        return self._display_data[index] if 0 <= index < len(self._display_data) else None

    def _sync_current_export(self) -> None:
        selected = self.get_selected_export()
        self.app.current_export_id = selected.get("export_id") if selected else None

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        self._sync_current_export()
        if self.app.current_export_id:
            try:
                self.app.query_one("#export_details", ExportDetailsScreen)._needs_refresh = True
            except Exception:
                pass
            self.app.open_details_screen("export_details")

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        self._sync_current_export()
        self.app.update_exports_context_actions()


class ExportDetailsScreen(VerticalScroll):
    can_focus = True

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._needs_refresh = True
        self._status_signature: tuple[str, str, str, str, str, str] | None = None

    def compose(self) -> ComposeResult:
        yield Label("", id="export-details-title", classes="details-title")
        yield Rule()

        for key, value_id in (
            ("Status:", "export-details-status"),
            ("Collection:", "export-details-collection"),
            ("Path:", "export-details-path"),
            ("Size:", "export-details-size"),
            ("Last Updated:", "export-details-updated"),
            ("Manifest:", "export-details-manifest"),
        ):
            with Horizontal(classes="dense-row"):
                yield Label(key, classes="dense-key")
                yield Label("", id=value_id, classes="dense-value")

    async def on_show(self) -> None:
        if self._needs_refresh:
            self.refresh_details()
            self._needs_refresh = False

    def refresh_details(self) -> None:
        export_record = self.app.get_current_export()

        if not export_record:
            self.query_one("#export-details-title", Label).update("No export selected")
            for value_id in (
                "#export-details-path",
                "#export-details-status",
                "#export-details-size",
                "#export-details-collection",
                "#export-details-updated",
                "#export-details-manifest",
            ):
                self.query_one(value_id, Label).update("")
            self.app.update_current_context_actions()
            return

        self._status_signature = self.status_signature(export_record)
        target_dir = Path(export_record["target_path"])
        status = export_status(export_record, self.app.configured_export_roots())
        size_text = export_secondary_text(export_record, status)

        self.query_one("#export-details-title", Label).update(export_record.get("collection_name", "Managed Export"))
        self.query_one("#export-details-status", Label).update(status["label"])
        self.query_one("#export-details-collection", Label).update(export_record.get("collection_name", "Unknown Collection"))
        self.query_one("#export-details-path", Label).update(str(target_dir))
        self.query_one("#export-details-size", Label).update(size_text)
        self.query_one("#export-details-updated", Label).update(str(export_record.get("last_updated") or "Unknown"))
        self.query_one("#export-details-manifest", Label).update(status["manifest"])
        self.app.update_current_context_actions()

    def refresh_if_status_changed(self) -> None:
        export_record = self.app.get_current_export()
        signature = self.status_signature(export_record) if export_record else None
        if signature != self._status_signature:
            self.refresh_details()

    def status_signature(self, export_record: dict) -> tuple[str, str, str, str, str, str]:
        status = export_status(export_record, self.app.configured_export_roots())
        return (
            str(export_record.get("export_id") or ""),
            str(export_record.get("target_path") or ""),
            status["label"],
            str(export_record.get("current_track_signature") or ""),
            str(export_record.get("last_operation") or "update"),
            str(export_record.get("shuffle_limit") or ""),
        )
