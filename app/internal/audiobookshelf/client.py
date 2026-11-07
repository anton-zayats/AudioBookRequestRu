from __future__ import annotations

import posixpath
from datetime import UTC, datetime
from typing import Literal

from aiohttp import ClientSession
from pydantic import BaseModel, TypeAdapter
from sqlmodel import Session

from app.internal.audiobookshelf.config import abs_config
from app.internal.audiobookshelf.types import (
    ABSBookItem,
    ABSBookItemMinified,
    ABSLibrary,
    ABSPodcastItem,
)
from app.internal.models import Audiobook
from app.util.cache import SimpleCache
from app.util.connection import USER_AGENT
from app.util.db import get_session
from app.util.log import logger
from app.util.normalize import normalize_for_match
from app.util.release_title import clean_release_title, release_author_key


def _headers(session: Session) -> dict[str, str]:
    token = abs_config.get_api_token(session)
    assert token is not None
    return {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}


class _LibraryArray(BaseModel):
    libraries: list[ABSLibrary] = []


async def abs_get_libraries(
    session: Session, client_session: ClientSession
) -> list[ABSLibrary]:
    base_url = abs_config.get_base_url(session)
    if not base_url:
        return []
    url = posixpath.join(base_url, "api/libraries")
    try:
        async with client_session.get(url, headers=_headers(session)) as resp:
            if not resp.ok:
                logger.error(
                    "ABS: failed to fetch libraries",
                    status=resp.status,
                    reason=resp.reason,
                )
                return []
            data = _LibraryArray.model_validate(await resp.json())
            return data.libraries
    except Exception as e:
        logger.error("ABS: exception fetching libraries", error=str(e))
        return []


async def abs_trigger_scan(session: Session, client_session: ClientSession) -> bool:
    base_url = abs_config.get_base_url(session)
    lib_id = abs_config.get_library_id(session)
    if not base_url or not lib_id:
        return False
    url = posixpath.join(base_url, f"api/libraries/{lib_id}/scan")
    logger.debug("ABS: triggering library scan", library_id=lib_id, url=url)
    async with client_session.post(url, headers=_headers(session), json={}) as resp:
        if not resp.ok:
            logger.warning(
                "ABS: failed to trigger scan", status=resp.status, reason=resp.reason
            )
            return False
        return True


async def background_abs_trigger_scan():
    with next(get_session()) as session:
        async with ClientSession() as client_session:
            logger.debug("ABS: running background library scan trigger")
            success = await abs_trigger_scan(session, client_session)
            logger.info(
                "ABS: background library scan trigger complete", success=success
            )


class _ListResponseBook(BaseModel):
    results: list[ABSBookItemMinified] = []
    mediaType: Literal["book"]


class _ListResponsePodcast(BaseModel):
    results: list[ABSPodcastItem] = []
    mediaType: Literal["podcast"]


_ListResponse: TypeAdapter[_ListResponseBook | _ListResponsePodcast] = TypeAdapter(
    _ListResponseBook | _ListResponsePodcast
)


async def abs_list_library_items(
    session: Session,
    client_session: ClientSession,
    limit: int = 10,
) -> list[Audiobook]:
    """
    Fetch a page of items from the configured ABS library and map them to Audiobook objects to render on the homepage
    """
    base_url = abs_config.get_base_url(session)
    lib_id = abs_config.get_library_id(session)
    if not base_url or not lib_id:
        return []

    url = posixpath.join(base_url, f"api/libraries/{lib_id}/items")
    params = {
        "limit": str(limit),
        "page": "0",
        "minified": "1",
        "sort": "addedAt",
        "desc": "1",
    }

    try:
        async with client_session.get(
            url, headers=_headers(session), params=params
        ) as resp:
            if not resp.ok:
                logger.debug(
                    "ABS: failed to list library items",
                    status=resp.status,
                    reason=resp.reason,
                )
                return []
            payload = _ListResponse.validate_python(await resp.json())
            if payload.mediaType == "podcast":
                logger.warning(
                    "ABS: podcasts not supported in library listing", lib_id=lib_id
                )
                return []
    except Exception as e:
        logger.debug("ABS: exception listing library items", error=str(e))
        return []

    results = payload.results
    books: list[Audiobook] = []
    for item in results:
        try:
            metadata = item.media.metadata
            title = metadata.title
            subtitle = metadata.subtitle
            authors = [metadata.authorName]
            narrators = [metadata.narratorName]
            # Cover: ABS exposes cover via /api/items/:id/cover
            cover_image = posixpath.join(base_url, f"api/items/{item.id}/cover")
            # Duration in seconds -> minutes
            try:
                runtime_length_min = round(item.media.duration / 60)
            except Exception:
                runtime_length_min = 0

            if metadata.publishedDate:
                try:
                    # Try ISO format
                    release_date = datetime.fromisoformat(metadata.publishedDate)
                    if release_date.tzinfo is None:
                        release_date = release_date.replace(tzinfo=UTC)
                except Exception:
                    release_date = datetime.now(UTC)
            else:
                release_date = datetime.now(UTC)

            if not title:
                logger.warning(
                    "ABS: skipping library item with missing title",
                    item_id=item.id,
                    title=title,
                )
                continue

            book = Audiobook(
                external_id=item.id,
                title=title,
                subtitle=subtitle,
                authors=authors,
                narrators=narrators,
                cover_image=cover_image,
                release_date=release_date,
                runtime_length_min=runtime_length_min,
            )
            books.append(book)
        except Exception as e:
            logger.debug("ABS: failed to map library item", error=str(e))

    return books


class _BookSearchResult(BaseModel):
    class _LibraryItem(BaseModel):
        libraryItem: ABSBookItem

    book: list[_LibraryItem] | None = None


async def _abs_search(
    session: Session, client_session: ClientSession, query: str
) -> list[ABSBookItem]:
    base_url = abs_config.get_base_url(session)
    lib_id = abs_config.get_library_id(session)
    if not base_url or not lib_id:
        return []
    url = posixpath.join(base_url, f"api/libraries/{lib_id}/search")
    try:
        async with client_session.get(
            url, headers=_headers(session), params={"q": query}
        ) as resp:
            if not resp.ok:
                logger.debug(
                    "ABS: search failed", status=resp.status, reason=resp.reason
                )
                return []
            data = _BookSearchResult.model_validate(await resp.json())
            if data.book is None:
                logger.warning(
                    "ABS: search returned no book results", query=query, lib_id=lib_id
                )
                return []
            return [it.libraryItem for it in data.book]
    except Exception as e:
        logger.debug("ABS: exception during search", error=str(e))
        return []


#: How long an "is this title in the library" answer is reused (seconds).
LIBRARY_CHECK_TTL = 300

_library_checks: SimpleCache[bool, str, str, str] = SimpleCache()


def flush_library_check_cache() -> None:
    """Drop cached in-library answers (tests and settings changes)."""
    _library_checks.flush()


def _library_cache_key(
    session: Session, title: str, authors: list[str]
) -> tuple[str, str, str]:
    return (
        abs_config.get_library_id(session) or "",
        normalize_for_match(clean_release_title(title)),
        release_author_key(authors),
    )


async def abs_title_author_in_library(
    session: Session,
    client_session: ClientSession,
    title: str,
    authors: list[str],
) -> bool:
    """Whether a title (optionally by one of the authors) exists in ABS.

    Titles are cleaned and normalized before comparison so release titles such as
    ``Book.Name.MP3`` still match the library's ``Book Name``. When no authors are
    known the title comparison alone decides.
    """
    if not abs_config.is_valid(session):
        return False

    clean_title = clean_release_title(title)
    norm_title = normalize_for_match(clean_title)
    if not norm_title:
        return False

    cache_key = _library_cache_key(session, title, authors)
    cached = _library_checks.get(LIBRARY_CHECK_TTL, *cache_key)
    if cached is not None:
        return cached

    query = clean_title if not authors else f"{clean_title} {' '.join(authors)}"
    candidates = await _abs_search(session, client_session, query.strip())
    norm_authors = {normalize_for_match(author) for author in authors if author}

    found = False
    for item in candidates:
        candidate_title = item.media.metadata.title
        if not candidate_title:
            continue
        if normalize_for_match(candidate_title) != norm_title:
            continue
        candidate_authors = {
            normalize_for_match(author.name) for author in item.media.metadata.authors
        }
        if not norm_authors or norm_authors & candidate_authors:
            found = True
            break

    logger.debug(
        "ABS: library check",
        title=clean_title,
        authors=authors,
        in_library=found,
        candidate_count=len(candidates),
    )
    _library_checks.set(found, *cache_key)
    return found
