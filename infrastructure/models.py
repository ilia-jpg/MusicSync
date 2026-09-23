from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

@dataclass
class MediaItem:
    title: str
    artist: str
    album: Optional[str] = None
    duration_ms: Optional[int] = 0
    media_type: str = 'music'
    external_id: Optional[str] = None  # e.g., The Spotify Track ID or YouTube Video ID
    external_source: Optional[str] = None
    external_url: Optional[str] = None
    
    # NEW: Stores direct download links or youtube_ids to bypass the matching engine
    acquisition_info: Dict[str, Any] = field(default_factory=dict) 

    def has_acquisition_info(self) -> bool:
        """Returns True if the item already knows where its media file is located."""
        return bool(self.acquisition_info)

@dataclass
class Collection:
    name: str
    source_type: str
    external_id: Optional[str] = None
    external_url: Optional[str] = None
    items: List['MediaItem'] = field(default_factory=list)
    
    # NEW: Fields populated by the Query Engine for the UI
    id: Optional[str] = None
    archived: bool = False
    item_count: int = 0


@dataclass
class AcquisitionCandidate:
    provider: str
    external_id: str
    title: str
    confidence: float
    duration_seconds: int = 0
    acquisition_info: Dict[str, Any] = field(default_factory=dict)
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_label(self) -> str:
        minutes, seconds = divmod(int(self.duration_seconds or 0), 60)
        return f"{minutes}:{seconds:02d}"
