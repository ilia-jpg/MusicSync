from dataclasses import dataclass, field


@dataclass
class ReviewSession:
    items: list[dict]
    origin_screen_id: str
    origin_index: int
    origin_actions: str
    current_index: int = 0
    candidate_index: int = 0
    approved_count: int = 0
    skipped_count: int = 0
    reviewed_count: int = 0
    complete: bool = False
    candidates: list[dict] = field(default_factory=list)

    @property
    def current_item(self) -> dict | None:
        if self.complete or not self.items:
            return None
        return self.items[self.current_index]

    @property
    def total(self) -> int:
        return len(self.items)
