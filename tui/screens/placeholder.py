from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Label


class PlaceholderScreen(VerticalScroll):
    can_focus = True

    def __init__(self, title: str, id: str):
        super().__init__(id=id)
        self.title_text = title

    def compose(self) -> ComposeResult:
        yield Label(f"--- {self.title_text} ---", classes="screen-title")
        yield Label("Business functionality has not been implemented yet.", classes="screen-placeholder")
