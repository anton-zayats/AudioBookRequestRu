from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, Security
from sqlmodel import Session

from app.internal.auth.authentication import AnyAuth, DetailedUser
from app.internal.db_queries import upsert_audiobooks
from app.internal.metadata.registry import get_provider
from app.internal.models import Audiobook, AudiobookWithRequests
from app.internal.recommendations.local import (
    AudiobookPopularity,
    get_popular_books,
    get_recently_requested_books,
)
from app.util.db import get_session
from app.util.log import logger

router = APIRouter(prefix="/recommendations", tags=["Recommendations"])

FALLBACK_TERMS = [
    "бестселлер",
    "современная проза",
    "детектив",
    "фантастика",
]


def _with_requests(
    books: list[Audiobook], username: str | None
) -> list[AudiobookWithRequests]:
    return [
        AudiobookWithRequests(book=book, requests=book.requests, username=username)
        for book in books
    ]


@router.get("/popular", response_model=list[AudiobookPopularity])
async def get_popular_recommendations(
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(AnyAuth())],
    min_requests: int = 1,
    limit: int = 10,
    exclude_downloaded: bool = True,
) -> list[AudiobookPopularity]:
    return get_popular_books(
        session=session,
        limit=limit,
        min_requests=min_requests,
        exclude_downloaded=exclude_downloaded,
        exclude_requested_username=user.username,
    )


@router.get("/recent", response_model=Sequence[AudiobookWithRequests])
async def get_recently_requested_recommendations(
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(AnyAuth())],
    limit: int = 10,
    days_back: int = 30,
    exclude_downloaded: bool = True,
) -> Sequence[AudiobookWithRequests]:
    return get_recently_requested_books(
        session=session,
        limit=limit,
        days_back=days_back,
        exclude_downloaded=exclude_downloaded,
        exclude_requested_username=user.username,
    )


@router.get("/categories", response_model=dict[str, list[AudiobookWithRequests]])
async def get_categories(
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(AnyAuth())],
) -> dict[str, list[AudiobookWithRequests]]:
    provider = get_provider(session)
    categories = await provider.categories()

    result: dict[str, list[AudiobookWithRequests]] = {}
    for category, books in categories.items():
        merged = upsert_audiobooks(session, books)
        session.commit()
        result[category] = _with_requests(merged, user.username)
    return result


@router.get("/fallback", response_model=list[AudiobookWithRequests])
async def get_fallback(
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(AnyAuth())],
) -> list[AudiobookWithRequests]:
    provider = get_provider(session)
    books: list[Audiobook] = []
    seen: set[str] = set()
    for term in FALLBACK_TERMS:
        try:
            found = await provider.search(term, num_results=6)
        except Exception as exc:
            logger.error("fallback search failed", term=term, error=str(exc))
            continue
        for book in found:
            if book.external_id not in seen:
                seen.add(book.external_id)
                books.append(book)

    merged = upsert_audiobooks(session, books)
    session.commit()
    return _with_requests(merged, user.username)
