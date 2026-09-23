from dataclasses import dataclass, field
from typing import Optional, List

@dataclass
class MediaQuery:
    """Standardized search parameters for the Media Catalog."""
    text: Optional[str] = None
    collection_id: Optional[str] = None
    source_collection_id: Optional[str] = None
    media_type: Optional[str] = None
    media_types: Optional[List[str]] = None
    status_groups: Optional[List[str]] = None
    
    downloaded: Optional[bool] = None
    matched: Optional[bool] = None
    archived: Optional[bool] = None
    orphaned: Optional[bool] = None
    file_missing: Optional[bool] = None
    in_review_queue: Optional[bool] = None
    audio_analysis_statuses: Optional[List[str]] = None
    audio_feature_filters: Optional[dict[str, List[int]]] = None
    audio_feature_rule: Optional[dict] = None
    feedback_ratings: Optional[List[str]] = None
    
    sort_by: str = "title"
    sort_order: str = "asc"
    
    limit: Optional[int] = None
    offset: int = 0

@dataclass
class CollectionQuery:
    """Standardized search parameters for Collections."""
    archived: Optional[bool] = None
    source_type: Optional[str] = None
    name_contains: Optional[str] = None
    min_items: Optional[int] = None
    max_items: Optional[int] = None
