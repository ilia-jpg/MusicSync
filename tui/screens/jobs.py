from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Label, ListItem, ListView, Rule


class JobsScreen(VerticalScroll):
    can_focus = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._rendered_job_ids: list[str] = []
        self._refreshing = False

    def compose(self) -> ComposeResult:
        yield Label("Active Jobs", classes="section-header")
        with ListView(id="jobs-list"):
            pass

    async def on_show(self) -> None:
        await self.refresh_jobs()

    async def refresh_jobs(self) -> None:
        if self._refreshing:
            return
        self._refreshing = True
        try:
            list_view = self.query_one("#jobs-list", ListView)
            previous_index = list_view.index or 0
            jobs = self.app.job_manager.get_all_jobs()
            job_ids = [job.id for job in jobs]
            if self.app.current_job_id not in job_ids:
                self.app.current_job_id = job_ids[0] if job_ids else None

            if job_ids == self._rendered_job_ids and jobs:
                for item, job in zip(list_view.children, jobs):
                    if not isinstance(item, ListItem):
                        continue
                    labels = list(item.query(Label))
                    title, detail = self.app.format_job_row(job)
                    if len(labels) >= 2:
                        labels[0].update(title)
                        labels[1].update(detail)
                self.app.update_jobs_context_actions()
                return

            await list_view.clear()
            self._rendered_job_ids = job_ids

            if not jobs:
                await list_view.mount(ListItem(Label("No jobs yet"), id="job-empty"))
                list_view.index = 0
                self.app.update_current_context_actions()
                return

            for job in jobs:
                title, detail = self.app.format_job_row(job)
                await list_view.mount(
                    ListItem(
                        Vertical(
                            Label(title, classes="job-name"),
                            Label(detail, classes="job-progress-text"),
                            classes="job-container",
                        ),
                        id=f"job-{job.id}",
                    )
                )
            list_view.index = max(0, min(previous_index, len(jobs) - 1))
            self.app.update_jobs_context_actions()
        finally:
            self._refreshing = False

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        item_id = getattr(event.item, "id", None)
        if isinstance(item_id, str) and item_id.startswith("job-"):
            self.app.current_job_id = item_id.split("-", 1)[1]
            self.app.open_details_screen("job_details")

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        item_id = getattr(event.item, "id", None)
        if isinstance(item_id, str) and item_id.startswith("job-"):
            self.app.current_job_id = item_id.split("-", 1)[1]
        self.app.update_jobs_context_actions()


class JobDetailsScreen(VerticalScroll):
    can_focus = True

    def compose(self) -> ComposeResult:
        yield Label("", id="job-details-title", classes="details-title")
        yield Rule()
        yield Label("", id="job-details-body", classes="detail-block")

    async def on_show(self) -> None:
        self.refresh_details()

    def refresh_details(self) -> None:
        job_id = getattr(self.app, "current_job_id", None)
        job = self.app.job_manager.get_job(job_id) if job_id else None
        if not job:
            self.query_one("#job-details-title", Label).update("No job selected")
            self.query_one("#job-details-body", Label).update("")
            self.app.update_current_context_actions()
            return

        status_name = job.status.name if hasattr(job.status, "name") else str(job.status)
        actions = self.app.job_manager.get_available_actions(job.id)
        action_lines = ["Esc    Back"]
        key_map = {"Pause": "P", "Resume": "R", "Cancel": "C", "Retry": "T", "Remove": "D"}
        for action in actions:
            key = key_map.get(action)
            if key:
                action_lines.append(f"{key}      {action}")
        self.app.update_current_context_actions()

        error = job.error_message or "(none)"
        current_item = job.current_item or "(none)"
        progress = f"{job.progress_current} / {job.progress_total}" if job.progress_total > 0 else status_name.title()
        self.query_one("#job-details-title", Label).update(job.description)
        self.query_one("#job-details-body", Label).update(
            f"Status: {status_name.title()}\n\n"
            f"Created: {job.created_at}\n"
            f"Started: {job.started_at or '(not started)'}\n\n"
            f"Current Item:\n{current_item}\n\n"
            f"Progress:\n{progress}\n\n"
            f"Error:\n{error}"
        )
