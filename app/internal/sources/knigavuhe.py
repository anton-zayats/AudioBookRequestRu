"""knigavuhe.org direct-source provider.

knigavuhe serves server-rendered search result cards and embeds each book's full
audio playlist as JSON inside an inline ``new BookPlayer(<book_id>, [...])``
script blob. See ``spikes/T0.3-knigavuhe.md`` for the parsing strategy.

Audio URLs are rotated roughly every 2.5 days (``player_data.url_refresh``), so
this provider intentionally re-derives them from a fresh book-page fetch on
every search and never caches them.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast, override
from urllib.parse import quote

from selectolax.lexbor import LexborHTMLParser

from app.internal.indexers.abstract import SessionContainer
from app.internal.indexers.configuration import (
    Configurations,
    IndexerConfiguration,
    indexer_configuration_cache,
)
from app.internal.models import (
    Audiobook,
    BookMetadata,
    DirectSource,
    ManualBookRequest,
)
from app.internal.sources.base import DirectSourceProvider
from app.util.connection import USER_AGENT
from app.util.log import logger

DEFAULT_BASE_URL = "https://knigavuhe.org"
DEFAULT_MAX_CANDIDATES = 3

ENABLED_KEY = "knigavuhe_enabled"
BASE_URL_KEY = "knigavuhe_base_url"
MAX_CANDIDATES_KEY = "knigavuhe_max_candidates"

# Stable synthetic per-provider indexer id. Prowlarr indexers use their own id
# space; direct providers get their own namespace starting at 1000.
KNGAVUHE_INDEXER_ID = 1000

_PLACEHOLDER_DATE = datetime(1970, 1, 1, tzinfo=UTC)
_BOOK_PLAYER_ID_RE = re.compile(r"new BookPlayer\(\s*(\d+)")


class KnigavuheConfigurations(Configurations):
    knigavuhe_enabled: IndexerConfiguration[bool] = IndexerConfiguration(
        display_name="Enabled",
        default=True,
        type_=bool,
    )
    knigavuhe_base_url: IndexerConfiguration[str] = IndexerConfiguration(
        display_name="Base URL",
        default=DEFAULT_BASE_URL,
        type_=str,
    )
    knigavuhe_max_candidates: IndexerConfiguration[int] = IndexerConfiguration(
        display_name="Max candidates",
        default=DEFAULT_MAX_CANDIDATES,
        type_=int,
    )


@dataclass(frozen=True)
class KnigavuheBook:
    """Minimal metadata for one search result card."""

    slug: str
    title: str
    author: str
    readers: list[str]


def parse_search_cards(html: str, max_candidates: int) -> list[KnigavuheBook]:
    """Parse server-rendered ``bookkitem`` cards up to ``max_candidates``."""
    parser = LexborHTMLParser(html)
    books: list[KnigavuheBook] = []
    for card in parser.css("div.bookkitem"):
        if len(books) >= max_candidates:
            break

        cover = card.css_first("a.bookkitem_cover")
        if cover is None:
            continue
        href = (cover.attributes.get("href") or "").strip("/")
        slug = href.removeprefix("book/")
        if not slug:
            continue

        # The cover ``alt`` carries the clean title (the title link nests
        # highlight <span>s, which selectolax concatenates without separators).
        title = ""
        img = card.css_first("img.bookkitem_cover_img")
        if img is not None:
            title = (img.attributes.get("alt") or "").strip()
        if not title:
            title_node = card.css_first("div.bookkitem_name a.bookkitem_name")
            if title_node is not None:
                title = title_node.text(strip=True)

        author_node = card.css_first('a[href^="/author/"]')
        author = author_node.text(strip=True) if author_node is not None else ""

        readers = [
            node.text(strip=True)
            for node in card.css('a[href^="/reader/"]')
            if node.text(strip=True)
        ]

        books.append(
            KnigavuheBook(
                slug=slug,
                title=title,
                author=author,
                readers=readers,
            )
        )
    return books


def extract_playlist(html: str) -> tuple[int, list[dict[str, object]]]:
    """Parse the inline ``new BookPlayer(<book_id>, [...])`` JSON payload.

    The array is valid JSON (URLs are escaped as ``https:\\/\\/...``, which
    :mod:`json` accepts), so we locate it with a small bracket matcher and hand
    it to ``json.loads`` — no HTML/JS parser needed.
    """
    start = html.find("new BookPlayer(")
    if start == -1:
        raise RuntimeError("BookPlayer payload not found on book page")
    array_start = html.find("[", start)
    if array_start == -1:
        raise RuntimeError("BookPlayer playlist array not found")

    book_id_match = _BOOK_PLAYER_ID_RE.search(html[start:])
    if book_id_match is None:
        raise RuntimeError("BookPlayer book id not found")
    book_id = int(book_id_match.group(1))

    depth = 0
    in_str = False
    escaped = False
    end = -1
    for pos in range(array_start, len(html)):
        char = html[pos]
        if in_str:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_str = False
        elif char == '"':
            in_str = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                end = pos
                break
    if end == -1:
        raise RuntimeError("unterminated BookPlayer playlist array")

    raw = cast(object, json.loads(html[array_start : end + 1]))
    if not isinstance(raw, list):
        raise TypeError(f"expected a list, got {raw.__class__.__name__}")
    return book_id, cast(list[dict[str, object]], raw)


def item_urls(items: list[dict[str, object]]) -> list[str]:
    """Collect the audio URLs from playlist items, skipping malformed entries."""
    urls: list[str] = []
    for item in items:
        url = item.get("url")
        if isinstance(url, str) and url:
            urls.append(url)
    return urls


def build_source(
    card: KnigavuheBook,
    book_url: str,
    urls: list[str],
) -> DirectSource:
    """Build a :class:`DirectSource` from a search card and its audio URLs."""
    readers = [reader for reader in card.readers if reader]
    title = card.title or "Unknown"
    display_title = f"{title} — {', '.join(readers)}" if readers else title

    return DirectSource(
        guid=book_url,
        indexer_id=KNGAVUHE_INDEXER_ID,
        indexer="knigavuhe",
        title=display_title,
        size=0,
        publish_date=_PLACEHOLDER_DATE,
        info_url=book_url,
        indexer_flags=[],
        protocol="direct",
        download_urls=urls,
        filetype="mp3",
        book_metadata=BookMetadata(
            title=title,
            authors=[card.author] if card.author else [],
            narrators=readers,
            filetype="mp3",
        ),
    )


class KnigavuheProvider(DirectSourceProvider):
    name: str = "knigavuhe"

    @override
    def get_configurations(self, container: SessionContainer) -> Configurations:
        _ = container
        return KnigavuheConfigurations()

    def _base_url(self, container: SessionContainer) -> str:
        base = indexer_configuration_cache.get(container.session, BASE_URL_KEY)
        return (base or DEFAULT_BASE_URL).rstrip("/")

    def _max_candidates(self, container: SessionContainer) -> int:
        value = indexer_configuration_cache.get_int(
            container.session, MAX_CANDIDATES_KEY, DEFAULT_MAX_CANDIDATES
        )
        if value < 1:
            return DEFAULT_MAX_CANDIDATES
        return value

    @override
    async def search(
        self,
        book: Audiobook | ManualBookRequest,
        container: SessionContainer,
    ) -> list[DirectSource]:
        configurations = self.get_configurations(container)
        if not self.is_enabled(container, configurations):
            return []

        title = book.title.strip()
        if not title:
            return []

        base_url = self._base_url(container)
        if not base_url:
            return []

        authors = book.authors or []
        query = f"{title} {authors[0]}" if authors else title
        search_url = f"{base_url}/search/?q={quote(query)}"

        headers = {"User-Agent": USER_AGENT, "Accept-Language": "ru"}
        try:
            async with container.client_session.get(
                search_url, headers=headers
            ) as response:
                if not response.ok:
                    logger.warning(
                        "knigavuhe search failed",
                        url=search_url,
                        status=response.status,
                    )
                    return []
                html = await response.text()
        except Exception as exc:
            logger.error("knigavuhe search failed", url=search_url, error=str(exc))
            return []

        candidates = parse_search_cards(html, self._max_candidates(container))
        logger.info(
            "knigavuhe search candidates found",
            query=query,
            count=len(candidates),
        )

        sources: list[DirectSource] = []
        for index, card in enumerate(candidates):
            if index > 0:
                await asyncio.sleep(1.0)

            book_url = f"{base_url}/book/{card.slug}/"
            try:
                async with container.client_session.get(
                    book_url,
                    headers={**headers, "Referer": f"{base_url}/search/"},
                ) as response:
                    if not response.ok:
                        logger.warning(
                            "knigavuhe book page failed",
                            slug=card.slug,
                            status=response.status,
                        )
                        continue
                    page_html = await response.text()

                _, items = extract_playlist(page_html)
                urls = item_urls(items)
                if not urls:
                    logger.warning("knigavuhe book has no playlist", slug=card.slug)
                    continue

                sources.append(build_source(card, book_url, urls))
            except Exception as exc:
                logger.error(
                    "knigavuhe candidate failed",
                    slug=card.slug,
                    error=str(exc),
                )

        logger.info("knigavuhe sources found", count=len(sources))
        return sources
