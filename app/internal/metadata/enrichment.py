"""Post-grab metadata enrichment from the configured metadata provider.

livelib is a **printed-book** database: it provides a clean title, authors and a
cover, but it has no notion of a narrator. Narrators keep coming from the release
itself (direct providers name their reader) — enrichment never overwrites them.

Enrichment is best-effort and idempotent: a release is looked up once and linked
to the resulting ``Audiobook`` (``release.book_id``); failures are logged and
never fail the download.
"""

from __future__ import annotations

from sqlmodel import Session

from app.internal.db_queries import upsert_audiobooks
from app.internal.metadata.registry import get_provider, is_metadata_enabled
from app.internal.models import Audiobook, Release
from app.util.db import get_session
from app.util.log import logger
from app.util.normalize import normalize_for_match
from app.util.release_title import clean_release_title

#: How many provider search results are considered for a match.
MATCH_LIMIT = 5


def _best_match(
    books: list[Audiobook], title: str, authors: list[str]
) -> Audiobook | None:
    """Pick the provider result that matches the release title (and author)."""
    norm_title = normalize_for_match(title)
    if not norm_title:
        return None
    norm_authors = {normalize_for_match(author) for author in authors if author}

    for book in books:
        candidate_title = normalize_for_match(book.title)
        if not candidate_title:
            continue
        title_matches = (
            candidate_title == norm_title
            or norm_title in candidate_title
            or candidate_title in norm_title
        )
        if not title_matches:
            continue
        if norm_authors:
            candidate_authors = {
                normalize_for_match(author) for author in book.authors if author
            }
            if candidate_authors.isdisjoint(norm_authors):
                continue
        return book
    return None


async def enrich_release(session: Session, release: Release) -> Audiobook | None:
    """Link a release to its livelib work (and clean up title/authors).

    Returns the linked ``Audiobook``, or ``None`` when enrichment is disabled,
    the provider has no matching book, or the release is already enriched.
    """
    if release.book_id is not None:
        return session.get(Audiobook, release.book_id)

    if not is_metadata_enabled(session):
        return None

    title = clean_release_title(release.title)
    if not title.strip():
        return None

    query = f"{title} {release.authors[0]}".strip() if release.authors else title
    try:
        results = await get_provider(session).search(query, num_results=MATCH_LIMIT)
    except Exception as exc:
        logger.warning(
            "livelib enrichment search failed", release_id=release.id, error=str(exc)
        )
        return None

    match = _best_match(results, title, release.authors)
    if match is None:
        logger.debug(
            "no livelib match for release",
            release_id=release.id,
            title=title,
            results=len(results),
        )
        return None

    book = upsert_audiobooks(session, [match])[0]
    session.commit()

    release.book_id = book.id
    # Clean title/authors from the provider; the narrator stays as-is because
    # livelib has none (it only knows printed editions).
    if book.title:
        release.title = book.title
    if book.authors:
        release.authors = book.authors
    session.add(release)
    session.commit()

    logger.info(
        "Enriched release from metadata provider",
        release_id=release.id,
        book_id=book.id,
        title=book.title,
    )
    return book


async def background_enrich_release(release_id: int) -> None:
    """Background entry point: enrich a freshly grabbed release."""
    with next(get_session()) as session:
        release = session.get(Release, release_id)
        if release is None or release.book_id is not None:
            return
        try:
            await enrich_release(session, release)
        except Exception as exc:
            session.rollback()
            logger.warning(
                "release enrichment failed", release_id=release_id, error=str(exc)
            )
