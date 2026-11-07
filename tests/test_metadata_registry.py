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


def test_provider_uses_configured_settings(db: Session) -> None:
    from app.internal.metadata.registry import is_metadata_enabled
    from app.internal.providers.configuration import set_field_value
    from app.internal.providers.definitions import LIVELIB, field_by_key

    assert is_metadata_enabled(db) is True
    default = get_provider(db)
    assert isinstance(default, LivelibProvider)
    assert default.base_url == "https://www.livelib.ru"
    assert default.delay == 0.5

    for key, value in (
        ("livelib_base_url", "https://mirror.example"),
        ("livelib_request_delay_ms", "1500"),
        ("livelib_timeout_seconds", "10"),
        ("livelib_enabled", "false"),
    ):
        provider_field = field_by_key(LIVELIB, key)
        assert provider_field is not None
        set_field_value(db, provider_field, value)

    configured = get_provider(db)
    assert isinstance(configured, LivelibProvider)
    assert configured.base_url == "https://mirror.example"
    assert configured.delay == 1.5
    assert configured.timeout == 10.0
    assert is_metadata_enabled(db) is False
