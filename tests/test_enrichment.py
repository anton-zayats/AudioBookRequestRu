"""Post-grab livelib enrichment (metadata only — livelib knows no narrators)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import override

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.internal.metadata.base import MetadataProvider
from app.internal.metadata.enrichment import background_enrich_release, enrich_release
from app.internal.models import Audiobook, Release
from app.internal.providers.configuration import (
    provider_configuration_cache,
    set_field_value,
)
from app.internal.providers.definitions import LIVELIB, field_by_key
from app.internal.releases import upsert_release
from tests.fakes import direct_source
from tests.http import json_object


def _book(
    title: str = "Test Book",
    authors: list[str] | None = None,
    external_id: str = "work-enrich-1",
) -> Audiobook:
    return Audiobook(
        source="livelib",
        external_id=external_id,
        url=f"https://www.livelib.ru/book/{external_id}-test",
        title=title,
        subtitle=None,
        authors=["Mock Author"] if authors is None else authors,
        narrators=[],
        cover_image="https://example.com/cover.jpg",
        release_date=datetime(2020, 1, 1, tzinfo=UTC),
        runtime_length_min=600,
    )


class _Provider(MetadataProvider):
    def __init__(self, results: list[Audiobook]) -> None:
        self.results = results
        self.calls = 0

    @override
    async def search(
        self, query: str, num_results: int = 20, page: int = 0
    ) -> list[Audiobook]:
        _ = (query, num_results, page)
        self.calls += 1
        return self.results

    @override
    async def get_book(self, external_id: str) -> Audiobook | None:
        return None

    @override
    async def suggestions(self, query: str) -> list[str]:
        return []

    @override
    async def similar(self, book: Audiobook) -> list[Audiobook]:
        return []

    @override
    async def categories(self) -> dict[str, list[Audiobook]]:
        return {}


def _use_provider(monkeypatch: pytest.MonkeyPatch, provider: _Provider) -> None:
    monkeypatch.setattr(
        "app.internal.metadata.enrichment.get_provider", lambda _session: provider
    )


def _release(db: Session, title: str = "Test Book mp3.64kbps") -> Release:
    source = direct_source()
    source.title = title
    release = upsert_release(db, source)
    assert release.id is not None
    return release


def test_enrich_release_links_book_and_cleans_metadata(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _Provider([_book()])
    _use_provider(monkeypatch, provider)
    release = _release(db)

    book = asyncio.run(enrich_release(db, release))

    assert book is not None
    assert release.book_id == book.id
    # Clean title/authors come from livelib...
    assert release.title == "Test Book"
    assert release.authors == ["Mock Author"]
    # ...but the narrator is not touched: livelib only knows printed books.
    assert release.narrators == ["Reader B"]
    assert provider.calls == 1

    stored = db.get(Audiobook, book.id)
    assert stored is not None and stored.external_id == "work-enrich-1"


def test_enrich_release_is_idempotent(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _Provider([_book()])
    _use_provider(monkeypatch, provider)
    release = _release(db)

    assert asyncio.run(enrich_release(db, release)) is not None
    assert asyncio.run(enrich_release(db, release)) is not None
    assert provider.calls == 1


def test_enrich_release_without_match_keeps_release_untouched(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _Provider([_book(title="Something Else Entirely")])
    _use_provider(monkeypatch, provider)
    release = _release(db)
    original_title = release.title

    assert asyncio.run(enrich_release(db, release)) is None
    assert release.book_id is None
    assert release.title == original_title
    assert (
        db.exec(
            select(Audiobook).where(Audiobook.external_id == "work-enrich-1")
        ).first()
        is None
    )


def test_enrich_release_requires_matching_author(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _Provider([_book(authors=["Another Author"])])
    _use_provider(monkeypatch, provider)
    release = _release(db)
    release.authors = ["Mock Author"]
    db.add(release)
    db.commit()

    assert asyncio.run(enrich_release(db, release)) is None


def test_enrich_release_skipped_when_provider_disabled(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _Provider([_book()])
    _use_provider(monkeypatch, provider)
    enabled = field_by_key(LIVELIB, "livelib_enabled")
    assert enabled is not None
    set_field_value(db, enabled, "false")

    assert asyncio.run(enrich_release(db, _release(db))) is None
    assert provider.calls == 0

    provider_configuration_cache.flush()


def test_background_enrich_release(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _Provider([_book()])
    _use_provider(monkeypatch, provider)
    release = _release(db)
    release_id = release.id
    assert release_id is not None

    asyncio.run(background_enrich_release(release_id))

    db.expire_all()
    stored = db.get(Release, release_id)
    assert stored is not None
    assert stored.book_id is not None


def test_download_triggers_background_enrichment(
    admin_client: TestClient,
    aria2: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = aria2
    provider = _Provider([_book()])
    _use_provider(monkeypatch, provider)

    response = admin_client.post(
        "/api/releases/download",
        json=direct_source().model_dump(mode="json"),
    )
    assert response.status_code == 201, response.text
    assert json_object(response)["title"] == "Test Book"
    assert provider.calls == 1
