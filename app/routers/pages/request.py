import json
from typing import Annotated, Literal

from aiohttp import ClientSession
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Form,
    HTTPException,
    Response,
    Security,
)
from sqlmodel import Session

from app.internal.auth.authentication import ABRAuth, DetailedUser
from app.internal.db_queries import downloaded_narrators
from app.internal.env_settings import Settings
from app.internal.models import Audiobook, SelectedRelease
from app.internal.query import query_sources
from app.internal.ranking.quality import quality_config
from app.routers.api.requests import create_request
from app.util.censor import censor
from app.util.connection import get_connection
from app.util.db import get_session
from app.util.log import logger
from app.util.templates import catalog_response
from app.util.toast import ToastException

router = APIRouter(prefix="/request")


@router.post("/hx-add/{book_id}")
async def add_request(
    book_id: str,
    session: Annotated[Session, Depends(get_session)],
    background_task: BackgroundTasks,
    user: Annotated[DetailedUser, Security(ABRAuth())],
):
    try:
        book = await create_request(
            book_ref=book_id,
            session=session,
            background_task=background_task,
            user=user,
        )
    except HTTPException as e:
        logger.warning(
            e.detail,
            username=censor(user.username),
            book_id=book_id,
        )
        raise ToastException(e.detail) from e

    return catalog_response(
        "BookCard",
        book_with_requests=book,
        auto_start_download=quality_config.get_auto_download(session),
        user=user,
    )


@router.get("/{book_id}/releases")
async def choose_releases(
    book_id: str,
    session: Annotated[Session, Depends(get_session)],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    user: Annotated[DetailedUser, Security(ABRAuth())],
    only_body: bool = False,
    refresh: bool = False,
):
    result = await query_sources(
        book_ref=book_id,
        session=session,
        client_session=client_session,
        prefer_snapshot=not refresh,
        force_refresh=refresh,
    )

    narrators = (
        downloaded_narrators(session, result.book.id)
        if isinstance(result.book, Audiobook)
        else []
    )

    if only_body:
        return catalog_response(
            "Request.ReleasesContent",
            result=result,
            user=user,
            downloaded_narrators=narrators,
        )

    return catalog_response(
        "Request.Releases",
        result=result,
        user=user,
        downloaded_narrators=narrators,
    )


@router.post("/hx-select/{book_id}")
async def select_release(
    book_id: str,
    session: Annotated[Session, Depends(get_session)],
    background_task: BackgroundTasks,
    user: Annotated[DetailedUser, Security(ABRAuth())],
    protocol: Annotated[Literal["torrent", "usenet", "direct"], Form()],
    guid: Annotated[str, Form()],
    title: Annotated[str | None, Form()] = None,
    narrators: Annotated[str | None, Form()] = None,
):
    selected_release = SelectedRelease(
        protocol=protocol,
        guid=guid,
        title=title or None,
        narrators=[n.strip() for n in (narrators or "").split(",") if n.strip()],
    )

    try:
        await create_request(
            book_ref=book_id,
            session=session,
            background_task=background_task,
            user=user,
            selected_release=selected_release,
        )
    except HTTPException as e:
        logger.warning(
            e.detail,
            username=censor(user.username),
            book_id=book_id,
        )
        raise ToastException(e.detail) from e

    base_url = Settings().app.base_url.rstrip("/")
    return Response(
        status_code=204,
        headers={
            "HX-Trigger": json.dumps(
                {"toast": {"type": "success", "message": "Release requested"}}
            ),
            "HX-Redirect": f"{base_url}/wishlist",
        },
    )
