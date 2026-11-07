from typing import Annotated

from aiohttp import ClientSession
from fastapi import APIRouter, Depends, Query, Security
from sqlmodel import Session

from app.internal.auth.authentication import ABRAuth, DetailedUser
from app.internal.downloads.config import DownloadClientMisconfigured
from app.internal.models import DownloadSource, Release
from app.internal.prowlarr.prowlarr import DownloadFailed
from app.internal.releases import (
    ReleaseView,
    add_release_request,
    build_release_views,
    download_release,
    is_sample_only,
    remove_release_request,
    upsert_release,
)
from app.internal.search import SourceFilter
from app.routers.api.search import search_books
from app.routers.api.search import search_suggestions as api_search_suggestions
from app.util.connection import get_connection
from app.util.db import get_session
from app.util.log import logger
from app.util.templates import catalog_response, catalog_response_toast
from app.util.toast import ToastException

from . import manual

router = APIRouter(prefix="/search")

router.include_router(manual.router)


async def _persist_and_build_views(
    session: Session,
    client_session: ClientSession,
    sources: list[DownloadSource],
    username: str,
) -> list[ReleaseView]:
    """Store the search page's results as releases and attach row state."""
    releases = [upsert_release(session, source) for source in sources]
    return await build_release_views(
        session, client_session, releases, username=username
    )


async def _release_view(
    session: Session,
    client_session: ClientSession,
    release_id: int,
    username: str,
) -> ReleaseView:
    release = session.get(Release, release_id)
    if release is None:
        raise ToastException("This release is no longer available")
    views = await build_release_views(
        session, client_session, [release], username=username
    )
    return views[0]


def _row_response(view: ReleaseView, message: str, type: str = "success"):
    return catalog_response_toast(
        "Search.Row",
        message,
        type,  # pyright: ignore[reportArgumentType]
        view=view,
    )


@router.get("")
async def read_search(
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(ABRAuth())],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    query: Annotated[str | None, Query(alias="q")] = None,
    source: Annotated[SourceFilter, Query()] = "both",
    num_results: int = 20,
    page: int = 0,
):
    try:
        # Ask for one extra result to know whether a next page exists.
        results = await search_books(
            session=session,
            user=user,
            client_session=client_session,
            query=query,
            source=source,
            num_results=num_results + 1,
            page=page,
        )
        has_more = len(results) > num_results
        results = results[:num_results]

        views = await _persist_and_build_views(
            session, client_session, results, user.username
        )

        return catalog_response(
            "Search.Index",
            user=user,
            search_term=query or "",
            search_results=views,
            page=page,
            source=source,
            has_more=has_more,
        )

    except Exception as e:
        session.rollback()
        logger.error("Error during search", error=e)
        raise ToastException(
            "An error occurred while searching for books. Please try again later."
        ) from e


@router.post("/hx-download/{release_id}")
async def download_row(
    release_id: int,
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(ABRAuth())],
    client_session: Annotated[ClientSession, Depends(get_connection)],
):
    """Send a search result straight to its download client."""
    release = session.get(Release, release_id)
    if release is None:
        raise ToastException("This release is no longer available")

    toast_type = "success"
    message = "Release sent to download"
    try:
        await download_release(session, client_session, release, user.username)
    except (DownloadFailed, DownloadClientMisconfigured) as e:
        session.rollback()
        toast_type = "error"
        message = str(e)
    except Exception as e:
        session.rollback()
        logger.error("Failed to download release", error=str(e))
        toast_type = "error"
        message = "Failed to send the release to the download client"

    view = await _release_view(session, client_session, release_id, user.username)
    return _row_response(view, message, toast_type)


@router.post("/hx-toggle-request/{release_id}")
async def toggle_request_row(
    release_id: int,
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(ABRAuth())],
    client_session: Annotated[ClientSession, Depends(get_connection)],
):
    """Bookmark or un-bookmark a search result (download later)."""
    release = session.get(Release, release_id)
    if release is None:
        raise ToastException("This release is no longer available")

    if is_sample_only(release):
        raise ToastException("Sample-only releases cannot be requested")

    if remove_release_request(session, release_id, user.username):
        message = "Removed from requests"
    else:
        add_release_request(session, release_id, user.username)
        message = "Added to requests"

    view = await _release_view(session, client_session, release_id, user.username)
    return _row_response(view, message)


@router.get("/hx-suggestions")
async def search_suggestions(
    session: Annotated[Session, Depends(get_session)],
    query: Annotated[str, Query(alias="q")],
    user: Annotated[DetailedUser, Security(ABRAuth())],
):
    if query.strip():
        suggestions = await api_search_suggestions(session, query, user)
    else:
        suggestions = []
    return catalog_response(
        "Search.Suggestions",
        id="search-suggestions",
        suggestions=suggestions,
    )
