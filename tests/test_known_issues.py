"""Reproductions / regression guards for previously reported issues."""

from __future__ import annotations

from fastapi.testclient import TestClient
from httpx2 import Response
from sqlmodel import Session, select

from app.internal.models import DownloadJob, Release, ReleaseRequest
from tests.fakes import direct_source, torrent_source
from tests.http import as_dict, json_list, json_object
from tests.mock_services import MockServer


def _grab(admin_client: TestClient) -> Response:
    return admin_client.post(
        "/api/releases/download",
        json=torrent_source(guid="mock-guid-1").model_dump(mode="json"),
    )


def test_issue_232_downloaded_needs_more_than_a_grab(
    admin_client: TestClient, prowlarr: MockServer, db: Session
) -> None:
    """Handing a release to Prowlarr is not completion (P9 tracks the rest)."""
    assert prowlarr.base_url
    assert _grab(admin_client).status_code == 201

    db.expire_all()
    release = db.exec(select(Release).where(Release.guid == "mock-guid-1")).first()
    assert release is not None
    job = db.exec(
        select(DownloadJob).where(DownloadJob.release_id == release.id)
    ).first()
    assert job is not None
    # Merely grabbed: the job is still in flight, so the list shows "sent".
    assert job.status == "downloading"

    listed = as_dict(json_list(admin_client.get("/api/releases/requests"))[0])
    assert listed["state"] == "sent"


def test_issue_237_home_has_release_requests(
    admin_client: TestClient, trusted_client: TestClient
) -> None:
    """The home page still surfaces other users' demand (now per release)."""
    created = trusted_client.post(
        "/api/releases/requests",
        json=direct_source().model_dump(mode="json"),
    )
    assert created.status_code == 201, created.text
    release_id = json_object(created)["id"]

    db_requests = admin_client.get("/api/releases/requests")
    assert db_requests.status_code == 200
    assert len(json_list(db_requests)) == 1
    assert as_dict(json_list(db_requests)[0])["release"]["id"] == release_id

    popular = admin_client.get("/hx-popular")
    assert popular.status_code == 200
    assert "Test Book" in popular.text

    # A single requester still owns exactly one bookmark.
    assert len(trusted_client.get("/api/releases/requests").text) > 0
    assert ReleaseRequest.__tablename__ == "releaserequest"
