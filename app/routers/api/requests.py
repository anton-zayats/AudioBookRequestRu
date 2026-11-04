import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from aiohttp import ClientSession
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Response,
    Security,
)
from pydantic import BaseModel, Field
from sqlmodel import Session, asc, col, delete, select

from app.internal.audiobookshelf.client import background_abs_trigger_scan
from app.internal.audiobookshelf.config import abs_config
from app.internal.auth.authentication import AnyAuth, DetailedUser
from app.internal.db_queries import get_wishlist_results
from app.internal.downloads.config import DownloadClientMisconfigured
from app.internal.downloads.dispatch import DirectDownloadFailed, start_direct_download
from app.internal.models import (
    Audiobook,
    AudiobookRequest,
    AudiobookWishlistResult,
    AudiobookWithRequests,
    DirectSource,
    EventEnum,
    GroupEnum,
    ManualBookRequest,
    SelectedRelease,
)
from app.internal.notifications import (
    send_all_manual_notifications,
    send_all_notifications,
)
from app.internal.prowlarr.prowlarr import DownloadFailed, start_download
from app.internal.prowlarr.util import ProwlarrMisconfigured, prowlarr_config
from app.internal.query import QueryResult, background_start_query, query_sources
from app.internal.ranking.quality import quality_config
from app.util.censor import censor
from app.util.connection import get_connection
from app.util.db import get_session
from app.util.log import logger

router = APIRouter(prefix="/requests", tags=["Requests"])


class DownloadSourceBody(BaseModel):
    guid: str
    indexer_id: int
    protocol: Literal["torrent", "usenet", "direct"] = "torrent"
    download_urls: list[str] = Field(default_factory=list)
    title: str | None = None
    indexer: str | None = None


class SelectedReleaseBody(BaseModel):
    selected_release: SelectedRelease | None = None


class ManualRequest(BaseModel):
    title: str
    author: str
    narrator: str | None = None
    subtitle: str | None = None
    publish_date: str | None = None
    info: str | None = None


def _audiobook_id(book_ref: str) -> int:
    """Best-effort parse of an Audiobook surrogate id from a URL token."""
    try:
        return int(book_ref)
    except ValueError:
        raise HTTPException(status_code=404, detail="Book not found") from None


# Must be declared before /{book_ref}, otherwise "manual" is parsed as an id.
@router.post("/manual", status_code=201)
async def create_manual_request(
    body: ManualRequest,
    session: Annotated[Session, Depends(get_session)],
    background_task: BackgroundTasks,
    user: Annotated[DetailedUser, Security(AnyAuth())],
):
    book_request = ManualBookRequest(
        user_username=user.username,
        title=body.title,
        authors=body.author.split(","),
        narrators=body.narrator.split(",") if body.narrator else [],
        subtitle=body.subtitle,
        publish_date=body.publish_date,
        additional_info=body.info,
    )
    session.add(book_request)
    session.commit()

    background_task.add_task(
        send_all_manual_notifications,
        event_type=EventEnum.on_new_request,
        book_request=ManualBookRequest.model_validate(book_request),
    )
    return Response(status_code=201)


@router.post("/{book_ref}", response_model=AudiobookWithRequests)
async def create_request(
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(AnyAuth())],
    background_task: BackgroundTasks,
    book_ref: str,
    selected_release: SelectedRelease | None = None,
) -> AudiobookWithRequests:
    book_id = _audiobook_id(book_ref)
    book = session.get(Audiobook, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    if not session.exec(
        select(AudiobookRequest).where(
            AudiobookRequest.book_id == book_id,
            AudiobookRequest.user_username == user.username,
        )
    ).first():
        book_request = AudiobookRequest(
            book_id=book_id,
            user_username=user.username,
            selected_release=selected_release,
        )
        session.add(book_request)
        session.commit()
        logger.info(
            "Added new audiobook request",
            username=censor(user.username),
            book_id=book_id,
        )
    else:
        raise HTTPException(status_code=409, detail="Book already requested")

    background_task.add_task(
        send_all_notifications,
        event_type=EventEnum.on_new_request,
        book_id=book_id,
    )

    if quality_config.get_auto_download(session) and user.is_above(GroupEnum.trusted):
        # start querying and downloading if auto download is enabled
        background_task.add_task(
            background_start_query,
            book_ref=book_ref,
            auto_download=True,
            selected_release=selected_release,
        )

    # commit() expires the book; reload it so the response has its fields
    session.refresh(book)

    requests = session.exec(
        select(AudiobookRequest).where(AudiobookRequest.book_id == book_id)
    ).all()

    return AudiobookWithRequests(
        book=book,
        requests=list(requests),
        username=user.username,
    )


@router.get("", response_model=list[AudiobookWishlistResult])
async def list_requests(
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(AnyAuth())],
    filter: Annotated[
        Literal["all", "downloaded", "not_downloaded"],
        Query(description="Filter by download status."),
    ] = "all",
):
    username = None if user.is_admin() else user.username
    results = get_wishlist_results(session, username, filter)
    return results


@router.delete("/{book_ref}", status_code=204)
async def delete_request(
    book_ref: str,
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(AnyAuth())],
):
    book_id = _audiobook_id(book_ref)
    if user.is_admin():
        session.execute(
            delete(AudiobookRequest).where(col(AudiobookRequest.book_id) == book_id)
        )
    else:
        session.execute(
            delete(AudiobookRequest).where(
                (col(AudiobookRequest.book_id) == book_id)
                & (col(AudiobookRequest.user_username) == user.username)
            )
        )
    session.commit()
    return Response(status_code=204)


@router.patch("/{book_ref}/downloaded", status_code=204)
async def mark_downloaded(
    book_ref: str,
    session: Annotated[Session, Depends(get_session)],
    background_task: BackgroundTasks,
    _: Annotated[DetailedUser, Security(AnyAuth(GroupEnum.admin))],
):
    book_id = _audiobook_id(book_ref)
    book = session.get(Audiobook, book_id)
    if book:
        book.downloaded = True
        session.add(book)
        session.commit()

        background_task.add_task(
            send_all_notifications,
            event_type=EventEnum.on_successful_download,
            book_id=book_id,
        )
        return Response(status_code=204)
    raise HTTPException(status_code=404, detail="Book not found")


@router.delete("/{book_ref}/downloaded", status_code=204)
async def unmark_downloaded(
    book_ref: str,
    session: Annotated[Session, Depends(get_session)],
    _: Annotated[DetailedUser, Security(AnyAuth(GroupEnum.admin))],
):
    book_id = _audiobook_id(book_ref)
    book = session.get(Audiobook, book_id)
    if book:
        book.downloaded = False
        session.add(book)
        session.commit()
        return Response(status_code=204)
    raise HTTPException(status_code=404, detail="Book not found")


@router.patch("/{book_ref}/release", status_code=204)
async def set_release(
    book_ref: str,
    body: SelectedReleaseBody,
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(AnyAuth())],
    background_task: BackgroundTasks,
):
    book_id = _audiobook_id(book_ref)
    book = session.get(Audiobook, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    if user.is_admin():
        requests: list[AudiobookRequest] = list(
            session.exec(
                select(AudiobookRequest).where(AudiobookRequest.book_id == book_id)
            ).all()
        )
    else:
        own = session.get(AudiobookRequest, (book_id, user.username))
        requests = [own] if own is not None else []

    if not requests:
        raise HTTPException(status_code=404, detail="Request not found")

    for book_request in requests:
        book_request.selected_release = body.selected_release
        session.add(book_request)
    session.commit()

    if (
        quality_config.get_auto_download(session)
        and user.is_above(GroupEnum.trusted)
        and not book.downloaded
    ):
        background_task.add_task(
            background_start_query,
            book_ref=book_ref,
            auto_download=True,
            selected_release=body.selected_release,
        )

    return Response(status_code=204)


@router.get("/manual", response_model=list[ManualBookRequest])
async def list_manual_requests(
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(AnyAuth())],
):
    return session.exec(
        select(ManualBookRequest)
        .where(
            user.is_admin() or ManualBookRequest.user_username == user.username,
            col(ManualBookRequest.user_username).is_not(None),
        )
        .order_by(asc(ManualBookRequest.downloaded))
    ).all()


@router.put("/manual/{id}", status_code=204)
async def update_manual_request(
    id: uuid.UUID,
    body: ManualRequest,
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(AnyAuth())],
):
    book_request = session.get(ManualBookRequest, id)
    if not book_request:
        raise HTTPException(status_code=404, detail="Book request not found")

    if not user.is_admin() and book_request.user_username != user.username:
        raise HTTPException(status_code=403, detail="Not authorized")

    book_request.title = body.title
    book_request.subtitle = body.subtitle
    book_request.authors = body.author.split(",")
    book_request.narrators = body.narrator.split(",") if body.narrator else []
    book_request.publish_date = body.publish_date
    book_request.additional_info = body.info

    session.add(book_request)
    session.commit()
    return Response(status_code=204)


@router.patch("/manual/{id}/downloaded", status_code=204)
async def mark_manual_downloaded(
    id: uuid.UUID,
    session: Annotated[Session, Depends(get_session)],
    background_task: BackgroundTasks,
    _: Annotated[DetailedUser, Security(AnyAuth(GroupEnum.admin))],
):
    book_request = session.get(ManualBookRequest, id)
    if book_request:
        book_request.downloaded = True
        session.add(book_request)
        session.commit()

        background_task.add_task(
            send_all_manual_notifications,
            event_type=EventEnum.on_successful_download,
            book_request=ManualBookRequest.model_validate(book_request),
        )
        return Response(status_code=204)
    raise HTTPException(status_code=404, detail="Request not found")


@router.delete("/manual/{id}", status_code=204)
async def delete_manual_request(
    id: uuid.UUID,
    session: Annotated[Session, Depends(get_session)],
    _: Annotated[DetailedUser, Security(AnyAuth(GroupEnum.admin))],
):
    book = session.get(ManualBookRequest, id)
    if book:
        session.delete(book)
        session.commit()
        return Response(status_code=204)
    raise HTTPException(status_code=404, detail="Request not found")


@router.post(
    "/{book_ref}/refresh",
    status_code=202,
    description="Refresh the sources from prowlarr for a book",
)
async def refresh_source(
    book_ref: str,
    session: Annotated[Session, Depends(get_session)],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    user: Annotated[DetailedUser, Security(AnyAuth())],
    force_refresh: Annotated[
        bool, Query(description="Ignore cached sources and query Prowlarr again.")
    ] = False,
):
    _ = user
    # causes the sources to be placed into cache once they're done
    await query_sources(
        book_ref=book_ref,
        session=session,
        client_session=client_session,
        force_refresh=force_refresh,
    )
    return Response(status_code=202)


@router.get("/{book_ref}/sources", response_model=QueryResult)
async def list_sources(
    book_ref: str,
    session: Annotated[Session, Depends(get_session)],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    admin_user: Annotated[DetailedUser, Security(AnyAuth(GroupEnum.admin))],
    only_cached: Annotated[
        bool, Query(description="Return cached sources without querying Prowlarr.")
    ] = False,
):
    _ = admin_user
    try:
        prowlarr_config.raise_if_invalid(session)
    except ProwlarrMisconfigured:
        raise HTTPException(status_code=400, detail="Prowlarr misconfigured")

    result = await query_sources(
        book_ref,
        session=session,
        client_session=client_session,
        only_return_if_cached=only_cached,
    )
    return result


def _download_book(session: Session, book_ref: str) -> Audiobook | ManualBookRequest:
    """Resolve a URL token to an Audiobook (int id) or a manual request (UUID)."""
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


@router.post("/{book_ref}/download", status_code=204)
async def download_book(
    book_ref: str,
    background_task: BackgroundTasks,
    body: DownloadSourceBody,
    session: Annotated[Session, Depends(get_session)],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    admin_user: Annotated[DetailedUser, Security(AnyAuth(GroupEnum.admin))],
):
    _ = admin_user
    if body.protocol == "direct":
        book = _download_book(session, book_ref)
        source = DirectSource(
            guid=body.guid,
            indexer_id=body.indexer_id,
            indexer=body.indexer or "direct",
            title=body.title or book.title,
            size=0,
            publish_date=datetime(1970, 1, 1, tzinfo=UTC),
            info_url=None,
            indexer_flags=[],
            download_urls=body.download_urls,
        )
        try:
            await start_direct_download(
                session=session,
                client_session=client_session,
                book=book,
                source=source,
            )
        except DirectDownloadFailed as e:
            raise HTTPException(status_code=500, detail=e.message) from e
        except DownloadClientMisconfigured as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
    else:
        try:
            await start_download(
                session=session,
                client_session=client_session,
                guid=body.guid,
                indexer_id=body.indexer_id,
                book_ref=book_ref,
            )
        except DownloadFailed as e:
            raise HTTPException(status_code=500, detail=e.message) from e
        except ProwlarrMisconfigured as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    if abs_config.is_valid(session):
        background_task.add_task(background_abs_trigger_scan)

    return Response(status_code=204)


@router.post("/{book_ref}/auto-download", status_code=204)
async def start_auto_download_endpoint(
    book_ref: str,
    session: Annotated[Session, Depends(get_session)],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    trusted_user: Annotated[DetailedUser, Security(AnyAuth(GroupEnum.trusted))],
):
    _ = trusted_user
    await query_sources(
        book_ref=book_ref,
        start_auto_download=True,
        session=session,
        client_session=client_session,
    )

    return Response(status_code=204)
