"""Shared offline test doubles for the metadata provider."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import override

from app.internal.metadata.base import MetadataProvider
from app.internal.models import (
    Audiobook,
    BookMetadata,
    DirectSource,
    DownloadSource,
    TorrentSource,
)


class FakeMetadataProvider(MetadataProvider):
    @override
    async def search(
        self, query: str, num_results: int = 20, page: int = 0
    ) -> list[Audiobook]:
        return [
            Audiobook(
                source="livelib",
                external_id="work-fake-1",
                url="https://www.livelib.ru/book/1-fake",
                title=f"Fake {query}",
                subtitle=None,
                authors=["Fake Author"],
                narrators=[],
                cover_image=None,
                release_date=datetime(2021, 1, 1, tzinfo=UTC),
                runtime_length_min=600,
            )
        ]

    @override
    async def get_book(self, external_id: str) -> Audiobook | None:
        return None

    @override
    async def suggestions(self, query: str) -> list[str]:
        return ["Fake Suggestion"]

    @override
    async def similar(self, book: Audiobook) -> list[Audiobook]:
        return []

    @override
    async def categories(self) -> dict[str, list[Audiobook]]:
        return {}


def fake_provider(_session: object) -> MetadataProvider:
    return FakeMetadataProvider()


def fake_releases() -> list[DownloadSource]:
    """Offline release list used to exercise the release-based search path."""
    releases: list[DownloadSource] = [
        TorrentSource(
            protocol="torrent",
            guid="torrent:1",
            indexer_id=1,
            indexer="MockIndexer",
            title="Fake harry potter mp3",
            seeders=42,
            leechers=0,
            size=500_000_000,
            publish_date=datetime(2020, 1, 1, tzinfo=UTC),
            info_url=None,
            indexer_flags=[],
            download_url=None,
            magnet_url=None,
            book_metadata=BookMetadata(narrators=["Narrator A"]),
        ),
        DirectSource(
            protocol="direct",
            guid="https://knigavuhe.org/book/fake/",
            indexer_id=1000,
            indexer="knigavuhe",
            title="Fake harry potter — Reader B",
            size=0,
            publish_date=datetime(1970, 1, 1, tzinfo=UTC),
            info_url="https://knigavuhe.org/book/fake/",
            indexer_flags=[],
            download_urls=["https://s1.knigavuhe.org/1/audio/1/abc/01.mp3"],
            filetype="mp3",
            book_metadata=BookMetadata(narrators=["Reader B"]),
        ),
    ]
    return releases


async def fake_search_releases(
    *_args: object, **_kwargs: object
) -> list[DownloadSource]:
    return fake_releases()
