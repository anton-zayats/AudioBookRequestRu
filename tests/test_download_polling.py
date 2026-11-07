"""P9 — automatic download-status polling."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.internal.downloads.poller import (
    in_flight_release_ids,
    poll_download_states,
    poller_running,
    start_poller,
    stop_poller,
)
from app.internal.models import DownloadJob
from tests.fakes import direct_source, torrent_source
from tests.http import json_object
from tests.mock_services import MockServer


def _bookmark(client: TestClient, source: object) -> int:
    response = client.post(
        "/api/releases/requests",
        json=source.model_dump(mode="json"),  # pyright: ignore[reportAttributeAccessIssue]
    )
    assert response.status_code == 201, response.text
    return int(json_object(response)["id"])  # pyright: ignore[reportArgumentType]


def _job(db: Session, release_id: int) -> DownloadJob:
    job = db.exec(
        select(DownloadJob).where(DownloadJob.release_id == release_id)
    ).first()
    assert job is not None
    return job


def test_poll_without_jobs_returns_none_and_calls_nothing(
    aria2: MockServer, db: Session
) -> None:
    assert in_flight_release_ids(db) == []
    assert asyncio.run(poll_download_states()) is None
    assert aria2.requests_to("/jsonrpc") == []


def test_poll_completes_aria2_job_and_scans_abs(
    admin_client: TestClient, aria2: MockServer, abs_service: MockServer, db: Session
) -> None:
    release_id = _bookmark(admin_client, direct_source())
    assert admin_client.post(f"/requests/hx-download/{release_id}").status_code == 200
    assert db.exec(select(DownloadJob)).one().status == "downloading"

    result = asyncio.run(poll_download_states())

    assert result is not None
    assert result.completed == 1
    db.expire_all()
    assert _job(db, release_id).status == "completed"
    assert abs_service.requests_to("/abs/api/libraries/lib-1/scan")


def test_poll_completes_torrent_job(
    admin_client: TestClient,
    prowlarr: MockServer,
    torrent_client: MockServer,
    db: Session,
) -> None:
    release_id = _bookmark(admin_client, torrent_source(guid="torrent:1"))
    assert admin_client.post(f"/requests/hx-download/{release_id}").status_code == 200

    result = asyncio.run(poll_download_states())

    assert result is not None
    assert result.completed == 1
    db.expire_all()
    assert _job(db, release_id).status == "completed"


def test_poll_survives_download_client_errors(
    admin_client: TestClient,
    prowlarr: MockServer,
    torrent_client: MockServer,
    db: Session,
) -> None:
    release_id = _bookmark(admin_client, torrent_source(guid="torrent:1"))
    assert admin_client.post(f"/requests/hx-download/{release_id}").status_code == 200
    torrent_client.state["qbittorrent_login_status"] = 403

    result = asyncio.run(poll_download_states())

    assert result is not None
    assert result.error is not None
    db.expire_all()
    # The job stays in flight; the poller must not blow up.
    assert _job(db, release_id).status == "downloading"


def test_poller_start_and_stop_are_idempotent() -> None:
    async def run() -> None:
        stop_poller()
        assert poller_running() is False

        start_poller(interval_seconds=3600)
        start_poller(interval_seconds=3600)
        assert poller_running() is True

        stop_poller()
        stop_poller()
        assert poller_running() is False

    asyncio.run(run())
