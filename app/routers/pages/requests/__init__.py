"""Release-based requests page (P12 target model).

Shows every bookmarked release, grouped by title + author, with a download
state label and per-release download / refresh / remove actions. Admins see
every user's bookmarks; everyone else sees only their own.

The page also hosts the manual requests tab: free-form requests typed by users
when no release exists.
"""

import uuid
from typing import Annotated

from aiohttp import ClientSession
from fastapi import APIRouter, BackgroundTasks, Depends, Security
from sqlmodel import Session

from app.internal.audiobookshelf.client import background_abs_trigger_scan
from app.internal.audiobookshelf.config import abs_config
from app.internal.auth.authentication import ABRAuth, DetailedUser
from app.internal.db_queries import get_all_manual_requests
from app.internal.downloads.config import DownloadClientMisconfigured
from app.internal.metadata.enrichment import background_enrich_release
from app.internal.models import GroupEnum, Release
from app.internal.prowlarr.prowlarr import DownloadFailed
from app.internal.releases import (
    ReleaseGroup,
    build_release_views,
    download_release,
    group_release_views,
    list_release_requests,
    refresh_download_states,
    refresh_release,
    remove_release_request,
    remove_release_request_all,
)
from app.routers.api.requests import delete_manual_request, mark_manual_downloaded
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


def _counts(
    session: Session, user: DetailedUser, groups: list[ReleaseGroup]
) -> dict[str, int]:
    return {
        "releases": sum(len(group.releases) for group in groups),
        "manual": len(get_all_manual_requests(session, user)),
    }


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
        counts=_counts(session, user, groups),
        update_tablist=True,
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
    return catalog_response(
        "Requests.Index",
        user=user,
        groups=groups,
        counts=_counts(session, user, groups),
    )


@router.get("/manual")
async def read_manual_requests(
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(ABRAuth())],
    client_session: Annotated[ClientSession, Depends(get_connection)],
):
    groups = await _load_groups(session, client_session, user)
    return catalog_response(
        "Requests.Manual",
        user=user,
        results=get_all_manual_requests(session, user),
        counts=_counts(session, user, groups),
    )


@router.patch("/manual/hx-mark/{request_id}")
async def mark_manual(
    request_id: uuid.UUID,
    session: Annotated[Session, Depends(get_session)],
    background_task: BackgroundTasks,
    admin_user: Annotated[DetailedUser, Security(ABRAuth(GroupEnum.admin))],
):
    try:
        await mark_manual_downloaded(request_id, session, background_task, admin_user)
    except Exception as e:
        raise ToastException("Could not update the request") from e

    if abs_config.is_valid(session):
        background_task.add_task(background_abs_trigger_scan)

    return catalog_response(
        "Requests.ManualList",
        user=admin_user,
        results=get_all_manual_requests(session, admin_user),
    )


@router.delete("/manual/hx-remove/{request_id}")
async def remove_manual(
    request_id: uuid.UUID,
    session: Annotated[Session, Depends(get_session)],
    admin_user: Annotated[DetailedUser, Security(ABRAuth(GroupEnum.admin))],
):
    try:
        await delete_manual_request(request_id, session, admin_user)
    except Exception as e:
        raise ToastException("Could not remove the request") from e

    return catalog_response(
        "Requests.ManualList",
        user=admin_user,
        results=get_all_manual_requests(session, admin_user),
    )


@router.post("/hx-refresh-status")
async def refresh_status(
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(ABRAuth())],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    background_task: BackgroundTasks,
):
    """Poll the download client for the bookmarked releases' in-flight jobs."""
    username = None if user.is_admin() else user.username
    bookmarks = list_release_requests(session, username)
    result = await refresh_download_states(
        session,
        client_session,
        [bookmark.release.id for bookmark in bookmarks],
    )

    if result.error:
        return await _content_response(
            session, client_session, user, result.error, "error"
        )

    if result.updated and abs_config.is_valid(session):
        background_task.add_task(background_abs_trigger_scan)

    if result.updated:
        message = (
            f"Updated {result.updated} download{'s' if result.updated != 1 else ''}"
        )
    elif result.pending_torrents and not result.torrent_client_configured:
        message = (
            "Torrent downloads are not tracked yet — configure the torrent client "
            "in Settings > Download"
        )
    elif result.pending:
        message = f"{result.pending} download{'s' if result.pending != 1 else ''} still in progress"
    elif result.skipped:
        message = "Could not read the download status from the client"
    else:
        message = "No downloads in progress"
    return await _content_response(session, client_session, user, message)


@router.post("/hx-download/{release_id}")
async def download_request(
    release_id: int,
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(ABRAuth())],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    background_task: BackgroundTasks,
):
    release = _get_release(session, release_id)

    toast_type = "success"
    message = "Release sent to download"
    try:
        await download_release(session, client_session, release, user.username)
        background_task.add_task(background_enrich_release, release_id)
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
