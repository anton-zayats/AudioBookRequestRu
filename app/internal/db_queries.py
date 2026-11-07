from collections.abc import Sequence

from sqlmodel import Session, asc, col, select

from app.internal.models import Audiobook, ManualBookRequest, User


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
