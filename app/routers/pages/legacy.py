"""Redirects for URLs that the release-centric model retired.

The book wishlist and the per-book release chooser are gone; old bookmarks and
links land on the release search or the requests page instead.
"""

from typing import Annotated
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.internal.models import Audiobook
from app.util.db import get_session
from app.util.redirect import BaseUrlRedirectResponse

router = APIRouter()


@router.get("/wishlist")
async def legacy_wishlist() -> BaseUrlRedirectResponse:
    return BaseUrlRedirectResponse("/requests")


@router.get("/wishlist/{rest:path}")
async def legacy_wishlist_page(rest: str) -> BaseUrlRedirectResponse:
    if rest.startswith("manual"):
        return BaseUrlRedirectResponse("/requests/manual")
    return BaseUrlRedirectResponse("/requests")


@router.get("/request/{book_ref}/releases")
async def legacy_release_chooser(
    book_ref: str,
    session: Annotated[Session, Depends(get_session)],
) -> BaseUrlRedirectResponse:
    """Send old chooser links to the release search for that book's title."""
    book: Audiobook | None = None
    try:
        book = session.get(Audiobook, int(book_ref))
    except ValueError:
        book = None
    if book is None:
        return BaseUrlRedirectResponse("/search")
    return BaseUrlRedirectResponse(f"/search?q={quote_plus(book.title)}")
