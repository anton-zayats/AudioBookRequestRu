"""P12 step 2 — release download / bookmark endpoints."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.internal.models import (
    BookMetadata,
    DirectSource,
    DownloadJob,
    Release,
    TorrentSource,
)
from tests.http import as_dict, json_list, json_object
from tests.mock_services import MockServer


def _direct_source(guid: str = "direct:1") -> DirectSource:
    return DirectSource(
        protocol="direct",
        guid=guid,
        indexer_id=1000,
        indexer="knigavuhe",
        title="Test Book mp3",
        size=0,
        publish_date=datetime(1970, 1, 1, tzinfo=UTC),
        info_url=None,
        indexer_flags=[],
        download_urls=["http://example.com/1.mp3"],
        filetype="mp3",
        book_metadata=BookMetadata(
            title="Test Book", authors=["Author"], narrators=["Narrator"]
        ),
    )


def _torrent_source(guid: str = "torrent:1") -> TorrentSource:
    return TorrentSource(
        protocol="torrent",
        guid=guid,
        indexer_id=1,
        indexer="MockIndexer",
        title="Test Book mp3",
        seeders=10,
        leechers=0,
        size=500_000_000,
        publish_date=datetime(2020, 1, 1, tzinfo=UTC),
        info_url=None,
        indexer_flags=[],
        download_url=None,
        magnet_url=None,
    )


def _get_release(db: Session, protocol: str, guid: str) -> Release | None:
    return db.exec(
        select(Release).where(Release.protocol == protocol, Release.guid == guid)
    ).first()


def test_download_direct_upserts_and_dispatches(
    admin_client: TestClient, aria2: MockServer, db: Session
) -> None:
    resp = admin_client.post(
        "/api/releases/download",
        json=_direct_source().model_dump(mode="json"),
    )
    assert resp.status_code == 201, resp.text

    release = _get_release(db, "direct", "direct:1")
    assert release is not None
    assert release.narrators == ["Narrator"]

    jobs = db.exec(
        select(DownloadJob).where(DownloadJob.release_id == release.id)
    ).all()
    assert len(jobs) == 1
    assert jobs[0].status == "downloading"

    assert len(aria2.state["aria2_params"]) == 1


def test_download_torrent_dispatches_to_prowlarr(
    admin_client: TestClient, prowlarr: MockServer, db: Session
) -> None:
    resp = admin_client.post(
        "/api/releases/download",
        json=_torrent_source().model_dump(mode="json"),
    )
    assert resp.status_code == 201, resp.text

    release = _get_release(db, "torrent", "torrent:1")
    assert release is not None

    grabs = prowlarr.requests_to("/prowlarr/api/v1/search")
    posts = [r for r in grabs if r["method"] == "POST"]
    assert len(posts) == 1
    assert json.loads(posts[0]["body"])["guid"] == "torrent:1"


def test_bookmark_list_remove(admin_client: TestClient) -> None:
    resp = admin_client.post(
        "/api/releases/requests",
        json=_torrent_source().model_dump(mode="json"),
    )
    assert resp.status_code == 201, resp.text
    release_id = json_object(resp)["id"]

    listed = admin_client.get("/api/releases/requests")
    assert listed.status_code == 200, listed.text
    items = json_list(listed)
    assert len(items) == 1
    assert as_dict(as_dict(items[0])["release"])["id"] == release_id

    removed = admin_client.delete(f"/api/releases/requests/{release_id}")
    assert removed.status_code == 204

    assert json_list(admin_client.get("/api/releases/requests")) == []


def test_download_bookmarked_release(
    admin_client: TestClient, aria2: MockServer
) -> None:
    resp = admin_client.post(
        "/api/releases/requests",
        json=_direct_source().model_dump(mode="json"),
    )
    assert resp.status_code == 201, resp.text
    release_id = json_object(resp)["id"]

    dl = admin_client.post(f"/api/releases/requests/{release_id}/download")
    assert dl.status_code == 201, dl.text
    assert len(aria2.state["aria2_params"]) == 1


def test_remove_missing_request_404(admin_client: TestClient) -> None:
    assert admin_client.delete("/api/releases/requests/999999").status_code == 404
