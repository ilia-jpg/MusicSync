import asyncio

from textual.widgets import ContentSwitcher


class JobWorkflowMixin:
    def run_job_action(self, action: str) -> None:
        current = self.query_one(ContentSwitcher).current
        if current not in ("jobs", "job_details"):
            return
        job_id = self.get_selected_job_id()
        if not job_id:
            self.show_status("No job selected.")
            return
        available = self.job_manager.get_available_actions(job_id)
        if action not in available:
            self.show_status(f"{action} is not available for this job.")
            return
        try:
            job = self.job_manager.get_job(job_id)
            description = job.description if job else "job"
            if action == "Pause":
                self.job_manager.pause(job_id)
            elif action == "Resume":
                self.job_manager.resume(job_id)
            elif action == "Cancel":
                self.job_manager.cancel(job_id)
            elif action == "Retry":
                new_id = self.job_manager.retry(job_id)
                self.current_job_id = new_id
            elif action == "Remove":
                self.job_manager.remove_job(job_id)
                if current == "job_details":
                    self.action_go_back()
                asyncio.create_task(self.refresh_jobs_view())
                self.show_status("Job removed.")
                return
            verb = {"Pause": "Paused", "Resume": "Resumed", "Cancel": "Cancelling", "Retry": "Retrying"}.get(action, action)
            self.show_status(f"{verb}: {description}.")
            asyncio.create_task(self.refresh_jobs_view())
        except Exception as exc:
            self.show_status(f"Job action failed: {exc}")

    def get_selected_job_id(self) -> str | None:
        current = self.query_one(ContentSwitcher).current
        if current == "jobs":
            try:
                list_view = self.query_one("#jobs-list")
                item = list_view.highlighted_child
                item_id = getattr(item, "id", None)
                if isinstance(item_id, str) and item_id.startswith("job-"):
                    self.current_job_id = item_id.split("-", 1)[1]
            except Exception:
                pass
        return getattr(self, "current_job_id", None)

    async def refresh_jobs_view(self) -> None:
        current = self.query_one(ContentSwitcher).current
        if current == "jobs":
            await self.query_one("#jobs").refresh_jobs()
        elif current == "job_details":
            self.query_one("#job_details").refresh_details()

    def update_jobs_context_actions(self) -> None:
        if self.query_one(ContentSwitcher).current != "jobs":
            return
        job_id = self.get_selected_job_id()
        if not job_id or not self.job_manager.get_job(job_id):
            self.update_context_actions("Enter  Details")
            return

        lines = ["Enter  Details"]
        key_map = {"Pause": "P", "Resume": "R", "Cancel": "C", "Retry": "T", "Remove": "D"}
        for action in self.job_manager.get_available_actions(job_id):
            key = key_map.get(action)
            if key:
                lines.append(f"{key}      {action}")
        self.update_context_actions("\n".join(lines))

    def refresh_catalog_views_for_completed_jobs(self) -> None:
        finished_statuses = {"COMPLETED", "FAILED", "CANCELLED"}
        jobs = self.job_manager.get_all_jobs()
        current_statuses = {
            job.id: job.status.name if hasattr(job.status, "name") else str(job.status)
            for job in jobs
        }
        for job in jobs:
            status = current_statuses.get(job.id)
            previous = self._known_job_statuses.get(job.id)
            if previous and previous != status and status in finished_statuses:
                self.notify_job_status(job, status)

        should_refresh = any(
            status in finished_statuses and self._known_job_statuses.get(job_id) != status
            for job_id, status in current_statuses.items()
        )
        self._known_job_statuses = current_statuses
        if should_refresh:
            self._catalog_refresh_pending = True

        if not self._catalog_refresh_pending or self._catalog_refresh_task:
            return

        current = self.query_one(ContentSwitcher).current
        if current not in ("collections", "collection_details", "media", "media_details", "circuits", "circuit_details", "exports", "export_details"):
            return

        self._catalog_refresh_pending = False
        self._catalog_refresh_task = asyncio.create_task(self.refresh_catalog_after_job_completion())

    async def refresh_catalog_after_job_completion(self) -> None:
        try:
            await self.refresh_current_list()
        finally:
            self._catalog_refresh_task = None

    def can_show_transient_job_status(self) -> bool:
        return (
            self.input_mode == "NORMAL"
            and not self.management_flow
            and not self.review_session
            and not self._status_persistent
        )

    def notify_job_status(self, job, status_name: str) -> None:
        if not self.can_show_transient_job_status():
            return
        if status_name == "COMPLETED":
            self.show_status(f"Completed: {job.description}")
        elif status_name == "FAILED":
            self.show_status(f"Failed: {job.description}. See Jobs details or the latest logs/session-*.log file.")
        elif status_name == "CANCELLED":
            self.show_status(f"Cancelled: {job.description}")
