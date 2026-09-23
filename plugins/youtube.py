import logging
import json
import re
import urllib.request
import yt_dlp
from difflib import SequenceMatcher
from typing import Optional

from infrastructure.models import AcquisitionCandidate, Collection, MediaItem
from infrastructure.config import ConfigManager

logger = logging.getLogger(__name__)

YOUTUBE_IMPORTER_VERSION = "playlist-continuation-fallback-v2"


class QuietLogger:
    def debug(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


class YouTubeSourcePlugin:
    """A pure data fetcher for YouTube Collections and Media Items."""
    source_type = "youtube"
    provider = "youtube"
    
    def __init__(self, config: ConfigManager):
        self.config = config
        
        # Base options for fast metadata extraction (no downloading)
        self.base_opts = {
            'quiet': True,
            'no_warnings': True,
            'no_color': True,
            'logger': QuietLogger(),
            'ignoreerrors': True, # Skip deleted/private videos in playlists
        }

        # Inject browser cookies if configured
        browser = self.config.get("youtube.browser_cookies")
        if browser:
            self.base_opts['cookiesfrombrowser'] = (browser,)
            logger.info(f"YouTube Plugin initialized using {browser.capitalize()} cookies.")
        logger.info("YouTube importer version: %s", YOUTUBE_IMPORTER_VERSION)

    def fetch_collection(self, url: str, max_results: int | None = None) -> Optional[Collection]:
        """Extracts metadata from a YouTube Playlist, Channel, or Mix."""
        logger.info(f"Fetching YouTube collection from: {url}")
        
        opts = {**self.base_opts, 'extract_flat': True} 
        
        # --- THE FIX: Cap infinite Mixes (RD...) without capping regular Playlists (PL...) ---
        if 'list=RD' in url or 'start_radio' in url:
            # Defaults to 50 if you ever remove max_results from your config
            max_mix = max_results or self.config.get("youtube.max_results", 50) 
            opts['playlistend'] = max_mix
            logger.info(f"Detected endless YouTube Mix. Capping at {max_mix} tracks.")
        
        try:
            info = self._extract_info_with_cookie_fallback(url, opts)

            if not info or 'entries' not in info:
                logger.error("URL does not appear to be a valid YouTube collection.")
                return None

            entries = self._materialize_collection_entries(info, url, opts)

            playlist_name = info.get('title') or 'Unknown YouTube Collection'
            playlist_id = info.get('id') or url

            items = []
            for entry in entries:
                items.append(self._parse_entry(entry))

            return Collection(
                name=playlist_name,
                source_type='youtube',
                external_id=playlist_id,
                external_url=url,
                items=items
            )
            
        except Exception as e:
            logger.error("YouTube extraction failed: %s", self._clean_error(e))
            return None

    def fetch_radio_collection(self, seed_video_id: str, max_results: int) -> Optional[Collection]:
        seed_video_id = seed_video_id.strip()
        if not seed_video_id:
            return None
        radio_url = f"https://www.youtube.com/watch?v={seed_video_id}&list=RD{seed_video_id}&start_radio=1"
        return self.fetch_collection(radio_url, max_results=max_results)

    def fetch_item(self, url: str) -> Optional[MediaItem]:
        """Extracts metadata for a single YouTube video or track."""
        logger.info(f"Fetching YouTube item from: {url}")
        
        opts = {**self.base_opts, 'extract_flat': False, 'ignoreerrors': False}
        
        try:
            info = self._extract_info_with_cookie_fallback(url, opts)

            if not info:
                logger.warning("YouTube item extraction returned no metadata for: %s", url)
                return None

            if 'entries' in info:
                entries = [entry for entry in info['entries'] if entry]
                if not entries:
                    logger.warning("YouTube item extraction returned no playable entries for: %s", url)
                    return None
                info = entries[0]

            return self._parse_entry(info)
            
        except Exception as e:
            logger.error("YouTube extraction failed: %s", self._clean_error(e))
            return None

    def _materialize_collection_entries(self, info: dict, url: str, opts: dict) -> list[dict]:
        entries = [entry for entry in info.get("entries", []) if entry]
        expected_count = self._expected_playlist_count(info)

        if (
            expected_count
            and len(entries) < expected_count
            and "cookiesfrombrowser" in opts
            and not self._is_youtube_radio_url(url)
        ):
            retry_opts = dict(opts)
            browser = retry_opts.pop("cookiesfrombrowser", None)
            logger.warning(
                "YouTube playlist extraction returned %s of %s entries using browser cookies %r; retrying without cookies for %s",
                len(entries),
                expected_count,
                browser,
                url,
            )
            try:
                retry_info = self._extract_info_with_cookie_fallback(url, retry_opts)
                retry_entries = [entry for entry in (retry_info or {}).get("entries", []) if entry]
                if len(retry_entries) > len(entries):
                    logger.info(
                        "YouTube playlist retry without cookies returned %s entries for %s",
                        len(retry_entries),
                        url,
                    )
                    return retry_entries
            except Exception as exc:
                logger.warning(
                    "YouTube playlist retry without cookies failed for %s: %s",
                    url,
                    self._clean_error(exc),
                )

        if expected_count and len(entries) < expected_count and not self._is_youtube_radio_url(url):
            paged_entries = self._fetch_playlist_entry_pages(url, opts, expected_count)
            if len(paged_entries) > len(entries):
                logger.info(
                    "YouTube playlist paged extraction returned %s entries for %s",
                    len(paged_entries),
                    url,
                )
                return paged_entries

            continuation_entries = self._fetch_playlist_continuation_entries(url, entries, expected_count)
            if len(continuation_entries) > len(entries):
                logger.info(
                    "YouTube playlist continuation fallback returned %s entries for %s",
                    len(continuation_entries),
                    url,
                )
                return continuation_entries
            logger.warning(
                "YouTube playlist continuation fallback did not expand entries for %s: %s -> %s of %s",
                url,
                len(entries),
                len(continuation_entries),
                expected_count,
            )

        if expected_count and len(entries) < expected_count and not self._is_youtube_radio_url(url):
            logger.warning(
                "YouTube playlist extraction returned %s of %s advertised entries for %s",
                len(entries),
                expected_count,
                url,
            )

        return entries

    def _fetch_playlist_entry_pages(self, url: str, opts: dict, expected_count: int) -> list[dict]:
        page_size = 100
        fetched: list[dict] = []
        seen_ids: set[str] = set()

        for start in range(1, expected_count + 1, page_size):
            end = min(start + page_size - 1, expected_count)
            page_opts = dict(opts)
            page_opts.pop("cookiesfrombrowser", None)
            page_opts["playliststart"] = start
            page_opts["playlistend"] = end
            try:
                page_info = self._extract_info_with_cookie_fallback(url, page_opts)
            except Exception as exc:
                logger.warning(
                    "YouTube playlist page extraction failed for %s items %s-%s: %s",
                    url,
                    start,
                    end,
                    self._clean_error(exc),
                )
                break

            page_entries = [entry for entry in (page_info or {}).get("entries", []) if entry]
            if not page_entries:
                break

            added = 0
            for entry in page_entries:
                entry_id = self._entry_video_id(entry) or entry.get("url") or entry.get("webpage_url")
                dedupe_key = str(entry_id) if entry_id else f"{start}:{len(fetched)}"
                if dedupe_key in seen_ids:
                    continue
                seen_ids.add(dedupe_key)
                fetched.append(entry)
                added += 1

            if added == 0 or len(fetched) >= expected_count:
                break

        return fetched

    def _fetch_playlist_continuation_entries(self, url: str, base_entries: list[dict], expected_count: int) -> list[dict]:
        try:
            api_key, client_version, tokens = self._playlist_page_context(url)
        except Exception as exc:
            logger.warning(
                "YouTube playlist continuation setup failed for %s: %s",
                url,
                self._clean_error(exc),
            )
            return base_entries

        entries = list(base_entries)
        seen_ids = {
            entry_id
            for entry in entries
            if (entry_id := self._entry_video_id(entry))
        }

        logger.info(
            "YouTube playlist continuation fallback starting for %s: base=%s expected=%s tokens=%s",
            url,
            len(entries),
            expected_count,
            len(tokens),
        )

        token_queue = list(tokens)
        used_tokens: set[str] = set()
        while token_queue and len(entries) < expected_count:
            token = token_queue.pop(0)
            if not token or token in used_tokens:
                continue
            used_tokens.add(token)
            try:
                data = self._fetch_youtube_browse_continuation(api_key, client_version, token)
            except Exception as exc:
                logger.warning(
                    "YouTube playlist continuation request failed for %s: %s",
                    url,
                    self._clean_error(exc),
                )
                continue

            added = 0
            lockups = list(self._walk_key(data, "lockupViewModel"))
            for item in lockups:
                entry = self._entry_from_lockup_view_model(item)
                entry_id = entry and self._entry_video_id(entry)
                if not entry or not entry_id or entry_id in seen_ids:
                    continue
                seen_ids.add(entry_id)
                entries.append(entry)
                added += 1

            for continuation in self._walk_key(data, "continuationCommand"):
                next_token = self._continuation_token(continuation)
                if next_token and next_token not in used_tokens:
                    token_queue.append(next_token)

            if added == 0:
                logger.info(
                    "YouTube playlist continuation token added no new entries for %s: lockups=%s total=%s",
                    url,
                    len(lockups),
                    len(entries),
                )
                continue

            logger.info(
                "YouTube playlist continuation token added %s entries for %s: total=%s",
                added,
                url,
                len(entries),
            )

        return entries

    def _playlist_page_context(self, url: str) -> tuple[str, str, list[str]]:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=20) as response:
            html = response.read().decode("utf-8", "replace")

        api_key_match = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', html)
        client_version_match = re.search(r'"INNERTUBE_CLIENT_VERSION":"([^"]+)"', html)
        initial_data_match = re.search(r"var ytInitialData = (\{.*?\});</script>", html)
        if not api_key_match or not client_version_match or not initial_data_match:
            raise ValueError("YouTube playlist page did not expose Innertube context")

        initial_data = json.loads(initial_data_match.group(1))
        tokens = []
        for continuation in self._walk_key(initial_data, "continuationCommand"):
            token = self._continuation_token(continuation)
            if token and token not in tokens:
                tokens.append(token)
        if not tokens:
            raise ValueError("YouTube playlist page did not expose continuation tokens")

        return api_key_match.group(1), client_version_match.group(1), tokens

    def _fetch_youtube_browse_continuation(self, api_key: str, client_version: str, token: str) -> dict:
        body = json.dumps(
            {
                "context": {
                    "client": {
                        "clientName": "WEB",
                        "clientVersion": client_version,
                    }
                },
                "continuation": token,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"https://www.youtube.com/youtubei/v1/browse?key={api_key}",
            data=body,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8", "replace"))

    def _entry_from_lockup_view_model(self, item: dict) -> dict | None:
        video_id = item.get("contentId")
        metadata = item.get("metadata", {}).get("lockupMetadataViewModel", {})
        title = metadata.get("title", {}).get("content")
        if not video_id or not title:
            return None

        uploader = "Unknown Artist"
        metadata_rows = (
            metadata.get("metadata", {})
            .get("contentMetadataViewModel", {})
            .get("metadataRows", [])
        )
        if metadata_rows:
            parts = metadata_rows[0].get("metadataParts", [])
            if parts:
                uploader = parts[0].get("text", {}).get("content") or uploader

        duration = 0
        for text in self._walk_key(item.get("contentImage", {}), "text"):
            if isinstance(text, str) and re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", text):
                duration = self._duration_text_to_seconds(text)
                break

        return {
            "id": video_id,
            "title": title,
            "uploader": uploader,
            "duration": duration,
            "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
        }

    def _duration_text_to_seconds(self, value: str) -> int:
        parts = [int(part) for part in value.split(":")]
        total = 0
        for part in parts:
            total = (total * 60) + part
        return total

    def _walk_key(self, value, key: str):
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                if child_key == key:
                    yield child_value
                yield from self._walk_key(child_value, key)
        elif isinstance(value, list):
            for child_value in value:
                yield from self._walk_key(child_value, key)

    def _continuation_token(self, continuation: dict) -> str | None:
        if "token" in continuation:
            return continuation["token"]
        return (
            continuation.get("innertubeCommand", {})
            .get("continuationCommand", {})
            .get("token")
        )

    def _expected_playlist_count(self, info: dict) -> int | None:
        for key in ("playlist_count", "n_entries", "playlist_n_entries"):
            value = info.get(key)
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return None

    def _is_youtube_radio_url(self, url: str | None) -> bool:
        value = (url or "").lower()
        return "list=rd" in value or "start_radio" in value

    def _extract_info_with_cookie_fallback(self, url: str, opts: dict) -> dict | None:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:
            if "cookiesfrombrowser" not in opts:
                raise
            return self._extract_without_cookies_after_failure(url, opts, exc)

        if info or "cookiesfrombrowser" not in opts:
            return info

        return self._extract_without_cookies_after_failure(url, opts, None)

    def _extract_without_cookies_after_failure(self, url: str, opts: dict, exc: Exception | None) -> dict | None:
        fallback_opts = dict(opts)
        browser = fallback_opts.pop("cookiesfrombrowser", None)
        if exc:
            logger.warning(
                "YouTube extraction failed using browser cookies %r; retrying without cookies for %s: %s",
                browser,
                url,
                self._clean_error(exc),
            )
        else:
            logger.warning(
                "YouTube extraction returned no metadata using browser cookies %r; retrying without cookies for %s",
                browser,
                url,
            )
        try:
            with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                return ydl.extract_info(url, download=False)
        except Exception as fallback_exc:
            if opts.get("extract_flat"):
                raise
            logger.warning(
                "YouTube full metadata extraction failed without cookies; retrying flat metadata for %s: %s",
                url,
                self._clean_error(fallback_exc),
            )
            flat_opts = {
                **fallback_opts,
                "extract_flat": True,
                "ignoreerrors": True,
            }
            with yt_dlp.YoutubeDL(flat_opts) as ydl:
                return ydl.extract_info(url, download=False)

    def _clean_error(self, exc: Exception) -> str:
        return re.sub(r"\x1b\[[0-9;]*m", "", str(exc))

    def _parse_entry(self, entry: dict) -> MediaItem:
        """Converts yt-dlp metadata into our standard MediaItem contract."""
        yt_id = self._entry_video_id(entry)
        title = (
            entry.get('title')
            or entry.get('fulltitle')
            or entry.get('alt_title')
            or (f"YouTube Video {yt_id}" if yt_id else "Unknown YouTube Title")
        )
        artist = entry.get('uploader') or entry.get('channel') or entry.get('creator') or 'Unknown Artist'
        duration_sec = entry.get('duration')
        duration_ms = int(duration_sec * 1000) if duration_sec else 0
        external_url = entry.get("webpage_url")
        if not external_url and yt_id:
            external_url = f"https://www.youtube.com/watch?v={yt_id}"

        if not entry.get("title"):
            logger.warning(
                "YouTube entry missing title; using fallback title=%r id=%r url=%r",
                title,
                yt_id,
                external_url,
            )

        return MediaItem(
            title=title,
            artist=artist,
            duration_ms=duration_ms,
            media_type='music',
            external_id=yt_id,
            external_source=self.source_type,
            external_url=external_url,
            acquisition_info={"youtube_id": yt_id} if yt_id else {}
        )

    def _entry_video_id(self, entry: dict) -> str | None:
        yt_id = entry.get("id")
        if yt_id:
            return str(yt_id)
        url = entry.get("url") or entry.get("webpage_url")
        if not url:
            return None
        match = re.search(r"(?:v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{6,})", str(url))
        if match:
            return match.group(1)
        if re.fullmatch(r"[A-Za-z0-9_-]{6,}", str(url)):
            return str(url)
        return None

    def resolve(self, item: MediaItem) -> list[AcquisitionCandidate]:
        """Finds downloadable YouTube candidates for a metadata-only media item."""
        query = self._search_query(item)
        opts = {
            **self.base_opts,
            'format': 'bestaudio/best',
            'extract_flat': True,
            'logger': QuietLogger(),
        }
        max_results = self.config.get("youtube.max_results", 10)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                result = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
        except Exception as exc:
            logger.debug(f"YouTube resolve failed for {item.title}: {exc}")
            return []

        entries = result.get("entries", []) if result else []
        candidates = [
            self._candidate_from_entry(item, entry)
            for entry in entries
            if entry
        ]
        candidates = [candidate for candidate in candidates if candidate.external_id]
        candidates.sort(key=lambda candidate: candidate.confidence, reverse=True)
        return candidates

    def _search_query(self, item: MediaItem) -> str:
        if item.media_type == "podcast":
            return f"{item.title} {item.artist} podcast"
        artist = item.artist or ""
        title = item.title or ""
        album = f" {item.album}" if item.album else ""
        return f"{artist} - {title}{album} audio"

    def _candidate_from_entry(self, item: MediaItem, entry: dict) -> AcquisitionCandidate:
        yt_id = entry.get("id") or ""
        yt_title = entry.get("title") or ""
        yt_uploader = entry.get("uploader") or entry.get("channel") or ""
        duration = int(entry.get("duration") or 0)
        score_data = self._score_candidate(item, yt_title, yt_uploader, duration)
        return AcquisitionCandidate(
            provider=self.provider,
            external_id=yt_id,
            title=yt_title,
            duration_seconds=duration,
            confidence=score_data["final_score"],
            acquisition_info={"youtube_id": yt_id},
            details={
                **score_data,
                "uploader": yt_uploader,
                "raw_title": yt_title,
            },
        )

    def _score_candidate(self, item: MediaItem, yt_title: str, yt_uploader: str, yt_duration_sec: int) -> dict:
        normalized_title = self._normalize(item.title)
        normalized_artist = self._normalize(item.artist)
        normalized_album = self._normalize(item.album or "")
        normalized_yt_title = self._normalize(yt_title)
        normalized_uploader = self._normalize(yt_uploader)

        title_sim = max(
            self._similar(normalized_title, normalized_yt_title),
            self._similar(f"{normalized_artist} {normalized_title}".strip(), normalized_yt_title),
            self._similar(f"{normalized_title} {normalized_artist}".strip(), normalized_yt_title),
        )
        artist_sim = max(
            self._similar(normalized_artist, normalized_uploader),
            0.85 if normalized_artist and normalized_artist in normalized_yt_title else 0.0,
        )
        album_sim = self._similar(normalized_album, normalized_yt_title) if normalized_album else 0.0

        source_duration_sec = (item.duration_ms or 0) / 1000.0
        if source_duration_sec > 0 and yt_duration_sec > 0:
            duration_delta = abs(source_duration_sec - yt_duration_sec)
            duration_sim = max(0.0, 1.0 - (duration_delta / max(source_duration_sec, 1.0)))
        else:
            duration_sim = 0.55

        weights = self.config.get("matching.weights", {"title": 0.45, "artist": 0.35, "duration": 0.20})
        w_title = float(weights.get("title", 0.45))
        w_artist = float(weights.get("artist", 0.35))
        w_duration = float(weights.get("duration", 0.20))
        total = w_title + w_artist + w_duration or 1.0

        base_score = (
            title_sim * (w_title / total)
            + artist_sim * (w_artist / total)
            + duration_sim * (w_duration / total)
        )
        if album_sim:
            base_score = min(1.0, base_score + (album_sim * 0.04))

        modifier = self._youtube_modifier(yt_title, yt_uploader)
        final_score = max(0.0, min(1.0, base_score + modifier))
        return {
            "final_score": final_score,
            "base_score": base_score,
            "modifier": modifier,
            "title_similarity": title_sim,
            "artist_similarity": artist_sim,
            "duration_similarity": duration_sim,
        }

    def _youtube_modifier(self, title: str, uploader: str) -> float:
        text = f"{title} {uploader}"
        modifier = 0.0

        penalties = self.config.get("matching.penalties", {})
        if isinstance(penalties, dict):
            for term, weight in penalties.items():
                if re.search(rf"\b{re.escape(str(term))}\b", text, flags=re.IGNORECASE):
                    modifier -= float(weight)

        lower = text.lower()
        positive_terms = ("official audio", "official video", "provided to youtube", "topic")
        if any(term in lower for term in positive_terms):
            modifier += 0.08
        noisy_terms = ("reaction", "tutorial", "cover by", "nightcore", "sped up", "slowed", "8d audio")
        if any(term in lower for term in noisy_terms):
            modifier -= 0.15
        return modifier

    def _normalize(self, text: str | None) -> str:
        text = (text or "").lower()
        text = re.sub(r"\([^)]*(official|audio|video|lyrics?|visualizer|remaster(ed)?)[^)]*\)", " ", text)
        text = re.sub(r"\[[^\]]*(official|audio|video|lyrics?|visualizer|remaster(ed)?)[^\]]*\]", " ", text)
        text = re.sub(r"[\&,/\\]+", " ", text)
        text = re.sub(r"[^a-z0-9\s]", "", text)
        return " ".join(text.split())

    def _similar(self, a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0
        if a in b or b in a:
            shorter = min(len(a), len(b))
            longer = max(len(a), len(b))
            return max(SequenceMatcher(None, a, b).ratio(), shorter / longer)
        return SequenceMatcher(None, a, b).ratio()
