import hashlib
import logging
import shutil
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from infrastructure.models import Collection, MediaItem

if TYPE_CHECKING:
    from infrastructure.config import ConfigManager

logger = logging.getLogger(__name__)


SUPPORTED_AUDIO_EXTENSIONS = {".mp3"}


class LocalFolderSourcePlugin:
    source_type = "local_folder"

    def __init__(self, config: "ConfigManager"):
        self.config = config

    def fetch_collection(self, path: str) -> Optional[Collection]:
        root = Path(path).expanduser()
        if not root.exists() or not root.is_dir():
            raise FileNotFoundError(f"Local folder does not exist: {root}")

        files = self._audio_files_recursive(root)
        if not files:
            return None

        items = [self._item_from_file(file) for file in files]
        return Collection(
            name=root.name or str(root),
            source_type=self.source_type,
            external_id=str(root.resolve()),
            external_url=str(root.resolve()),
            items=items,
        )

    def fetch_immediate_subfolder_collections(self, path: str) -> list[Collection]:
        root = Path(path).expanduser()
        if not root.exists() or not root.is_dir():
            raise FileNotFoundError(f"Local folder does not exist: {root}")
        collections: list[Collection] = []
        for subfolder in self._immediate_subfolders(root):
            collection = self.fetch_collection(str(subfolder))
            if collection:
                collections.append(collection)
        return collections

    def preview_folder(self, path: str) -> dict:
        root = Path(path).expanduser()
        if not root.exists() or not root.is_dir():
            raise FileNotFoundError(f"Local folder does not exist: {root}")

        top_level_files = self._audio_files_top_level(root)
        subfolders = []
        for subfolder in self._immediate_subfolders(root):
            files = self._audio_files_recursive(subfolder)
            if files:
                subfolders.append(
                    {
                        "name": subfolder.name,
                        "path": str(subfolder.resolve()),
                        "track_count": len(files),
                    }
                )
        subfolder_track_count = sum(item["track_count"] for item in subfolders)
        return {
            "path": str(root.resolve()),
            "name": root.name or str(root.resolve()),
            "top_level_count": len(top_level_files),
            "subfolder_track_count": subfolder_track_count,
            "total_count": len(top_level_files) + subfolder_track_count,
            "subfolder_collection_count": len(subfolders),
            "subfolders": subfolders,
        }

    def fetch_item(self, path: str) -> Optional[MediaItem]:
        file = Path(path).expanduser()
        if not file.exists() or not file.is_file():
            raise FileNotFoundError(f"Local file does not exist: {file}")
        if file.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
            raise ValueError(f"Unsupported audio file: {file}")
        return self._item_from_file(file)

    def _item_from_file(self, file: Path) -> MediaItem:
        file = file.resolve()
        file_hash = sha256_file(file)
        metadata = read_audio_metadata(file)
        title = metadata.get("title") or file.stem
        artist = metadata.get("artist") or file.parent.name or "Unknown Artist"
        album = metadata.get("album")
        duration_ms = metadata.get("duration_ms") or 0
        return MediaItem(
            title=title,
            artist=artist,
            album=album,
            duration_ms=duration_ms,
            media_type="music",
            external_id=file_hash,
            external_source=self.source_type,
            external_url=str(file),
            acquisition_info={
                "provider": LocalFileAcquisitionPlugin.provider,
                "local_path": str(file),
                "sha256": file_hash,
                "source_mtime": file.stat().st_mtime,
            },
        )

    def _audio_files_recursive(self, root: Path) -> list[Path]:
        return sorted(
            file
            for file in root.rglob("*")
            if file.is_file() and file.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS
        )

    def _audio_files_top_level(self, root: Path) -> list[Path]:
        return sorted(
            file
            for file in root.iterdir()
            if file.is_file() and file.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS
        )

    def _immediate_subfolders(self, root: Path) -> list[Path]:
        return sorted(child for child in root.iterdir() if child.is_dir())


class LocalFileAcquisitionPlugin:
    provider = "local_file"

    def __init__(self, config: "ConfigManager"):
        self.config = config

    def can_acquire(self, acquisition_info: dict) -> bool:
        return acquisition_info.get("provider") == self.provider or bool(acquisition_info.get("local_path"))

    def acquire(self, item: dict, target_path: Path, job_context=None) -> bool:
        acquisition_info = item.get("acquisition_info") or {}
        source = Path(str(acquisition_info.get("local_path") or "")).expanduser()
        if not source.exists() or not source.is_file():
            logger.error("Local acquisition source is missing for '%s': %s", item.get("title"), source)
            return False

        expected_hash = str(acquisition_info.get("sha256") or "")
        if expected_hash:
            actual_hash = sha256_file(source)
            if actual_hash != expected_hash:
                logger.error("Local source hash changed for '%s': %s", item.get("title"), source)
                return False

        target_path.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() == target_path.resolve():
            return True
        shutil.copy2(source, target_path)
        return True


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_audio_metadata(path: Path) -> dict:
    try:
        import mutagen
    except ImportError:
        return {}

    try:
        audio = mutagen.File(path, easy=True)
    except Exception as exc:
        logger.debug("Failed to read metadata from %s: %s", path, exc)
        return {}
    if not audio:
        return {}

    def first(key: str) -> str | None:
        value = audio.get(key)
        if isinstance(value, list) and value:
            return str(value[0]).strip() or None
        if value:
            return str(value).strip() or None
        return None

    duration_ms = 0
    info = getattr(audio, "info", None)
    if info and getattr(info, "length", None):
        duration_ms = int(float(info.length) * 1000)

    return {
        "title": first("title"),
        "artist": first("artist"),
        "album": first("album"),
        "duration_ms": duration_ms,
    }
