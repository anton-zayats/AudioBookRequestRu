"""livelib.ru metadata provider.

Search runs against the legacy PHP search page (HTTP/1.1 friendly) while book
detail pages are served by livelib's Next.js frontend behind DDoS-Guard, which
rejects plain HTTP/1.1 clients with HTTP 403. Those pages therefore require an
HTTP/2 client, provided here via ``httpx[http2]``. See spikes/T0.2-livelib.md.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TypedDict, cast, override
from urllib.parse import quote_plus

import httpx
from selectolax.lexbor import LexborHTMLParser

from app.internal.metadata.base import MetadataProvider
from app.internal.models import Audiobook
from app.util.log import logger

BASE = "https://www.livelib.ru"
SOURCE = "livelib"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36 "
    "AudioBookRequestRu/0.2 (metadata provider; polite; single-request)"
)

_PLACEHOLDER_DATE = datetime(1970, 1, 1, tzinfo=UTC)
_URL_ID_RE = re.compile(r"/(?:book|work)/(\d+)")
_EDITION_LINK_RE = re.compile(r'href="/book/(\d+)-([a-z0-9-]+)"')
_COVER_RE = re.compile(r"background:url\(([^)]+)\)")
_JSONLD_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.DOTALL
)

CATEGORY_TERMS: dict[str, list[str]] = {
    "художественная литература": ["роман", "проза", "классика"],
    "фантастика и фэнтези": ["фантастика", "фэнтези"],
    "детективы": ["детектив", "триллер"],
    "биографии и история": ["биография", "история", "мемуары"],
    "психология и саморазвитие": ["психология", "саморазвитие"],
    "детям": ["сказки", "детская литература"],
}
_CATEGORY_LIMIT = 12
_POLITE_SLEEP = 0.5


class SearchResult(TypedDict):
    work_id: str | None
    title: str | None
    author: str | None
    cover: str | None
    genres: list[str]
    edition_url: str | None


class SimilarBook(TypedDict):
    title: str | None
    url: str | None


class DetailData(TypedDict):
    title: str | None
    url: str | None
    cover: str | None
    genres: list[str]
    year: str | None
    author: str | None
    similar: list[SimilarBook]


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    items = cast(list[object], value)
    return [item for item in items if isinstance(item, str)]


def parse_search_results(html: str) -> list[SearchResult]:
    """Parse /find/books/<query> result cards into work-level records."""
    parser = LexborHTMLParser(html)
    results: list[SearchResult] = []
    for card in parser.css("#objects-block .object-wrapper.object-edition"):
        work_id: str | None = None
        book_id_node = card.css_first("[data-book_id]")
        if book_id_node is not None:
            work_id = book_id_node.attributes.get("data-book_id")
        if not work_id:
            match = re.search(r"ub-book-(\d+)", card.html or "")
            work_id = match.group(1) if match else None

        title_node = card.css_first(".brow-title a.title")
        title = title_node.text(strip=True) if title_node is not None else None

        author_node = card.css_first('a.description[href^="/author/"]')
        author = author_node.text(strip=True) if author_node is not None else None

        cover: str | None = None
        cover_node = card.css_first("span.object-cover")
        if cover_node is not None:
            match = _COVER_RE.search(cover_node.attributes.get("style") or "")
            cover = match.group(1) if match else None

        genres = [
            node.text(strip=True)
            for node in card.css(".brow-genres a.label-genre")
            if node.text(strip=True)
        ]

        link = card.attributes.get("data-link")
        results.append(
            SearchResult(
                work_id=work_id,
                title=title,
                author=author,
                cover=cover,
                genres=genres,
                edition_url=f"{BASE}{link}" if link else None,
            )
        )
    return results


def parse_work_editions(html: str) -> list[dict[str, str]]:
    """Parse /book/<work_id>/editions into the first-page edition links."""
    seen: set[str] = set()
    editions: list[dict[str, str]] = []
    for match in _EDITION_LINK_RE.finditer(html):
        edition_id, slug = match.group(1), match.group(2)
        if edition_id in seen:
            continue
        seen.add(edition_id)
        editions.append(
            {
                "edition_id": edition_id,
                "slug": slug,
                "url": f"{BASE}/book/{edition_id}-{slug}",
            }
        )
    return editions


def parse_detail_jsonld(html: str) -> DetailData:
    """Extract the schema.org ``Book`` node and its ``ItemList`` (similar)."""
    detail = DetailData(
        title=None,
        url=None,
        cover=None,
        genres=[],
        year=None,
        author=None,
        similar=[],
    )

    for match in _JSONLD_RE.finditer(html):
        block = match.group(1)
        data = cast(object, json.loads(block))
        if not isinstance(data, dict):
            continue
        graph = cast(dict[str, object], data).get("@graph")
        nodes: list[object]
        if isinstance(graph, list):
            nodes = cast(list[object], graph)
        else:
            nodes = [data]

        for node in nodes:
            if not isinstance(node, dict):
                continue
            item = cast(dict[str, object], node)
            ntype = item.get("@type")
            if ntype == "Book":
                detail["title"] = _as_str(item.get("name"))
                detail["url"] = _as_str(item.get("url"))
                image = item.get("image")
                if isinstance(image, dict):
                    detail["cover"] = _as_str(cast(dict[str, object], image).get("url"))
                detail["genres"] = _as_str_list(item.get("genre"))
                date = item.get("datePublished")
                if isinstance(date, str) and date:
                    detail["year"] = date[:4]
                author = item.get("author")
                if isinstance(author, dict):
                    detail["author"] = _as_str(
                        cast(dict[str, object], author).get("name")
                    )
            elif ntype == "ItemList" and not detail["similar"]:
                elements = item.get("itemListElement")
                if not isinstance(elements, list):
                    continue
                for element in cast(list[object], elements):
                    if not isinstance(element, dict):
                        continue
                    el = cast(dict[str, object], element)
                    entry = el.get("item")
                    name = el.get("name")
                    url = el.get("url")
                    if isinstance(entry, dict):
                        entry_item = cast(dict[str, object], entry)
                        name = entry_item.get("name") or name
                        url = entry_item.get("url") or url
                    detail["similar"].append(
                        SimilarBook(title=_as_str(name), url=_as_str(url))
                    )
    return detail


def parse_autocomplete(html: str) -> list[str]:
    """Best-effort pull of title-ish strings from the autocomplete HTML."""
    parser = LexborHTMLParser(html)
    titles = [node.text(strip=True) for node in parser.css("a")]
    if not any(titles):
        titles = [node.text(strip=True) for node in parser.css("li")]

    seen: set[str] = set()
    result: list[str] = []
    for title in titles:
        if title and title not in seen:
            seen.add(title)
            result.append(title)
    return result[:8]


def external_id_from_url(url: str | None) -> str:
    if not url:
        return ""
    match = _URL_ID_RE.search(url)
    return match.group(1) if match else url


class LivelibProvider(MetadataProvider):
    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            http2=True,
            follow_redirects=True,
            timeout=httpx.Timeout(30.0),
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "ru-RU,ru;q=0.9",
            },
        )

    @asynccontextmanager
    async def _http_client(self) -> AsyncIterator[httpx.AsyncClient]:
        async with self._client() as client:
            yield client

    async def _get(self, url: str) -> str:
        async with self._http_client() as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text

    async def _post(self, url: str, data: dict[str, str]) -> str:
        async with self._http_client() as client:
            response = await client.post(
                url,
                data=data,
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": f"{BASE}/",
                },
            )
            response.raise_for_status()
            return response.text

    @override
    async def search(
        self,
        query: str,
        num_results: int = 20,
        page: int = 0,
    ) -> list[Audiobook]:
        query = query.strip()
        if not query:
            return []
        path = f"/find/books/{quote_plus(query)}"
        if page > 0:
            path += f"/~{page + 1}"
        try:
            html = await self._get(f"{BASE}{path}")
        except Exception as exc:
            logger.error(
                "livelib search failed", query=query, page=page, error=str(exc)
            )
            return []

        books: list[Audiobook] = []
        seen: set[str] = set()
        for item in parse_search_results(html):
            work_id = item["work_id"]
            if not work_id or work_id in seen:
                continue
            seen.add(work_id)
            title = item["title"] or ""
            author = item["author"]
            books.append(
                Audiobook(
                    source=SOURCE,
                    external_id=work_id,
                    url=item["edition_url"],
                    title=title,
                    subtitle=None,
                    authors=[author] if author else [],
                    narrators=[],
                    cover_image=item["cover"],
                    release_date=_PLACEHOLDER_DATE,
                    runtime_length_min=0,
                )
            )
            if len(books) >= num_results:
                break
        return books

    @override
    async def get_book(self, external_id: str) -> Audiobook | None:
        try:
            editions_html = await self._get(f"{BASE}/book/{external_id}/editions")
        except Exception as exc:
            logger.error(
                "livelib editions fetch failed",
                external_id=external_id,
                error=str(exc),
            )
            return None

        editions = parse_work_editions(editions_html)
        if not editions:
            return None

        try:
            await asyncio.sleep(_POLITE_SLEEP)
            detail_html = await self._get(editions[0]["url"])
        except Exception as exc:
            logger.error(
                "livelib detail fetch failed",
                external_id=external_id,
                url=editions[0]["url"],
                error=str(exc),
            )
            return None

        data = parse_detail_jsonld(detail_html)
        title = data["title"]
        if not title:
            return None

        release_date = _PLACEHOLDER_DATE
        year = data["year"]
        if year:
            try:
                release_date = datetime(int(year), 1, 1, tzinfo=UTC)
            except ValueError:
                release_date = _PLACEHOLDER_DATE

        author = data["author"]
        return Audiobook(
            source=SOURCE,
            external_id=external_id,
            url=data["url"] or editions[0]["url"],
            title=title,
            subtitle=None,
            authors=[author] if author else [],
            narrators=[],
            cover_image=data["cover"],
            release_date=release_date,
            runtime_length_min=0,
        )

    @override
    async def suggestions(self, query: str) -> list[str]:
        query = query.strip()
        if not query:
            return []
        try:
            body = await self._post(
                f"{BASE}/main/search",
                {
                    "text": query,
                    "object_alias": "booksauthors",
                    "result_element": "x",
                    "width": "400",
                    "start": "0",
                    "limit": "5",
                    "is_new_design": "0",
                },
            )
            if body.lstrip().startswith("{"):
                payload = cast(object, json.loads(body))
                if isinstance(payload, dict):
                    content = cast(dict[str, object], payload).get("content")
                    suggestions = (
                        parse_autocomplete(content) if isinstance(content, str) else []
                    )
                else:
                    suggestions = []
            else:
                suggestions = parse_autocomplete(body)
        except Exception as exc:
            logger.debug(
                "livelib autocomplete failed, falling back to search",
                query=query,
                error=str(exc),
            )
            suggestions = []

        if not suggestions:
            try:
                books = await self.search(query, num_results=5)
                suggestions = [book.title for book in books if book.title]
            except Exception as exc:
                logger.debug("livelib suggestions fallback failed", error=str(exc))
                suggestions = []
        return suggestions

    @override
    async def similar(self, book: Audiobook) -> list[Audiobook]:
        url = book.url
        if not url:
            return []
        try:
            html = await self._get(url)
        except Exception as exc:
            logger.error("livelib similar failed", url=url, error=str(exc))
            return []

        data = parse_detail_jsonld(html)
        similar: list[Audiobook] = []
        for item in data["similar"]:
            title = item["title"]
            if not title:
                continue
            similar.append(
                Audiobook(
                    source=SOURCE,
                    external_id=external_id_from_url(item["url"]),
                    url=item["url"],
                    title=title,
                    subtitle=None,
                    authors=[],
                    narrators=[],
                    cover_image=None,
                    release_date=_PLACEHOLDER_DATE,
                    runtime_length_min=0,
                )
            )
        return similar

    @override
    async def categories(self) -> dict[str, list[Audiobook]]:
        result: dict[str, list[Audiobook]] = {}
        for category, terms in CATEGORY_TERMS.items():
            books: list[Audiobook] = []
            seen: set[str] = set()
            for term in terms:
                try:
                    found = await self.search(term, num_results=6)
                except Exception as exc:
                    logger.error(
                        "livelib category search failed",
                        category=category,
                        term=term,
                        error=str(exc),
                    )
                    continue
                for book in found:
                    if book.external_id not in seen:
                        seen.add(book.external_id)
                        books.append(book)
                await asyncio.sleep(_POLITE_SLEEP)
            result[category] = books[:_CATEGORY_LIMIT]
        return result
