from dataclasses import dataclass, field


@dataclass
class ManagementFlow:
    kind: str
    origin_screen_id: str
    origin_actions: str
    collection_id: str | None = None
    collection_name: str | None = None
    options: list[str] = field(default_factory=list)
    selected_index: int = 0
    anchor_index: int = 0
    selected_indices: set[int] = field(default_factory=set)
    fields: list[tuple[str, str]] = field(default_factory=list)
    field_index: int = 0
    query: str = ""
    results: list[dict] = field(default_factory=list)
    source_results: list[dict] = field(default_factory=list)
    page_offset: int = 0
    page_size: int = 20
    total_count: int = 0
    origin_index: int = 0
    song_id: str | None = None
    setting_path: str | None = None
    setting_type: str | None = None
    payload: dict = field(default_factory=dict)
