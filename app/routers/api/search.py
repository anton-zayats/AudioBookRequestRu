from typing import Annotated

from aiohttp import ClientSession
from fastapi import APIRouter, Depends, Query, Security
from sqlmodel import Session

from app.internal.audiobookshelf.client import abs_mark_downloaded_flags
from app.internal.auth.authentication import AnyAuth, DetailedUser
from app.internal.db_queries import upsert_audiobooks
from app.internal.metadata.registry import get_provider
from app.internal.models import AudiobookWithRequests
from app.util.connection import get_connection
from app.util.db import get_session

router = APIRouter(prefix="/search", tags=["Search"])


@router.get("", response_model=list[AudiobookWithRequests])
async def search_books(
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(AnyAuth())],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    query: Annotated[
        str | None,
        Query(alias="q", description="Search term (title, author, narrator)."),
    ] = None,
    num_results: Annotated[int, Query(description="Number of results to return.")] = 20,
    page: Annotated[int, Query(description="Page number, starting at 0.")] = 0,
) -> list[AudiobookWithRequests]:
    if not query or not query.strip():
        return []

    provider = get_provider(session)
    books = await provider.search(query=query, num_results=num_results, page=page)
    merged = upsert_audiobooks(session, books)
    session.commit()
    await abs_mark_downloaded_flags(session, client_session, merged)

    return [
        AudiobookWithRequests(book=book, requests=book.requests, username=user.username)
        for book in merged
    ]


@router.get("/suggestions", response_model=list[str])
async def search_suggestions(
    session: Annotated[Session, Depends(get_session)],
    query: Annotated[str, Query(alias="q", description="Search term.")],
    user: Annotated[DetailedUser, Security(AnyAuth())],
) -> list[str]:
    _ = user
    if not query.strip():
        return []
    provider = get_provider(session)
    return await provider.suggestions(query)
