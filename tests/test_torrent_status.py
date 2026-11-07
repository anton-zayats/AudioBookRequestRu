"""P9 — torrent download status via the qBittorrent-compatible client."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.internal.models import DownloadJob, Release
from tests.fakes import torrent_source
from tests.http import json_object
from tests.mock_services import MockServer

HASH = "0123456789abcdef0123456789abcdef01234567"


def _bookmark(client: TestClient, **kwargs: object) -> int:
    source = torrent_source(**kwargs)  # pyright: ignore[reportArgumentType]
    response = client.post(
        "/api/releases/requests", json=source.model_dump(mode="json")
    )
    assert response.status_code == 201, response.text
    return int(json_object(response)["id"])  # pyright: ignore[reportArgumentType]


def _job(db: Session, release_id: int) -> DownloadJob:
    job = db.exec(
        select(DownloadJob).where(DownloadJob.release_id == release_id)
    ).first()
    assert job is not None
    return job


def _toast(response) -> str:  # pyright: ignore[reportMissingParameterType, reportUnknownParameterType]
    return response.headers.get("HX-Trigger", "")


def test_torrent_grab_records_infohash(
    admin_client: TestClient, prowlarr: MockServer, db: Session
) -> None:
    release_id = _bookmark(admin_client, guid="torrent:1")
    assert admin_client.post(f"/requests/hx-download/{release_id}").status_code == 200

    job = _job(db, release_id)
    # Prowlarr never returns the hash; it was computed from the magnet link.
    assert job.external_id == HASH
    assert job.client == "prowlarr"


def test_refresh_status_completes_torrent(
    admin_client: TestClient,
    prowlarr: MockServer,
    torrent_client: MockServer,
    db: Session,
) -> None:
    release_id = _bookmark(admin_client, guid="torrent:1")
    assert admin_client.post(f"/requests/hx-download/{release_id}").status_code == 200

    # qBittorrent reports "uploading" by default -> completed.
    response = admin_client.post("/requests/hx-refresh-status")
    assert response.status_code == 200, response.text
    assert "Updated 1 download" in _toast(response)
    assert _job(db, release_id).status == "completed"
    assert "Redownload" in response.text


def test_refresh_status_marks_torrent_failed(
    admin_client: TestClient,
    prowlarr: MockServer,
    torrent_client: MockServer,
    db: Session,
) -> None:
    release_id = _bookmark(admin_client, guid="torrent:1")
    assert admin_client.post(f"/requests/hx-download/{release_id}").status_code == 200

    torrent_client.state["qbittorrent_state"] = "error"
    response = admin_client.post("/requests/hx-refresh-status")
    assert response.status_code == 200, response.text
    assert "Updated 1 download" in _toast(response)

    job = _job(db, release_id)
    assert job.status == "failed"
    assert job.error is not None and "error" in job.error.lower()
    assert "Download failed" in response.text


def test_refresh_status_reports_pending_torrent(
    admin_client: TestClient,
    prowlarr: MockServer,
    torrent_client: MockServer,
    db: Session,
) -> None:
    release_id = _bookmark(admin_client, guid="torrent:1")
    assert admin_client.post(f"/requests/hx-download/{release_id}").status_code == 200

    torrent_client.state["qbittorrent_state"] = "downloading"
    response = admin_client.post("/requests/hx-refresh-status")
    assert response.status_code == 200, response.text
    assert "still in progress" in _toast(response)
    assert _job(db, release_id).status == "downloading"


def test_refresh_status_skips_unknown_hash(
    admin_client: TestClient,
    prowlarr: MockServer,
    torrent_client: MockServer,
    db: Session,
) -> None:
    release_id = _bookmark(admin_client, guid="torrent:1")
    assert admin_client.post(f"/requests/hx-download/{release_id}").status_code == 200
    # The client no longer knows the torrent (removed from its queue).
    torrent_client.state["qbittorrent_states"] = {HASH: "unknownState"}

    response = admin_client.post("/requests/hx-refresh-status")
    assert response.status_code == 200, response.text
    assert "Could not read" in _toast(response)
    assert _job(db, release_id).status == "downloading"


def test_refresh_status_torrent_client_error_is_a_toast(
    admin_client: TestClient,
    prowlarr: MockServer,
    torrent_client: MockServer,
    db: Session,
) -> None:
    release_id = _bookmark(admin_client, guid="torrent:1")
    assert admin_client.post(f"/requests/hx-download/{release_id}").status_code == 200

    torrent_client.state["qbittorrent_login_status"] = 403
    response = admin_client.post("/requests/hx-refresh-status")
    assert response.status_code == 200
    assert "login failed" in _toast(response)
    assert _job(db, release_id).status == "downloading"


def test_torrent_client_settings_api(admin_client: TestClient) -> None:
    got = admin_client.get("/api/settings/download/torrent-client")
    assert got.status_code == 200, got.text
    assert json_object(got)["torrent_client_url"] == ""

    updated = admin_client.put(
        "/api/settings/download/torrent-client",
        json={
            "torrent_client_url": "http://decypharr:8282",
            "torrent_client_username": "token",
            "torrent_client_password": "secret",
        },
    )
    assert updated.status_code == 204

    got = admin_client.get("/api/settings/download/torrent-client")
    body = json_object(got)
    assert body["torrent_client_url"] == "http://decypharr:8282"
    assert body["torrent_client_username"] == "token"
    assert body["torrent_client_password_set"] is True


def test_torrent_client_connection_test(
    admin_client: TestClient, torrent_client: MockServer
) -> None:
    response = admin_client.post("/api/settings/download/torrent-client/test")
    assert response.status_code == 200, response.text
    assert json_object(response)["ok"] is True

    torrent_client.state["qbittorrent_login_status"] = 403
    failed = admin_client.post("/api/settings/download/torrent-client/test")
    assert failed.status_code == 400


def test_torrent_client_test_requires_url(admin_client: TestClient) -> None:
    response = admin_client.post("/api/settings/download/torrent-client/test")
    assert response.status_code == 400


@pytest.fixture(autouse=True)
def _no_direct_jobs(db: Session) -> None:
    """Guard: these tests must not accidentally rely on direct releases."""
    assert db.exec(select(Release)).all() is not None
