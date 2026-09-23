from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Label, ListItem


class JobItem(ListItem):
    def __init__(self, name: str, progress: int, total: int, state: str):
        super().__init__()
        self.job_state = state
        self.job_name = name
        self.progress = progress
        self.total = total

    def compose(self) -> ComposeResult:
        percent = self.progress / self.total if self.total > 0 else 0
        filled = int(20 * percent)
        bar = "[" + "\u2588" * filled + "\u2591" * (20 - filled) + "]"

        with Vertical(classes="job-container"):
            with Horizontal(classes="job-row-top"):
                yield Label(self.job_name, classes="job-name")
                yield Label(self.job_state, classes=f"job-status-{self.job_state.lower()}")
            with Horizontal(classes="job-row-bottom"):
                yield Label(bar, classes="job-progress-bar")
                yield Label(f"{self.progress} / {self.total}", classes="job-progress-text")


class FilterOption(ListItem):
    def __init__(self, key: str, label: str, val: bool):
        super().__init__(id=f"filter-{key}")
        self.key = key
        self.label = label
        self.val = val

    def compose(self) -> ComposeResult:
        box = "\u2611" if self.val else "\u2610"
        yield Label(f"{box} {self.label}", classes="filter-option")

    def toggle(self) -> None:
        self.val = not self.val
        box = "\u2611" if self.val else "\u2610"
        self.query_one(Label).update(f"{box} {self.label}")


class FilterText(ListItem):
    def __init__(self, key: str, label: str, text_val: str):
        super().__init__(id=f"filter-{key}")
        self.key = key
        self.label = label
        self.text_val = text_val

    def compose(self) -> ComposeResult:
        yield Label(f"{self.label}: {self.text_val}_", classes="filter-text")

    def update_text(self, new_text: str) -> None:
        self.text_val = new_text
        self.query_one(Label).update(f"{self.label}: {self.text_val}_")

