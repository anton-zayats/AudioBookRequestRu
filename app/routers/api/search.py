from typing import Annotated

from aiohttp import ClientSession
from fastapi import APIRouter, Depends, Query, Security
from sqlmodel import Session

from app.internal.auth.authentication import AnyAuth, DetailedUser
from app.internal.metadata.registry import get_provider
from app.internal.models import DownloadSource, SearchHistory
from app.internal.search import SourceFilter, search_releases
from app.util.connection import get_connection
from app.util.db import get_session

router = APIRouter(prefix="/search", tags=["Search"])


@router.get("", response_model=list[DownloadSource])
async def search_books(
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(AnyAuth())],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    query: Annotated[
        str | None,
        Query(alias="q", description="Search term (title, author, narrator)."),
    ] = None,
    source: Annotated[
        SourceFilter, Query(description="Filter releases by source.")
    ] = "both",
    num_results: Annotated[int, Query(description="Number of results to return.")] = 20,
    page: Annotated[int, Query(description="Page number, starting at 0.")] = 0,
) -> list[DownloadSource]:
    if not query or not query.strip():
        return []

    releases = await search_releases(
        session=session,
        client_session=client_session,
        query=query.strip(),
        source=source,
    )

    session.add(
        SearchHistory(
            user_username=user.username,
            query=query.strip(),
            result_count=len(releases),
        )
    )
    session.commit()

    start = page * num_results
    return releases[start : start + num_results]


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
