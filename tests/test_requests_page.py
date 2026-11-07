"""P12 step 3 — release-based requests page (grouped, state labels, actions)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlmodel import Session, select

from app.internal.models import (
    DownloadSource,
    Release,
    ReleaseRequest,
)
from tests.fakes import direct_source, torrent_source
from tests.http import json_object
from tests.mock_services import MockServer


def _bookmark(client: TestClient, source: DownloadSource) -> int:
    response = client.post(
        "/api/releases/requests", json=source.model_dump(mode="json")
    )
    assert response.status_code == 201, response.text
    return int(json_object(response)["id"])  # pyright: ignore[reportArgumentType]


def _release(db: Session, guid: str) -> Release | None:
    return db.exec(select(Release).where(Release.guid == guid)).first()


def _toast(response: Response) -> str:
    return response.headers.get("HX-Trigger", "")


def test_requests_page_empty(admin_client: TestClient) -> None:
    response = admin_client.get("/requests")
    assert response.status_code == 200, response.text
    assert "No releases in your requests yet" in response.text


def test_requests_grouped_by_title_and_author(admin_client: TestClient) -> None:
    _bookmark(admin_client, torrent_source(title="Test.Book.MP3"))
    _bookmark(admin_client, direct_source())

    response = admin_client.get("/requests")
    assert response.status_code == 200, response.text
    # One group: tracker row merged into the author-known knob.
    assert response.text.count('<details class="collapse') == 1
    assert "Test Book" in response.text
    assert "Mock Author" in response.text
    assert "2 releases" in response.text
    assert "Reader A" in response.text and "Reader B" in response.text


def test_requests_visibility_admin_vs_user(
    admin_client: TestClient, trusted_client: TestClient
) -> None:
    _bookmark(admin_client, torrent_source(guid="torrent:root", title="Root Book mp3"))
    _bookmark(
        trusted_client,
        direct_source(guid="https://knigavuhe.org/book/2/", book_title="Другая книга"),
    )

    as_admin = admin_client.get("/requests")
    assert "Root Book" in as_admin.text
    assert "Другая книга" in as_admin.text
    assert "requested by" in as_admin.text

    as_trusted = trusted_client.get("/requests")
    assert "Root Book" not in as_trusted.text
    assert "Другая книга" in as_trusted.text


def test_requests_download_action(admin_client: TestClient, aria2: MockServer) -> None:
    release_id = _bookmark(admin_client, direct_source())

    response = admin_client.post(f"/requests/hx-download/{release_id}")
    assert response.status_code == 200, response.text
    assert "Sent to download" in response.text
    assert len(aria2.state["aria2_params"]) == 1


def test_requests_remove_action(
    admin_client: TestClient, trusted_client: TestClient, db: Session
) -> None:
    release_id = _bookmark(trusted_client, direct_source())

    # A non-admin can only remove their own bookmark.
    assert admin_client.delete(f"/requests/hx-remove/{release_id}").status_code == 200
    assert db.exec(select(ReleaseRequest)).all() == []

    # Removing a missing request returns a toast instead of a 404 page.
    missing = trusted_client.delete(f"/requests/hx-remove/{release_id}")
    assert missing.status_code == 200
    assert "no longer exists" in missing.text


def test_requests_refresh_updates_from_source(
    admin_client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    release_id = _bookmark(admin_client, direct_source())
    fresh = direct_source(
        download_urls=["https://s2.knigavuhe.org/1/audio/1/xyz/01.mp3"]
    )

    async def _search(*_args: object, **_kwargs: object) -> list[DownloadSource]:
        return [fresh]

    monkeypatch.setattr("app.internal.search.search_releases", _search)

    response = admin_client.post(f"/requests/hx-refresh/{release_id}")
    assert response.status_code == 200, response.text
    assert "Release refreshed" in _toast(response)

    release = _release(db, fresh.guid)
    assert release is not None
    assert release.download_urls == fresh.download_urls


def test_requests_refresh_reports_missing_source(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    release_id = _bookmark(admin_client, direct_source())

    async def _search(*_args: object, **_kwargs: object) -> list[DownloadSource]:
        return []

    monkeypatch.setattr("app.internal.search.search_releases", _search)

    response = admin_client.post(f"/requests/hx-refresh/{release_id}")
    assert response.status_code == 200
    assert "no longer lists" in response.text


def test_requests_download_reports_client_error(admin_client: TestClient) -> None:
    release_id = _bookmark(admin_client, direct_source(download_urls=[]))

    response = admin_client.post(f"/requests/hx-download/{release_id}")
    assert response.status_code == 200
    assert "No downloadable files" in _toast(response)
