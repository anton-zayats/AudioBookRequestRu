"""Shared offline test doubles for the metadata provider."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import override

from app.internal.metadata.base import MetadataProvider
from app.internal.models import Audiobook


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
