from collections.abc import Sequence
from typing import Literal, cast

from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import InstrumentedAttribute, selectinload
from sqlmodel import Session, asc, col, not_, select

from app.internal.models import (
    Audiobook,
    AudiobookRequest,
    AudiobookWishlistResult,
    ManualBookRequest,
    User,
)


class WishlistCounts(BaseModel):
    requests: int
    downloaded: int
    manual: int


def upsert_audiobooks(
    session: Session,
    books: list[Audiobook],
) -> list[Audiobook]:
    """Persist transient :class:`Audiobook` objects keyed by
    ``(source, external_id)``.

    Existing rows are updated in place, new rows are added. The caller is
    expected to ``session.commit()`` and the returned list contains the
    persisted rows (with their surrogate ``id`` set after commit).
    """
    persisted: list[Audiobook] = []
    by_key: dict[tuple[str, str], Audiobook] = {}
    for book in books:
        key = (book.source, book.external_id)
        if key in by_key:
            persisted.append(by_key[key])
            continue

        existing = session.exec(
            select(Audiobook).where(
                Audiobook.source == book.source,
                Audiobook.external_id == book.external_id,
            )
        ).first()

        if existing is not None:
            existing.title = book.title
            existing.subtitle = book.subtitle
            existing.authors = book.authors
            existing.narrators = book.narrators
            existing.cover_image = book.cover_image
            existing.release_date = book.release_date
            existing.runtime_length_min = book.runtime_length_min
            session.add(existing)
            result = existing
        else:
            session.add(book)
            result = book

        by_key[key] = result
        persisted.append(result)
    return persisted


def get_wishlist_counts(session: Session, user: User | None = None) -> WishlistCounts:
    """
    If a non-admin user is given, only count requests for that user.
    Admins can see and get counts for all requests.
    """
    username = None if user is None or user.is_admin() else user.username

    rows = session.exec(
        select(Audiobook.downloaded, func.count("*"))
        .where(not username or AudiobookRequest.user_username == username)
        .select_from(Audiobook)
        .join(AudiobookRequest)
        .group_by(col(Audiobook.downloaded))
    ).all()
    requests = 0
    downloaded = 0
    for downloaded_status, count in rows:
        if downloaded_status:
            downloaded = count
        else:
            requests = count

    manual = session.exec(
        select(func.count())
        .select_from(ManualBookRequest)
        .where(
            not username or ManualBookRequest.user_username == username,
            col(ManualBookRequest.user_username).is_not(None),
        )
    ).one()

    return WishlistCounts(
        requests=requests,
        downloaded=downloaded,
        manual=manual,
    )


def get_wishlist_results(
    session: Session,
    username: str | None = None,
    response_type: Literal["all", "downloaded", "not_downloaded"] = "all",
) -> list[AudiobookWishlistResult]:
    """
    Gets the books that have been requested. If a username is given only the books requested by that
    user are returned. If no username is given, all book requests are returned.
    """
    match response_type:
        case "downloaded":
            clause = Audiobook.downloaded
        case "not_downloaded":
            clause = not_(Audiobook.downloaded)
        case _:
            clause = True

    results = session.exec(
        select(Audiobook)
        .where(
            clause,
            col(Audiobook.id).in_(
                select(AudiobookRequest.book_id).where(
                    not username or AudiobookRequest.user_username == username
                )
            ),
        )
        .options(
            selectinload(
                cast(
                    InstrumentedAttribute[list[AudiobookRequest]],
                    cast(object, Audiobook.requests),
                )
            )
        )
    ).all()

    return [
        AudiobookWishlistResult(
            book=book,
            requests=book.requests,
        )
        for book in results
    ]


def get_all_manual_requests(
    session: Session, user: User
) -> Sequence[ManualBookRequest]:
    return session.exec(
        select(ManualBookRequest)
        .where(
            user.is_admin() or ManualBookRequest.user_username == user.username,
            col(ManualBookRequest.user_username).is_not(None),
        )
        .order_by(asc(ManualBookRequest.downloaded))
    ).all()
