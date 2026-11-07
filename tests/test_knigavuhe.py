"""Offline tests for the knigavuhe direct-source provider (no network)."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import override

import aiohttp
import pytest
from sqlmodel import Session

from app.internal.models import Audiobook, DirectSource, ManualBookRequest
from app.internal.providers.configuration import SessionContainer
from app.internal.sources import registry
from app.internal.sources.base import DirectSourceProvider
from app.internal.sources.knigavuhe import (
    build_source,
    extract_playlist,
    item_urls,
    parse_search_cards,
    split_playlist_urls,
)
from tests.constants import BOOK_ID

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "knigavuhe"

_AUDIO_URL_RE = re.compile(
    r"^https://s1\.knigavuhe\.org/1/audio/285/[0-9a-f]{32}/\d{4}\.mp3(\?\d+)?$"
)


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _canned_source() -> DirectSource:
    return DirectSource(
        guid="https://knigavuhe.org/book/285-master-i-margarita-1/",
        indexer_id=1000,
        indexer="knigavuhe",
        title="Мастер и Маргарита",
        size=0,
        publish_date=datetime(1970, 1, 1, tzinfo=UTC),
        info_url="https://knigavuhe.org/book/285-master-i-margarita-1/",
        indexer_flags=[],
        protocol="direct",
        download_urls=[
            "https://s1.knigavuhe.org/1/audio/285/7ed0707fe3d621de3993505eb3f58b42/0001.mp3?1"
        ],
        filetype="mp3",
    )


def test_parse_search_cards() -> None:
    cards = parse_search_cards(_read("search-master-i-margarita.html"), 3)
    assert len(cards) == 3

    top = cards[0]
    assert top.slug == "285-master-i-margarita-1"
    assert top.title == "Мастер и Маргарита"
    assert top.author == "Михаил Булгаков"
    assert "Кира Серова" in top.readers


def test_extract_playlist() -> None:
    book_id, items = extract_playlist(_read("book-285-master-i-margarita-1.html"))
    assert book_id == 285
    assert len(items) == 197

    urls = item_urls(items)
    assert len(urls) == 197
    assert all(_AUDIO_URL_RE.match(url) for url in urls)
    assert all(isinstance(item.get("duration"), int) for item in items)


def test_build_source_from_card_and_playlist() -> None:
    cards = parse_search_cards(_read("search-master-i-margarita.html"), 1)
    _, items = extract_playlist(_read("book-285-master-i-margarita-1.html"))

    source = build_source(
        cards[0],
        "https://knigavuhe.org/book/285-master-i-margarita-1/",
        item_urls(items),
    )

    assert source.protocol == "direct"
    assert source.filetype == "mp3"
    assert source.book_metadata.filetype == "mp3"
    assert source.book_metadata.narrators
    assert len(source.download_urls) == 197


class _FakeProvider(DirectSourceProvider):
    name: str
    _sources: list[DirectSource]
    called: bool
    disabled: bool

    def __init__(
        self,
        name: str = "fake",
        sources: list[DirectSource] | None = None,
    ) -> None:
        self.name = name
        self._sources = sources or []
        self.called = False
        self.disabled = False

    @override
    def is_enabled(self, container: SessionContainer) -> bool:
        _ = container
        return not self.disabled

    @override
    async def search(
        self,
        book: Audiobook | ManualBookRequest,
        container: SessionContainer,
    ) -> list[DirectSource]:
        _ = (book, container)
        self.called = True
        return list(self._sources)


def test_search_direct_sources_aggregates(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    book = db.get(Audiobook, BOOK_ID)
    assert book is not None

    provider = _FakeProvider(sources=[_canned_source()])
    monkeypatch.setattr(registry, "direct_source_providers", [provider])

    async def run() -> list[DirectSource]:
        async with aiohttp.ClientSession() as client_session:
            return await registry.search_direct_sources(db, client_session, book)

    sources = asyncio.run(run())
    assert len(sources) == 1
    assert sources[0].guid == "https://knigavuhe.org/book/285-master-i-margarita-1/"
    assert provider.called is True


def test_search_direct_sources_skips_disabled(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    book = db.get(Audiobook, BOOK_ID)
    assert book is not None

    provider = _FakeProvider(name="knigavuhe", sources=[_canned_source()])
    provider.disabled = True
    monkeypatch.setattr(registry, "direct_source_providers", [provider])

    async def run() -> list[DirectSource]:
        async with aiohttp.ClientSession() as client_session:
            return await registry.search_direct_sources(db, client_session, book)

    sources = asyncio.run(run())
    assert sources == []
    assert provider.called is False


def _item(url: str) -> dict[str, object]:
    return {"url": url}


def test_split_playlist_urls_marks_litres_trial_as_sample() -> None:
    trial_items = [_item("https://www.litres.ru/audiotrial/?art=6713197")]
    audio, trial = split_playlist_urls(trial_items)
    assert audio == []
    assert trial is True

    full_items = [
        _item("https://s9.knigavuhe.org/1/audio/42021/abc/01-01.mp3"),
        _item("https://s9.knigavuhe.org/1/audio/42021/def/01-02.mp3"),
    ]
    audio, trial = split_playlist_urls(full_items)
    assert trial is False
    assert audio == [
        "https://s9.knigavuhe.org/1/audio/42021/abc/01-01.mp3",
        "https://s9.knigavuhe.org/1/audio/42021/def/01-02.mp3",
    ]

    mixed = [
        _item("https://s9.knigavuhe.org/1/audio/42021/abc/01-01.mp3"),
        _item("https://www.litres.ru/audiotrial/?art=1"),
    ]
    audio, trial = split_playlist_urls(mixed)
    assert trial is True
    assert audio == ["https://s9.knigavuhe.org/1/audio/42021/abc/01-01.mp3"]
