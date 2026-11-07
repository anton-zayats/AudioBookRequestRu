"""Release dispatch tests: aria2 (direct) and Prowlarr (torrent) clients."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import cast

import aiohttp
from sqlmodel import Session

from app.internal.downloads.aria2 import Aria2Client
from app.internal.downloads.config import download_client_config
from app.internal.models import (
    Audiobook,
    DirectSource,
    DownloadJob,
    DownloadSource,
    EventEnum,
    Notification,
    NotificationBodyTypeEnum,
    TorrentSource,
)
from app.internal.prowlarr.util import prowlarr_config
from app.internal.ranking.download_ranking import rank_sources
from app.internal.releases import download_release, upsert_release
from tests.constants import BOOK_ID
from tests.mock_services import MockServer


def _direct(**overrides: object) -> DirectSource:
    defaults: dict[str, object] = {
        "guid": "direct:test:1",
        "indexer_id": 999,
        "indexer": "knigavuhe",
        "title": "Test Book",
        "size": 0,
        "publish_date": datetime(1970, 1, 1, tzinfo=UTC),
        "info_url": None,
        "indexer_flags": [],
        "download_urls": ["http://example.com/1.mp3"],
        "filetype": "mp3",
    }
    defaults.update(overrides)
    return DirectSource.model_validate(defaults)


def _add_uri(aria2: MockServer, index: int) -> tuple[str, list[str], dict[str, object]]:
    entry = aria2.state["aria2_params"][index]
    params = cast(list[object], entry["params"])
    return (
        cast(str, params[0]),
        cast(list[str], params[1]),
        cast(dict[str, object], params[2]),
    )


def test_aria2_test_connection(aria2: MockServer, db: Session) -> None:
    assert aria2.base_url
    host = download_client_config.get_host(db)
    port = download_client_config.get_port(db)
    secret = download_client_config.get_rpc_secret(db)
    assert host is not None

    async def run() -> bool:
        async with aiohttp.ClientSession() as client_session:
            client = Aria2Client(
                host=host,
                port=port,
                secret=secret,
                client_session=client_session,
            )
            return await client.test_connection()

    assert asyncio.run(run()) is True


def test_download_direct_release_enqueues_and_notifies(
    aria2: MockServer,
    abs_service: MockServer,
    mock_server: MockServer,
    db: Session,
) -> None:
    db.add(
        Notification(
            name="ok",
            url=f"{mock_server.base_url}/notify/success",
            event=EventEnum.on_successful_download,
            body_type=NotificationBodyTypeEnum.text,
            body="downloaded {sourceProtocol}",
            enabled=True,
        )
    )
    db.commit()

    source = _direct(
        download_urls=[
            "http://example.com/a.mp3",
            "http://example.com/b.mp3",
        ],
    )
    release = upsert_release(db, source)
    assert release.id is not None

    async def run() -> DownloadJob:
        async with aiohttp.ClientSession() as client_session:
            return await download_release(db, client_session, release, "root")

    job = asyncio.run(run())

    assert job.status == "downloading"
    assert job.external_id == "mock-gid-1,mock-gid-2"
    assert len(aria2.state["aria2_params"]) == 2

    first_token, first_urls, first_options = _add_uri(aria2, 0)
    _second_token, second_urls, second_options = _add_uri(aria2, 1)
    assert first_token == "token:mock-secret"
    assert first_urls == ["http://example.com/a.mp3"]
    assert second_urls == ["http://example.com/b.mp3"]
    # Direct sources have no authors, so the folder falls back to "Unknown Author"
    assert first_options["dir"] == "/downloads/Unknown Author - Test Book"
    assert first_options["out"] == "a.mp3"
    assert second_options["out"] == "b.mp3"

    assert abs_service.requests_to("/abs/api/libraries/lib-1/scan")
    assert any(
        b"direct" in request["body"]
        for request in mock_server.requests_to("/notify/success")
    )


def test_direct_source_ranks_first(db: Session) -> None:
    prowlarr_config.set_api_key(db, "unused")
    book = db.get(Audiobook, BOOK_ID)
    assert book is not None

    direct = _direct(
        title="Test Book flac",
        size=500_000_000,
        download_urls=["http://example.com/1.flac"],
        filetype="flac",
    )
    torrent = TorrentSource(
        protocol="torrent",
        guid="mock-guid-1",
        indexer_id=1,
        indexer="MockIndexer",
        title="Test Book mp3",
        seeders=42,
        leechers=0,
        size=500_000_000,
        info_url=None,
        indexer_flags=[],
        publish_date=datetime(2020, 1, 1, tzinfo=UTC),
    )

    async def run() -> list[DownloadSource]:
        async with aiohttp.ClientSession() as client_session:
            return await rank_sources(
                db, client_session, [torrent, direct], book, is_manual=False
            )

    ranked = asyncio.run(run())
    assert ranked[0].protocol == "direct"
    assert ranked[0].guid == direct.guid


def test_direct_seeders_are_ignored(db: Session) -> None:
    prowlarr_config.set_api_key(db, "unused")
    book = db.get(Audiobook, BOOK_ID)
    assert book is not None

    direct = _direct(
        title="Test Book mp3",
        size=500_000_000,
        download_urls=["http://example.com/1.mp3"],
        filetype="mp3",
    )
    torrent = TorrentSource(
        protocol="torrent",
        guid="mock-guid-1",
        indexer_id=1,
        indexer="MockIndexer",
        title="Test Book mp3",
        seeders=100,
        leechers=0,
        size=500_000_000,
        info_url=None,
        indexer_flags=[],
        publish_date=datetime(2020, 1, 1, tzinfo=UTC),
    )

    async def run() -> list[DownloadSource]:
        async with aiohttp.ClientSession() as client_session:
            return await rank_sources(
                db, client_session, [direct, torrent], book, is_manual=False
            )

    ranked = asyncio.run(run())
    # Seeders are never compared for direct sources, so the stable input order
    # is preserved (the high-seeder torrent does not displace the valid direct).
    assert ranked[0].protocol == "direct"
    assert ranked[1].protocol == "torrent"
