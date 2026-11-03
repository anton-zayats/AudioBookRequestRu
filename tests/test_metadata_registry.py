"""Metadata registry resolution tests (offline)."""

from __future__ import annotations

from sqlmodel import Session

from app.internal.metadata.base import MetadataProvider
from app.internal.metadata.livelib import LivelibProvider
from app.internal.metadata.registry import get_provider, metadata_source


def test_default_source_is_livelib(db: Session) -> None:
    assert metadata_source.get_source(db) == "livelib"


def test_get_provider_resolves_livelib(db: Session) -> None:
    provider = get_provider(db)
    assert isinstance(provider, LivelibProvider)
    assert isinstance(provider, MetadataProvider)


def test_external_link_returns_book_url(db: Session) -> None:
    from datetime import UTC, datetime

    from app.internal.models import Audiobook

    book = Audiobook(
        source="livelib",
        external_id="1",
        url="https://www.livelib.ru/book/1-one",
        title="One",
        subtitle=None,
        cover_image=None,
        release_date=datetime(1970, 1, 1, tzinfo=UTC),
        runtime_length_min=0,
    )
    assert get_provider(db).external_link(book) == "https://www.livelib.ru/book/1-one"
