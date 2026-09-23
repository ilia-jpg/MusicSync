import logging
import re
from html import unescape
from typing import Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from infrastructure.config import ConfigManager
from infrastructure.models import Collection, MediaItem

logger = logging.getLogger(__name__)


class BillboardSourcePlugin:
    """Fetches public Billboard chart pages as MusicSync collections."""
    source_type = "billboard"

    BASE_URL = "https://www.billboard.com/charts"

    def __init__(self, config: ConfigManager):
        self.config = config

    def fetch_collection(self, url: str) -> Optional[Collection]:
        chart_url, chart_id = self._normalize_url(url)
        try:
            request = Request(chart_url, headers={"User-Agent": "MusicSync/1.0"})
            with urlopen(request, timeout=20) as response:
                html = response.read().decode("utf-8", errors="replace")
        except (OSError, URLError) as exc:
            logger.error(f"Failed to fetch Billboard chart: {exc}")
            return None

        items = self._parse_chart_items(html)
        if not items:
            logger.error("Billboard chart did not contain parseable songs.")
            return None

        return Collection(
            name=f"Billboard {chart_id.replace('-', ' ').title()}",
            source_type="billboard",
            external_id=chart_id,
            external_url=chart_url,
            items=items,
        )

    def fetch_item(self, url: str) -> Optional[MediaItem]:
        return None

    def _normalize_url(self, value: str) -> tuple[str, str]:
        value = value.strip()
        if value.startswith("billboard:"):
            chart_id = value.split(":", 1)[1].strip("/") or "hot-100"
            return f"{self.BASE_URL}/{chart_id}/", chart_id
        if "billboard.com/charts/" in value:
            match = re.search(r"billboard\.com/charts/([^/?#]+)/?", value)
            chart_id = match.group(1) if match else "hot-100"
            return value, chart_id
        chart_id = value.strip("/") or "hot-100"
        return f"{self.BASE_URL}/{chart_id}/", chart_id

    def _parse_chart_items(self, html: str) -> list[MediaItem]:
        items: list[MediaItem] = []
        blocks = re.findall(r'<div class="o-chart-results-list-row-container.*?</div>\s*</div>\s*</div>', html, flags=re.DOTALL)
        if not blocks:
            blocks = html.split("o-chart-results-list-row-container")

        for block in blocks:
            title_match = re.search(r'<h3[^>]*id="title-of-a-story"[^>]*>(.*?)</h3>', block, flags=re.DOTALL)
            if not title_match:
                continue
            title = self._clean_html(title_match.group(1))

            artist = self._extract_artist(block[title_match.end():])

            if title and not any(item.title == title and item.artist == artist for item in items):
                items.append(MediaItem(title=title, artist=artist, media_type="music"))

        return items

    def _extract_artist(self, block_after_title: str) -> str:
        labels = re.findall(
            r'<span[^>]*class="[^"]*c-label[^"]*"[^>]*>\s*(.*?)\s*</span>',
            block_after_title,
            flags=re.DOTALL,
        )
        for label_html in labels:
            label = self._clean_html(label_html)
            if self._looks_like_artist(label):
                return label
        return "Unknown Artist"

    def _looks_like_artist(self, value: str) -> bool:
        if not value:
            return False
        normalized = value.strip().upper()
        if normalized in {"-", "NEW", "RE-ENTRY", "REENTRY", "GREATEST GAINER"}:
            return False
        if re.fullmatch(r"\d+", normalized):
            return False
        if re.fullmatch(r"\d+\s+WKS?", normalized):
            return False
        return True

    def _clean_html(self, value: str) -> str:
        value = re.sub(r"<[^>]+>", " ", value)
        value = unescape(value)
        return " ".join(value.split())
