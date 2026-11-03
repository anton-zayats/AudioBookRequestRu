from typing import Annotated, Literal

from aiohttp import ClientSession
from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Security
from sqlmodel import Session

from app.internal.auth.authentication import ABRAuth, DetailedUser
from app.internal.models import GroupEnum
from app.routers.api.requests import DownloadSourceBody
from app.routers.api.requests import download_book as api_download_book
from app.routers.api.requests import list_sources as api_list_sources
from app.util.connection import get_connection
from app.util.db import get_session
from app.util.redirect import BaseUrlRedirectResponse
from app.util.templates import catalog_response

router = APIRouter(prefix="/sources")


@router.get("/{book_id}")
async def list_sources(
    book_id: str,
    session: Annotated[Session, Depends(get_session)],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    admin_user: Annotated[DetailedUser, Security(ABRAuth(GroupEnum.admin))],
    only_body: bool = False,
):
    try:
        result = await api_list_sources(
            book_id,
            session,
            client_session,
            admin_user,
            only_cached=not only_body,
        )
    except HTTPException as e:
        if e.detail == "Prowlarr misconfigured":
            return BaseUrlRedirectResponse(
                "/settings/prowlarr?prowlarr_misconfigured=1", status_code=302
            )
        raise

    if only_body:
        return catalog_response(
            "Wishlist.Sources.Content",
            result=result,
        )

    return catalog_response(
        "Wishlist.Sources.Index",
        user=admin_user,
        result=result,
    )


@router.post("/{book_id}")
async def download_book(
    background_task: BackgroundTasks,
    book_id: str,
    guid: Annotated[str, Form()],
    indexer_id: Annotated[int, Form()],
    session: Annotated[Session, Depends(get_session)],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    admin_user: Annotated[DetailedUser, Security(ABRAuth(GroupEnum.admin))],
    protocol: Annotated[Literal["torrent", "usenet", "direct"], Form()] = "torrent",
    title: Annotated[str | None, Form()] = None,
    indexer: Annotated[str | None, Form()] = None,
    download_urls: Annotated[list[str] | None, Form()] = None,
):
    body = DownloadSourceBody(
        guid=guid,
        indexer_id=indexer_id,
        protocol=protocol,
        title=title,
        indexer=indexer,
        download_urls=download_urls or [],
    )
    return await api_download_book(
        background_task=background_task,
        book_ref=book_id,
        body=body,
        session=session,
        client_session=client_session,
        admin_user=admin_user,
    )
