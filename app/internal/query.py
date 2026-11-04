import uuid
from contextlib import contextmanager
from typing import Literal, cast

import aiohttp
import pydantic
from aiohttp import ClientSession
from fastapi import HTTPException
from sqlmodel import Session

from app.internal.audiobookshelf.client import abs_trigger_scan
from app.internal.audiobookshelf.config import abs_config
from app.internal.downloads.config import DownloadClientMisconfigured
from app.internal.downloads.dispatch import DirectDownloadFailed, start_direct_download
from app.internal.models import (
    Audiobook,
    DirectSource,
    DownloadSource,
    ManualBookRequest,
    SelectedRelease,
)
from app.internal.prowlarr.prowlarr import (
    DownloadFailed,
    query_prowlarr,
    start_download,
)
from app.internal.prowlarr.util import prowlarr_config
from app.internal.ranking.download_ranking import rank_sources
from app.internal.sources import search_direct_sources
from app.util.db import get_session
from app.util.log import logger

querying: set[str] = set()


@contextmanager
def manage_queried(book_ref: str):
    querying.add(book_ref)
    try:
        yield
    finally:
        try:
            querying.remove(book_ref)
        except KeyError:
            pass


class QueryResult(pydantic.BaseModel):
    sources: list[DownloadSource] | None
    book: Audiobook | ManualBookRequest
    state: Literal["ok", "querying", "uncached"]
    error_message: str | None = None

    @property
    def ok(self) -> bool:
        return self.state == "ok"


def _get_book(session: Session, book_ref: str) -> Audiobook | ManualBookRequest:
    """Resolve a URL token to either an Audiobook (int id) or a manual request (UUID)."""
    try:
        book: Audiobook | ManualBookRequest | None = session.get(
            ManualBookRequest, uuid.UUID(book_ref)
        )
    except ValueError:
        book = None
    if book is not None:
        return book

    try:
        book = session.get(Audiobook, int(book_ref))
    except ValueError:
        book = None
    if book is not None:
        return book

    raise HTTPException(status_code=404, detail="Book not found")


def _selected_source(
    ranked: list[DownloadSource],
    selected_release: SelectedRelease | None,
) -> DownloadSource:
    """Pick the ranked source to download.

    Defaults to the top-ranked source, but honors an explicit
    ``(protocol, guid)`` selection when that source survived ranking.
    """
    if selected_release is None:
        return ranked[0]

    for source in ranked:
        if (
            source.protocol == selected_release.protocol
            and source.guid == selected_release.guid
        ):
            return source

    logger.warning(
        "Selected release not found in ranked sources, falling back to top-ranked",
        protocol=selected_release.protocol,
        guid=selected_release.guid,
    )
    return ranked[0]


async def query_sources(
    book_ref: str,
    session: Session,
    client_session: ClientSession,
    force_refresh: bool = False,
    start_auto_download: bool = False,
    only_return_if_cached: bool = False,
    selected_release: SelectedRelease | None = None,
) -> QueryResult:
    book = _get_book(session, book_ref)

    if book_ref in querying:
        return QueryResult(
            sources=None,
            book=book,
            state="querying",
        )

    with manage_queried(book_ref):
        prowlarr_list: list[DownloadSource]
        if prowlarr_config.is_valid(session):
            prowlarr_sources = await query_prowlarr(
                session,
                client_session,
                book,
                force_refresh=force_refresh,
                only_return_if_cached=only_return_if_cached,
                indexer_ids=prowlarr_config.get_indexers(session),
            )
            if prowlarr_sources is None:
                # `only_return_if_cached` missed the cache; Prowlarr is
                # configured but has nothing cached yet.
                return QueryResult(
                    sources=None,
                    book=book,
                    state="uncached",
                )
            prowlarr_list = [
                cast(DownloadSource, source) for source in prowlarr_sources
            ]
        else:
            prowlarr_list = []

        direct_sources = await search_direct_sources(session, client_session, book)
        sources: list[DownloadSource] = [*prowlarr_list, *direct_sources]

        is_manual = isinstance(book, ManualBookRequest)
        ranked = await rank_sources(session, client_session, sources, book, is_manual)

        # start download if requested
        if start_auto_download and not book.downloaded and len(ranked) > 0:
            target = _selected_source(ranked, selected_release)
            if "sample" in target.indexer_flags:
                logger.info(
                    "Skipping sample-only release for auto-download",
                    guid=target.guid,
                    title=target.title,
                )
            else:
                try:
                    if isinstance(target, DirectSource):
                        await start_direct_download(
                            session=session,
                            client_session=client_session,
                            book=book,
                            source=target,
                        )
                    else:
                        await start_download(
                            session=session,
                            client_session=client_session,
                            guid=target.guid,
                            indexer_id=target.indexer_id,
                            book_ref=book_ref,
                            prowlarr_source=target,
                        )
                except DownloadFailed as e:
                    raise HTTPException(status_code=500, detail=e.message) from e
                except DirectDownloadFailed as e:
                    raise HTTPException(status_code=500, detail=e.message) from e
                except DownloadClientMisconfigured as e:
                    raise HTTPException(status_code=500, detail=str(e)) from e

                # Try to trigger an ABS scan to pick up new media
                try:
                    if abs_config.is_valid(session):
                        await abs_trigger_scan(session, client_session)
                except Exception:
                    logger.error("Failed to trigger ABS scan after starting download")

        return QueryResult(
            sources=ranked,
            book=book,
            state="ok",
        )


async def background_start_query(
    book_ref: str,
    auto_download: bool,
    selected_release: SelectedRelease | None = None,
):
    # Runs after the response is sent; a raise here aborts the ASGI request
    # context ("response already started"), so every failure is logged instead.
    try:
        with next(get_session()) as session:
            async with ClientSession(
                timeout=aiohttp.ClientTimeout(60)
            ) as client_session:
                await query_sources(
                    book_ref=book_ref,
                    session=session,
                    client_session=client_session,
                    start_auto_download=auto_download,
                    selected_release=selected_release,
                )
    except HTTPException as e:
        logger.error(
            "Background query failed",
            book_ref=book_ref,
            status=e.status_code,
            error=e.detail,
        )
    except Exception as e:
        logger.error("Background query failed", book_ref=book_ref, error=str(e))
