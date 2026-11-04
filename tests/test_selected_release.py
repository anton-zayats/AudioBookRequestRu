"""Phase 6 — release/narrator selection persistence and dispatch."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import cast

import aiohttp
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.internal.db_queries import get_wishlist_results
from app.internal.models import (
    AudiobookRequest,
    BookMetadata,
    DirectSource,
    SelectedRelease,
    TorrentSource,
)
from app.internal.query import query_sources
from tests.constants import BOOK_ID
from tests.mock_services import MockServer


def _direct(
    guid: str,
    url: str,
    *,
    title: str = "Test Book mp3",
    narrators: list[str] | None = None,
    size: int = 500_000_000,
) -> DirectSource:
    return DirectSource(
        protocol="direct",
        guid=guid,
        indexer_id=999,
        indexer="knigavuhe",
        title=title,
        size=size,
        publish_date=datetime(1970, 1, 1, tzinfo=UTC),
        info_url=None,
        indexer_flags=[],
        download_urls=[url],
        filetype="mp3",
        book_metadata=BookMetadata(narrators=narrators or []),
    )


def _torrent(
    guid: str,
    *,
    seeders: int = 42,
    leechers: int = 0,
) -> TorrentSource:
    return TorrentSource(
        protocol="torrent",
        guid=guid,
        indexer_id=1,
        indexer="MockIndexer",
        title="Test Book mp3",
        seeders=seeders,
        leechers=leechers,
        size=500_000_000,
        publish_date=datetime(2020, 1, 1, tzinfo=UTC),
        info_url=None,
        indexer_flags=[],
        download_url=None,
        magnet_url=None,
    )


def _add_uri(aria2: MockServer, index: int) -> tuple[str, list[str], dict[str, object]]:
    entry = aria2.state["aria2_params"][index]
    params = cast(list[object], entry["params"])
    return (
        cast(str, params[0]),
        cast(list[str], params[1]),
        cast(dict[str, object], params[2]),
    )


# -- persistence ----------------------------------------------------------
def test_create_request_persists_selected_release(
    admin_client: TestClient, db: Session
) -> None:
    selected = SelectedRelease(
        protocol="direct",
        guid="direct:test:1",
        title="Chosen release",
        narrators=["Narrator A"],
    )
    response = admin_client.post(
        f"/api/requests/{BOOK_ID}", json=selected.model_dump(mode="json")
    )
    assert response.status_code == 200, response.text

    stored = db.get(AudiobookRequest, (BOOK_ID, "root"))
    assert stored is not None
    assert stored.selected_release == selected


def test_patch_release_sets_selection(trusted_client: TestClient, db: Session) -> None:
    selected = SelectedRelease(
        protocol="usenet",
        guid="usenet:1",
        narrators=["Narrator B"],
    )
    response = trusted_client.patch(
        f"/api/requests/{BOOK_ID}/release",
        json={"selected_release": selected.model_dump(mode="json")},
    )
    assert response.status_code == 204, response.text

    stored = db.get(AudiobookRequest, (BOOK_ID, "trusted"))
    assert stored is not None
    assert stored.selected_release == selected


def test_patch_release_clears_selection(
    trusted_client: TestClient, db: Session
) -> None:
    stored = db.get(AudiobookRequest, (BOOK_ID, "trusted"))
    assert stored is not None
    stored.selected_release = SelectedRelease(protocol="direct", guid="direct:stale")
    db.add(stored)
    db.commit()

    response = trusted_client.patch(
        f"/api/requests/{BOOK_ID}/release", json={"selected_release": None}
    )
    assert response.status_code == 204, response.text

    db.expire_all()
    stored = db.get(AudiobookRequest, (BOOK_ID, "trusted"))
    assert stored is not None
    assert stored.selected_release is None


def test_hx_select_creates_request_with_selection(
    admin_client: TestClient, db: Session
) -> None:
    response = admin_client.post(
        f"/request/hx-select/{BOOK_ID}",
        data={
            "protocol": "direct",
            "guid": "direct:select:1",
            "title": "Selected title",
            "narrators": "A, B",
        },
    )
    assert response.status_code == 204, response.text
    assert response.headers["HX-Redirect"] == "/wishlist"

    stored = db.get(AudiobookRequest, (BOOK_ID, "root"))
    assert stored is not None
    assert stored.selected_release == SelectedRelease(
        protocol="direct",
        guid="direct:select:1",
        title="Selected title",
        narrators=["A", "B"],
    )


# -- dispatch -------------------------------------------------------------
def test_query_sources_dispatches_selected_torrent(
    prowlarr: MockServer,
    mock_server: MockServer,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert prowlarr.base_url

    async def _sources(*_args: object, **_kwargs: object) -> list[TorrentSource]:
        # guid-2 has fewer seeders, so it is *not* the top-ranked source.
        return [_torrent("guid-1", seeders=42), _torrent("guid-2", seeders=10)]

    async def _no_direct(*_args: object, **_kwargs: object) -> list[DirectSource]:
        return []

    monkeypatch.setattr("app.internal.query.query_prowlarr", _sources)
    monkeypatch.setattr("app.internal.query.search_direct_sources", _no_direct)

    selected = SelectedRelease(protocol="torrent", guid="guid-2")

    async def run() -> None:
        async with aiohttp.ClientSession() as client_session:
            result = await query_sources(
                book_ref=str(BOOK_ID),
                session=db,
                client_session=client_session,
                start_auto_download=True,
                selected_release=selected,
            )
            assert result.ok

    asyncio.run(run())

    grabs = mock_server.requests_to("/prowlarr/api/v1/search")
    posts = [request for request in grabs if request["method"] == "POST"]
    assert len(posts) == 1
    assert json.loads(posts[0]["body"])["guid"] == "guid-2"


def test_query_sources_dispatches_selected_direct(
    aria2: MockServer,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _no_prowlarr(*_args: object, **_kwargs: object) -> list[object]:
        return []

    async def _sources(*_args: object, **_kwargs: object) -> list[DirectSource]:
        return [
            _direct("direct:1", "http://example.com/1.mp3"),
            _direct("direct:2", "http://example.com/2.mp3"),
        ]

    monkeypatch.setattr("app.internal.query.query_prowlarr", _no_prowlarr)
    monkeypatch.setattr("app.internal.query.search_direct_sources", _sources)

    selected = SelectedRelease(protocol="direct", guid="direct:2")

    async def run() -> None:
        async with aiohttp.ClientSession() as client_session:
            result = await query_sources(
                book_ref=str(BOOK_ID),
                session=db,
                client_session=client_session,
                start_auto_download=True,
                selected_release=selected,
            )
            assert result.ok

    asyncio.run(run())

    assert len(aria2.state["aria2_params"]) == 1
    _token, urls, _options = _add_uri(aria2, 0)
    assert urls == ["http://example.com/2.mp3"]


def test_query_sources_falls_back_to_top_ranked(
    aria2: MockServer,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _no_prowlarr(*_args: object, **_kwargs: object) -> list[object]:
        return []

    async def _sources(*_args: object, **_kwargs: object) -> list[DirectSource]:
        return [
            _direct("direct:1", "http://example.com/1.mp3"),
            _direct("direct:2", "http://example.com/2.mp3"),
        ]

    monkeypatch.setattr("app.internal.query.query_prowlarr", _no_prowlarr)
    monkeypatch.setattr("app.internal.query.search_direct_sources", _sources)

    selected = SelectedRelease(protocol="direct", guid="direct:missing")

    async def run() -> None:
        async with aiohttp.ClientSession() as client_session:
            result = await query_sources(
                book_ref=str(BOOK_ID),
                session=db,
                client_session=client_session,
                start_auto_download=True,
                selected_release=selected,
            )
            assert result.ok

    asyncio.run(run())

    assert len(aria2.state["aria2_params"]) == 1
    _token, urls, _options = _add_uri(aria2, 0)
    assert urls == ["http://example.com/1.mp3"]


# -- Prowlarr optional ----------------------------------------------------
def test_query_sources_works_without_prowlarr(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _sources(
        _session: Session, _client_session: aiohttp.ClientSession, _book: object
    ) -> list[DirectSource]:
        return [_direct("direct:only:1", "http://example.com/only.mp3")]

    monkeypatch.setattr("app.internal.query.search_direct_sources", _sources)

    async def run() -> list[str]:
        async with aiohttp.ClientSession() as client_session:
            result = await query_sources(
                book_ref=str(BOOK_ID),
                session=db,
                client_session=client_session,
            )
            assert result.ok
            assert result.sources is not None
            return [source.guid for source in result.sources]

    assert asyncio.run(run()) == ["direct:only:1"]


# -- chooser page ---------------------------------------------------------
def test_releases_page_renders(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _no_prowlarr(*_args: object, **_kwargs: object) -> list[object]:
        return []

    async def _sources(
        _session: Session, _client_session: aiohttp.ClientSession, _book: object
    ) -> list[DirectSource]:
        return [
            _direct(
                "direct:choose:1",
                "http://example.com/choose.mp3",
                narrators=["Ann Narrator"],
            )
        ]

    monkeypatch.setattr("app.internal.query.query_prowlarr", _no_prowlarr)
    monkeypatch.setattr("app.internal.query.search_direct_sources", _sources)

    response = admin_client.get(f"/request/{BOOK_ID}/releases")
    assert response.status_code == 200, response.text
    assert "Choose a release for Test Book" in response.text
    assert "Ann Narrator" in response.text
    assert "Request this release" in response.text
    assert "Request without choosing a release" in response.text

    body = admin_client.get(f"/request/{BOOK_ID}/releases?only_body=true")
    assert body.status_code == 200, body.text
    assert 'id="releases"' in body.text


# -- wishlist auto-download honors the saved release ----------------------
def test_auto_download_endpoint_honors_selected_release(
    admin_client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored = db.get(AudiobookRequest, (BOOK_ID, "trusted"))
    assert stored is not None
    stored.selected_release = SelectedRelease(
        protocol="direct", guid="direct:auto:1", narrators=["Auto Narrator"]
    )
    db.add(stored)
    db.commit()

    captured: dict[str, object] = {}

    async def _fake_query(*_args: object, **kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("app.routers.api.requests.query_sources", _fake_query)

    response = admin_client.post(f"/api/requests/{BOOK_ID}/auto-download")
    assert response.status_code == 204, response.text

    assert captured.get("start_auto_download") is True
    assert captured.get("selected_release") == SelectedRelease(
        protocol="direct", guid="direct:auto:1", narrators=["Auto Narrator"]
    )


def test_wishlist_result_exposes_selected_release(db: Session) -> None:
    stored = db.get(AudiobookRequest, (BOOK_ID, "trusted"))
    assert stored is not None
    stored.selected_release = SelectedRelease(
        protocol="direct", guid="direct:prop:1", narrators=["Prop Narrator"]
    )
    db.add(stored)
    db.commit()

    results = get_wishlist_results(db, None, "not_downloaded")
    result = next(r for r in results if r.book.id == BOOK_ID)
    assert result.selected_release is not None
    assert result.selected_release.narrators == ["Prop Narrator"]
