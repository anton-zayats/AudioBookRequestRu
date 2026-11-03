from typing import Annotated

from fastapi import APIRouter, Depends, Security
from pydantic import BaseModel
from sqlmodel import Session

from app.internal.auth.authentication import ABRAuth, DetailedUser
from app.internal.models import AudiobookWithRequests
from app.internal.ranking.quality import quality_config
from app.internal.recommendations.local import get_popular_books
from app.routers.api.recommendations import (
    get_categories as api_get_categories,
)
from app.routers.api.recommendations import (
    get_popular_recommendations as api_get_popular_recommendations,
)
from app.routers.api.recommendations import (
    get_recently_requested_recommendations as api_get_recently_requested_recommendations,
)
from app.util.db import get_session
from app.util.templates import catalog_response

router = APIRouter()


@router.get("/")
def read_root(
    user: Annotated[DetailedUser, Security(ABRAuth())],
    session: Annotated[Session, Depends(get_session)],
):
    # hide the section when the same query used by /hx-popular would return nothing
    show_popular = bool(
        get_popular_books(
            session,
            limit=1,
            exclude_requested_username=user.username,
        )
    )
    return catalog_response(
        "Index.Index",
        user=user,
        auto_download=quality_config.get_auto_download(session),
        show_popular=show_popular,
    )


@router.get("/hx-popular")
async def get_popular_recommendations(
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(ABRAuth())],
    min_requests: int = 1,
    limit: int = 10,
    exclude_downloaded: bool = True,
):
    result = await api_get_popular_recommendations(
        session=session,
        user=user,
        min_requests=min_requests,
        limit=limit,
        exclude_downloaded=exclude_downloaded,
    )

    return catalog_response(
        "Index.PopularSection",
        title="Popular",
        user=user,
        reasons=result,
        description="The most popular books on the instance",
        empty="No popular recommendations available at this time. Request some books to start getting recommendations.",
        auto_start_download=quality_config.get_auto_download(session),
    )


class _AudiobookReasonWrapper(BaseModel):
    book: AudiobookWithRequests
    reason: str


@router.get("/hx-recent")
async def get_recently_requested_recommendations(
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(ABRAuth())],
    limit: int = 10,
    days_back: int = 30,
    exclude_downloaded: bool = True,
):
    result = await api_get_recently_requested_recommendations(
        session=session,
        user=user,
        limit=limit,
        days_back=days_back,
        exclude_downloaded=exclude_downloaded,
    )

    reasons = [
        _AudiobookReasonWrapper(book=book, reason="Recently requested")
        for book in result
    ]

    return catalog_response(
        "Index.PopularSection",
        title="Recently Requested",
        reasons=reasons,
        user=user,
        description="Books that have been recently requested by users on the instance",
        empty="No recently requested recommendations available at this time. Request some books to start getting recommendations.",
        auto_start_download=quality_config.get_auto_download(session),
    )


@router.get("/hx-categories")
async def get_categories(
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(ABRAuth())],
):
    categories = await api_get_categories(session=session, user=user)
    return catalog_response(
        "Index.Categories",
        title="Browse by Category",
        user=user,
        categories=categories,
        description="Explore popular Russian audiobooks by category",
        auto_start_download=quality_config.get_auto_download(session),
    )
