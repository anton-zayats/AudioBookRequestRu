"""P8 — persisted release results and the re-request flow."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import aiohttp
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.internal.db_queries import (
    downloaded_narrators,
    load_release_snapshot,
    save_release_snapshot,
)
from app.internal.models import (
    Audiobook,
    AudiobookRequest,
    BookMetadata,
    DirectSource,
    SelectedRelease,
)
from app.internal.query import query_sources
from app.util.normalize import narrators_overlap
from tests.constants import BOOK_ID


def _direct(
    guid: str,
    url: str,
    *,
    narrators: list[str] | None = None,
    title: str = "Test Book mp3",
) -> DirectSource:
    return DirectSource(
        protocol="direct",
        guid=guid,
        indexer_id=999,
        indexer="knigavuhe",
        title=title,
        size=500_000_000,
        publish_date=datetime(1970, 1, 1, tzinfo=UTC),
        info_url=None,
        indexer_flags=[],
        download_urls=[url],
        filetype="mp3",
        book_metadata=BookMetadata(narrators=narrators or []),
    )


# -- snapshot persistence ------------------------------------------------
def test_snapshot_roundtrip(db: Session) -> None:
    sources = [
        _direct(
            "direct:1", "http://example.com/1.mp3", narrators=["Александр Клюквин"]
        ),
        _direct("direct:2", "http://example.com/2.mp3", narrators=["Михаил Ефремов"]),
    ]
    save_release_snapshot(db, BOOK_ID, sources)

    loaded = load_release_snapshot(db, BOOK_ID)
    assert loaded is not None
    assert [source.guid for source in loaded] == ["direct:1", "direct:2"]
    assert loaded[0].book_metadata.narrators == ["Александр Клюквин"]


def test_snapshot_empty_list_is_not_none(db: Session) -> None:
    save_release_snapshot(db, BOOK_ID, [])
    assert load_release_snapshot(db, BOOK_ID) == []


def test_query_sources_prefers_snapshot(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_release_snapshot(
        db,
        BOOK_ID,
        [_direct("direct:saved", "http://example.com/saved.mp3", narrators=["N"])],
    )

    async def _boom(*_args: object, **_kwargs: object) -> list[object]:
        raise AssertionError("live query should not run when a snapshot exists")

    monkeypatch.setattr("app.internal.query.query_prowlarr", _boom)
    monkeypatch.setattr("app.internal.query.search_direct_sources", _boom)

    async def run() -> list[str]:
        async with aiohttp.ClientSession() as client_session:
            result = await query_sources(
                book_ref=str(BOOK_ID),
                session=db,
                client_session=client_session,
                prefer_snapshot=True,
            )
            assert result.ok
            assert result.sources is not None
            return [source.guid for source in result.sources]

    assert asyncio.run(run()) == ["direct:saved"]


# -- re-request (choose another release) ---------------------------------
def test_re_request_updates_selection(admin_client: TestClient, db: Session) -> None:
    assert admin_client.post(f"/api/requests/{BOOK_ID}").status_code == 200
    assert admin_client.patch(f"/api/requests/{BOOK_ID}/downloaded").status_code == 204

    selected = SelectedRelease(
        protocol="direct",
        guid="direct:again:1",
        narrators=["New Narrator"],
    )
    response = admin_client.post(
        f"/api/requests/{BOOK_ID}", json=selected.model_dump(mode="json")
    )
    assert response.status_code == 200, response.text

    stored = db.get(AudiobookRequest, (BOOK_ID, "root"))
    assert stored is not None
    assert stored.selected_release == selected

    book = db.get(Audiobook, BOOK_ID)
    assert book is not None
    assert book.downloaded is False


# -- narrator awareness ---------------------------------------------------
def test_downloaded_narrators_only_when_downloaded(db: Session) -> None:
    assert downloaded_narrators(db, BOOK_ID) == []

    stored = db.get(AudiobookRequest, (BOOK_ID, "trusted"))
    assert stored is not None
    stored.selected_release = SelectedRelease(
        protocol="direct", guid="direct:x", narrators=["Александр Клюквин"]
    )
    book = db.get(Audiobook, BOOK_ID)
    assert book is not None
    book.downloaded = True
    db.add(stored)
    db.add(book)
    db.commit()

    assert downloaded_narrators(db, BOOK_ID) == ["Александр Клюквин"]


def test_narrators_overlap() -> None:
    assert narrators_overlap(["Александр Клюквин"], ["александр клюквин"]) is True
    assert narrators_overlap(["Михаил Ефремов"], ["Александр Клюквин"]) is False
    assert narrators_overlap([], ["Александр Клюквин"]) is False
