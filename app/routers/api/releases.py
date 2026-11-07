from datetime import datetime
from typing import Annotated

from aiohttp import ClientSession
from fastapi import APIRouter, Depends, HTTPException, Response, Security
from pydantic import BaseModel
from sqlmodel import Session

from app.internal.auth.authentication import AnyAuth, DetailedUser
from app.internal.downloads.config import DownloadClientMisconfigured
from app.internal.models import DownloadSource, GroupEnum, Release
from app.internal.prowlarr.prowlarr import DownloadFailed
from app.internal.releases import (
    ReleaseState,
    add_release_request,
    build_release_views,
    download_release,
    list_release_requests,
    remove_release_request,
    upsert_release,
)
from app.util.connection import get_connection
from app.util.db import get_session

router = APIRouter(prefix="/releases", tags=["Releases"])


class ReleaseRequestOut(BaseModel):
    release: Release
    requested_at: datetime
    requested_by: list[str] = []
    state: ReleaseState = "not_downloaded"
    in_library: bool | None = None


@router.post("/download", status_code=201, response_model=Release)
async def download(
    body: DownloadSource,
    session: Annotated[Session, Depends(get_session)],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    user: Annotated[DetailedUser, Security(AnyAuth(GroupEnum.trusted))],
) -> Release:
    release = upsert_release(session, body)
    try:
        await download_release(session, client_session, release, user.username)
    except (DownloadFailed, DownloadClientMisconfigured) as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    session.refresh(release)
    return release


@router.post("/requests", status_code=201, response_model=Release)
async def add_request(
    body: DownloadSource,
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(AnyAuth())],
) -> Release:
    release = upsert_release(session, body)
    add_release_request(session, release.id, user.username)
    session.refresh(release)
    return release


@router.get("/requests", response_model=list[ReleaseRequestOut])
async def list_requests(
    session: Annotated[Session, Depends(get_session)],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    user: Annotated[DetailedUser, Security(AnyAuth())],
) -> list[ReleaseRequestOut]:
    # Admins see every user's bookmarks (a fulfilment queue); everyone else only
    # their own.
    username = None if user.is_admin() else user.username
    bookmarks = list_release_requests(session, username)
    views = await build_release_views(
        session,
        client_session,
        [bookmark.release for bookmark in bookmarks],
        username=user.username,
    )
    views_by_release = {view.id: view for view in views}

    output: list[ReleaseRequestOut] = []
    for bookmark in bookmarks:
        view = views_by_release.get(bookmark.release.id)
        output.append(
            ReleaseRequestOut(
                release=bookmark.release,
                requested_at=bookmark.requested_at,
                requested_by=bookmark.users,
                state=view.state if view else "not_downloaded",
                in_library=view.in_library if view else None,
            )
        )
    return output


@router.delete("/requests/{release_id}", status_code=204)
async def remove_request(
    release_id: int,
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(AnyAuth())],
) -> Response:
    if not remove_release_request(session, release_id, user.username):
        raise HTTPException(status_code=404, detail="Request not found")
    return Response(status_code=204)


@router.post("/requests/{release_id}/download", status_code=201, response_model=Release)
async def download_bookmarked(
    release_id: int,
    session: Annotated[Session, Depends(get_session)],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    user: Annotated[DetailedUser, Security(AnyAuth(GroupEnum.trusted))],
) -> Release:
    release = session.get(Release, release_id)
    if release is None:
        raise HTTPException(status_code=404, detail="Release not found")
    try:
        await download_release(session, client_session, release, user.username)
    except (DownloadFailed, DownloadClientMisconfigured) as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    session.refresh(release)
    return release
