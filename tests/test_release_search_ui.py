"""P12 steps 1-2 — search page: persistence, filter, row actions, in-library badge."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.internal.models import (
    BookMetadata,
    DirectSource,
    DownloadJob,
    DownloadSource,
    Release,
    ReleaseRequest,
    TorrentSource,
)
from tests.http import as_dict, as_list, json_list, json_object
from tests.mock_services import MockServer


def _torrent(
    guid: str = "torrent:1",
    title: str = "Test Book mp3",
    authors: list[str] | None = None,
) -> TorrentSource:
    return TorrentSource(
        protocol="torrent",
        guid=guid,
        indexer_id=1,
        indexer="MockIndexer",
        title=title,
        seeders=10,
        leechers=0,
        size=500_000_000,
        publish_date=datetime(2020, 1, 1, tzinfo=UTC),
        info_url=None,
        indexer_flags=[],
        download_url=None,
        magnet_url=None,
        book_metadata=BookMetadata(
            title=None, authors=authors or [], narrators=["Reader A"]
        ),
    )


def _direct(guid: str = "https://knigavuhe.org/book/1/") -> DirectSource:
    return DirectSource(
        protocol="direct",
        guid=guid,
        indexer_id=1000,
        indexer="knigavuhe",
        title="Test Book — Reader B",
        size=0,
        publish_date=datetime(1970, 1, 1, tzinfo=UTC),
        info_url=guid,
        indexer_flags=[],
        download_urls=["https://s1.knigavuhe.org/1/audio/1/abc/01.mp3"],
        filetype="mp3",
        book_metadata=BookMetadata(
            title="Test Book", authors=["Mock Author"], narrators=["Reader B"]
        ),
    )


def _fake_search(releases: list[DownloadSource]):
    async def _search(*_args: object, **_kwargs: object) -> list[DownloadSource]:
        return releases

    return _search


def _release(db: Session, guid: str) -> Release | None:
    return db.exec(select(Release).where(Release.guid == guid)).first()


def test_search_page_persists_releases_and_renders_actions(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch, db: Session
) -> None:
    monkeypatch.setattr(
        "app.routers.api.search.search_releases", _fake_search([_torrent(), _direct()])
    )

    response = admin_client.get("/search", params={"q": "test book"})
    assert response.status_code == 200, response.text
    assert "Add to requests" in response.text
    assert "Download" in response.text

    assert _release(db, "torrent:1") is not None
    # The provider's clean book title wins over the noisy upload title.
    direct = _release(db, "https://knigavuhe.org/book/1/")
    assert direct is not None
    assert direct.title == "Test Book"


def test_search_page_keeps_selected_source_filter(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    async def _search(*_args: object, **kwargs: object) -> list[DownloadSource]:
        seen.update(kwargs)
        return []

    monkeypatch.setattr("app.routers.api.search.search_releases", _search)

    response = admin_client.get("/search", params={"q": "book", "source": "direct"})
    assert response.status_code == 200, response.text
    assert seen["source"] == "direct"
    assert re.search(r'value="direct"[^>]*selected', response.text)


def test_download_row_dispatches_and_shows_state(
    admin_client: TestClient,
    aria2: MockServer,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.routers.api.search.search_releases", _fake_search([_direct()])
    )
    assert admin_client.get("/search", params={"q": "test book"}).status_code == 200

    release = _release(db, "https://knigavuhe.org/book/1/")
    assert release is not None and release.id is not None

    response = admin_client.post(f"/search/hx-download/{release.id}")
    assert response.status_code == 200, response.text
    assert "Sent to download" in response.text
    assert len(aria2.state["aria2_params"]) == 1

    # Sending to download also keeps the release in the requests list.
    bookmarks = db.exec(select(ReleaseRequest)).all()
    assert [bookmark.release_id for bookmark in bookmarks] == [release.id]


def test_toggle_request_row(
    admin_client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.routers.api.search.search_releases", _fake_search([_torrent()])
    )
    assert admin_client.get("/search", params={"q": "test book"}).status_code == 200

    release = _release(db, "torrent:1")
    assert release is not None and release.id is not None

    added = admin_client.post(f"/search/hx-toggle-request/{release.id}")
    assert added.status_code == 200, added.text
    assert "Remove from requests" in added.text
    assert db.get(ReleaseRequest, (release.id, "root")) is not None

    removed = admin_client.post(f"/search/hx-toggle-request/{release.id}")
    assert removed.status_code == 200, removed.text
    assert "Add to requests" in removed.text
    assert db.get(ReleaseRequest, (release.id, "root")) is None


def test_in_library_badge(
    admin_client: TestClient,
    abs_service: MockServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = abs_service
    monkeypatch.setattr(
        "app.routers.api.search.search_releases",
        _fake_search([_torrent(title="Test.Book.MP3.64kbps")]),
    )

    response = admin_client.get("/search", params={"q": "test book"})
    assert response.status_code == 200, response.text
    assert "In library" in response.text


def test_missing_release_shows_toast(admin_client: TestClient) -> None:
    response = admin_client.post("/search/hx-download/999999")
    assert response.status_code == 200
    assert "no longer available" in response.text


# -- requests listing ------------------------------------------------------
def test_requests_list_is_admin_wide(
    admin_client: TestClient, trusted_client: TestClient
) -> None:
    assert (
        admin_client.post(
            "/api/releases/requests", json=_torrent().model_dump(mode="json")
        ).status_code
        == 201
    )
    assert (
        trusted_client.post(
            "/api/releases/requests", json=_direct().model_dump(mode="json")
        ).status_code
        == 201
    )

    as_admin = json_list(admin_client.get("/api/releases/requests"))
    assert len(as_admin) == 2
    requesters = {
        as_dict(as_dict(item)["release"])["guid"]: as_dict(item)["requested_by"]
        for item in as_admin
    }
    assert requesters["torrent:1"] == ["root"]
    assert requesters["https://knigavuhe.org/book/1/"] == ["trusted"]
    assert all(as_dict(item)["state"] == "not_downloaded" for item in as_admin)

    as_trusted = json_list(trusted_client.get("/api/releases/requests"))
    assert len(as_trusted) == 1
    assert as_dict(as_trusted[0])["requested_by"] == ["trusted"]


def test_requests_list_reports_download_state(
    admin_client: TestClient, aria2: MockServer
) -> None:
    created = admin_client.post(
        "/api/releases/requests", json=_direct().model_dump(mode="json")
    )
    assert created.status_code == 201, created.text
    release_id = json_object(created)["id"]

    assert (
        admin_client.post(f"/api/releases/requests/{release_id}/download").status_code
        == 201
    )

    listed = as_list(json_list(admin_client.get("/api/releases/requests")))
    assert len(listed) == 1
    assert as_dict(listed[0])["state"] == "sent"


# -- UX fixes: sample handling, pagination, button state -------------------
def _sample_source() -> DirectSource:
    source = _direct(guid="https://knigavuhe.org/book/sample/")
    source.indexer_flags = ["sample"]
    return source


def _next_button(html: str) -> str:
    index = html.index('title="Next page"')
    return html[index : html.index("</button>", index)]


def test_sample_only_row_disables_both_actions(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.routers.api.search.search_releases", _fake_search([_sample_source()])
    )
    response = admin_client.get("/search", params={"q": "test book"})
    assert response.status_code == 200, response.text
    html = response.text

    # Short badge, not "Sample only"
    assert ">Sample<" in html.replace("\n", "").replace(" ", "")
    assert "Sample only" not in html
    assert 'hx-post="/search/hx-download/' in html
    assert 'hx-post="/search/hx-toggle-request/' in html
    assert html.count("disabled") >= 2


def test_sample_only_release_cannot_be_bookmarked(
    admin_client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.routers.api.search.search_releases", _fake_search([_sample_source()])
    )
    assert admin_client.get("/search", params={"q": "test book"}).status_code == 200
    release = db.exec(
        select(Release).where(Release.guid == "https://knigavuhe.org/book/sample/")
    ).first()
    assert release is not None and release.id is not None

    response = admin_client.post(f"/search/hx-toggle-request/{release.id}")
    assert response.status_code == 200
    assert "cannot be requested" in response.text
    assert db.get(ReleaseRequest, (release.id, "root")) is None

    api = admin_client.post(
        "/api/releases/requests",
        json=_sample_source().model_dump(mode="json"),
    )
    assert api.status_code == 400


def test_search_row_has_no_requested_by_annotation(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.routers.api.search.search_releases", _fake_search([_direct()])
    )
    assert admin_client.get("/search", params={"q": "test book"}).status_code == 200

    response = admin_client.get("/search", params={"q": "test book"})
    assert response.status_code == 200
    assert "Requested by" not in response.text


def test_pagination_next_disabled_past_last_page(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    many = [_torrent(guid=f"torrent:{i}", title=f"Book {i} mp3") for i in range(21)]

    async def _search(*_args: object, **_kwargs: object) -> list[DownloadSource]:
        # search_releases sees no pagination; the API slices the full list.
        return many

    monkeypatch.setattr("app.routers.api.search.search_releases", _search)

    first = admin_client.get("/search", params={"q": "book"})
    assert first.status_code == 200
    assert "disabled" not in _next_button(first.text)

    # The fake returns the whole list, so page 2 (21-40) is empty: next must
    # be disabled and the empty state must say "No more results".
    second = admin_client.get("/search", params={"q": "book", "page": 1})
    assert second.status_code == 200
    assert "disabled" in _next_button(second.text)
    assert "No more results" in second.text


def test_download_button_locks_while_downloading(
    admin_client: TestClient,
    aria2: MockServer,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.routers.api.search.search_releases", _fake_search([_direct()])
    )
    assert admin_client.get("/search", params={"q": "test book"}).status_code == 200
    release = _release(db, "https://knigavuhe.org/book/1/")
    assert release is not None and release.id is not None

    started = admin_client.post(f"/search/hx-download/{release.id}")
    assert started.status_code == 200
    assert "Downloading" in started.text
    assert 'hx-post="/search/hx-download/' in started.text
    assert "disabled" in started.text

    # Once the job is terminal the button offers a redownload.
    job = db.exec(
        select(DownloadJob).where(DownloadJob.release_id == release.id)
    ).first()
    assert job is not None
    job.status = "completed"
    db.add(job)
    db.commit()

    refreshed = admin_client.get("/search", params={"q": "test book"})
    assert refreshed.status_code == 200
    assert "Redownload" in refreshed.text
