"""Release-based requests page (P12 target model).

Shows every bookmarked release, grouped by title + author, with a download
state label and per-release download / refresh / remove actions. Admins see
every user's bookmarks; everyone else sees only their own.
"""

from typing import Annotated

from aiohttp import ClientSession
from fastapi import APIRouter, Depends, Security
from sqlmodel import Session

from app.internal.auth.authentication import ABRAuth, DetailedUser
from app.internal.downloads.config import DownloadClientMisconfigured
from app.internal.models import Release
from app.internal.prowlarr.prowlarr import DownloadFailed
from app.internal.releases import (
    ReleaseGroup,
    build_release_views,
    download_release,
    group_release_views,
    list_release_requests,
    refresh_release,
    remove_release_request,
    remove_release_request_all,
)
from app.util.connection import get_connection
from app.util.db import get_session
from app.util.log import logger
from app.util.templates import catalog_response, catalog_response_toast
from app.util.toast import ToastException

router = APIRouter(prefix="/requests")


async def _load_groups(
    session: Session,
    client_session: ClientSession,
    user: DetailedUser,
) -> list[ReleaseGroup]:
    username = None if user.is_admin() else user.username
    bookmarks = list_release_requests(session, username)
    views = await build_release_views(
        session,
        client_session,
        [bookmark.release for bookmark in bookmarks],
        username=user.username,
    )
    return group_release_views(views)


async def _content_response(
    session: Session,
    client_session: ClientSession,
    user: DetailedUser,
    message: str,
    toast_type: str = "success",
):
    groups = await _load_groups(session, client_session, user)
    return catalog_response_toast(
        "Requests.Content",
        message,
        toast_type,  # pyright: ignore[reportArgumentType]
        user=user,
        groups=groups,
    )


def _get_release(session: Session, release_id: int) -> Release:
    release = session.get(Release, release_id)
    if release is None:
        raise ToastException("This release is no longer available")
    return release


@router.get("")
async def read_requests(
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(ABRAuth())],
    client_session: Annotated[ClientSession, Depends(get_connection)],
):
    groups = await _load_groups(session, client_session, user)
    return catalog_response("Requests.Index", user=user, groups=groups)


@router.post("/hx-download/{release_id}")
async def download_request(
    release_id: int,
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(ABRAuth())],
    client_session: Annotated[ClientSession, Depends(get_connection)],
):
    release = _get_release(session, release_id)

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

    return await _content_response(session, client_session, user, message, toast_type)


@router.post("/hx-refresh/{release_id}")
async def refresh_request(
    release_id: int,
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(ABRAuth())],
    client_session: Annotated[ClientSession, Depends(get_connection)],
):
    """Re-fetch the release from its source (fresh URLs / metadata)."""
    release = _get_release(session, release_id)
    try:
        refreshed = await refresh_release(session, client_session, release)
    except Exception as e:
        session.rollback()
        logger.error("Failed to refresh release", error=str(e))
        raise ToastException("Could not refresh the release from its source") from e

    if refreshed is None:
        raise ToastException("The source no longer lists this release")

    return await _content_response(
        session, client_session, user, "Release refreshed from its source"
    )


@router.delete("/hx-remove/{release_id}")
async def remove_request(
    release_id: int,
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(ABRAuth())],
    client_session: Annotated[ClientSession, Depends(get_connection)],
):
    if user.is_admin():
        removed = remove_release_request_all(session, release_id) > 0
    else:
        removed = remove_release_request(session, release_id, user.username)

    if not removed:
        raise ToastException("This request no longer exists")

    return await _content_response(
        session, client_session, user, "Removed from requests"
    )
