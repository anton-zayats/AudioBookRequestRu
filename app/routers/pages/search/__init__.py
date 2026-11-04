from typing import Annotated

from aiohttp import ClientSession
from fastapi import APIRouter, Depends, Query, Security
from sqlmodel import Session

from app.internal.auth.authentication import ABRAuth, DetailedUser
from app.internal.models import GroupEnum
from app.internal.prowlarr.util import prowlarr_config
from app.internal.ranking.quality import quality_config
from app.internal.search import SourceFilter
from app.routers.api.search import search_books
from app.routers.api.search import search_suggestions as api_search_suggestions
from app.util.connection import get_connection
from app.util.db import get_session
from app.util.log import logger
from app.util.templates import catalog_response
from app.util.toast import ToastException

from . import manual

router = APIRouter(prefix="/search")

router.include_router(manual.router)


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
        results = await search_books(
            session=session,
            user=user,
            client_session=client_session,
            query=query,
            source=source,
            num_results=num_results,
            page=page,
        )

        prowlarr_configured = prowlarr_config.is_valid(session)

        return catalog_response(
            "Search.Index",
            user=user,
            search_term=query or "",
            search_results=results,
            page=page,
            source=source,
            auto_start_download=quality_config.get_auto_download(session)
            and user.is_above(GroupEnum.trusted),
            prowlarr_configured=prowlarr_configured,
        )

    except Exception as e:
        session.rollback()
        logger.error("Error during search", error=e)
        raise ToastException(
            "An error occurred while searching for books. Please try again later."
        ) from e


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
