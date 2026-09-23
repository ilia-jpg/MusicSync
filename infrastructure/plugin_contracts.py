from typing import Optional, Protocol

from infrastructure.models import AcquisitionCandidate, Collection, MediaItem


class DiscoveryPlugin(Protocol):
    source_type: str

    def fetch_collection(self, url: str) -> Optional[Collection]:
        ...

    def fetch_item(self, url: str) -> Optional[MediaItem]:
        ...


class ResolverPlugin(Protocol):
    provider: str

    def resolve(self, item: MediaItem) -> list[AcquisitionCandidate]:
        ...


class AcquisitionPlugin(Protocol):
    provider: str

    def can_acquire(self, acquisition_info: dict) -> bool:
        ...

    def acquire(self, item: dict, target_path, job_context=None) -> bool:
        ...
