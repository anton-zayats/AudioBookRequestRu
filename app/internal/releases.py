"""Release persistence and download dispatch (P12 target model).

A ``Release`` is a persisted :class:`~app.internal.models.DownloadSource` found
via search. This module handles upserting releases, bookmarking them
(:class:`~app.internal.models.ReleaseRequest`), and dispatching a download to
the right client while recording a :class:`~app.internal.models.DownloadJob`.
"""

from __future__ import annotations

import posixpath
import re
from datetime import datetime
from urllib.parse import urlparse

from aiohttp import ClientSession
from sqlmodel import Session, desc, select

from app.internal.audiobookshelf.client import abs_trigger_scan
from app.internal.audiobookshelf.config import abs_config
from app.internal.downloads.aria2 import Aria2Client
from app.internal.downloads.client import DownloadFile
from app.internal.downloads.config import (
    DownloadClientMisconfigured,
    download_client_config,
)
from app.internal.models import (
    DownloadJob,
    DownloadSource,
    Release,
    ReleaseRequest,
    TorrentSource,
    UsenetSource,
)
from app.internal.prowlarr.prowlarr import start_download
from app.util.log import logger


def _sanitize_segment(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or "download"


def _filename_from_url(url: str) -> str | None:
    path = urlparse(url).path
    name = path.rsplit("/", 1)[-1] if path else ""
    name = name.strip()
    if not name:
        return None
    return _sanitize_segment(name)


def release_from_source(source: DownloadSource) -> Release:
    """Map a transient ``DownloadSource`` to a persisted ``Release`` (no id)."""
    if isinstance(source, TorrentSource):
        return Release(
            protocol="torrent",
            guid=source.guid,
            title=source.title,
            authors=source.book_metadata.authors,
            narrators=source.book_metadata.narrators,
            indexer_id=source.indexer_id,
            indexer=source.indexer,
            size=source.size,
            seeders=source.seeders,
            leechers=source.leechers,
            publish_date=source.publish_date,
            info_url=source.info_url,
            indexer_flags=source.indexer_flags,
            download_url=source.download_url,
            magnet_url=source.magnet_url,
        )
    if isinstance(source, UsenetSource):
        return Release(
            protocol="usenet",
            guid=source.guid,
            title=source.title,
            authors=source.book_metadata.authors,
            narrators=source.book_metadata.narrators,
            indexer_id=source.indexer_id,
            indexer=source.indexer,
            size=source.size,
            grabs=source.grabs,
            publish_date=source.publish_date,
            info_url=source.info_url,
            indexer_flags=source.indexer_flags,
            download_url=source.download_url,
            magnet_url=source.magnet_url,
        )
    return Release(
        protocol="direct",
        guid=source.guid,
        title=source.title,
        authors=source.book_metadata.authors,
        narrators=source.book_metadata.narrators,
        indexer_id=source.indexer_id,
        indexer=source.indexer,
        size=source.size,
        publish_date=source.publish_date,
        info_url=source.info_url,
        indexer_flags=source.indexer_flags,
        download_urls=source.download_urls,
        file_format=source.filetype or source.book_metadata.filetype,
    )


def upsert_release(session: Session, source: DownloadSource) -> Release:
    """Insert or refresh a release keyed by ``(protocol, guid)``."""
    existing = session.exec(
        select(Release).where(
            Release.protocol == source.protocol,
            Release.guid == source.guid,
        )
    ).first()
    incoming = release_from_source(source)

    if existing is None:
        session.add(incoming)
        session.commit()
        session.refresh(incoming)
        return incoming

    existing.title = incoming.title
    existing.authors = incoming.authors
    existing.narrators = incoming.narrators
    existing.indexer_id = incoming.indexer_id
    existing.indexer = incoming.indexer
    existing.size = incoming.size
    existing.seeders = incoming.seeders
    existing.leechers = incoming.leechers
    existing.grabs = incoming.grabs
    existing.file_format = incoming.file_format
    existing.publish_date = incoming.publish_date
    existing.info_url = incoming.info_url
    existing.indexer_flags = incoming.indexer_flags
    existing.download_urls = incoming.download_urls
    existing.magnet_url = incoming.magnet_url
    existing.download_url = incoming.download_url
    session.add(existing)
    session.commit()
    return existing


def add_release_request(session: Session, release_id: int, username: str) -> None:
    """Bookmark a release for later download (idempotent)."""
    if session.get(ReleaseRequest, (release_id, username)) is None:
        session.add(ReleaseRequest(release_id=release_id, user_username=username))
        session.commit()


def remove_release_request(session: Session, release_id: int, username: str) -> bool:
    req = session.get(ReleaseRequest, (release_id, username))
    if req is None:
        return False
    session.delete(req)
    session.commit()
    return True


def list_release_requests(
    session: Session, username: str
) -> list[tuple[Release, datetime]]:
    """Return ``(release, requested_at)`` for a user's bookmarks."""
    requests = session.exec(
        select(ReleaseRequest)
        .where(ReleaseRequest.user_username == username)
        .order_by(desc(ReleaseRequest.created_at))
    ).all()
    result: list[tuple[Release, datetime]] = []
    for req in requests:
        release = session.get(Release, req.release_id)
        if release is not None:
            result.append((release, req.created_at))
    return result


async def _enqueue_direct(
    session: Session,
    client_session: ClientSession,
    release: Release,
) -> list[str]:
    download_client_config.raise_if_invalid(session)
    if not release.download_urls:
        raise DownloadClientMisconfigured("No downloadable files (sample-only release)")

    authors = ", ".join(release.authors) if release.authors else "Unknown Author"
    target_dir = posixpath.join(
        download_client_config.get_dir(session) or "",
        _sanitize_segment(f"{authors} - {release.title}"),
    )
    files = [
        DownloadFile(urls=[url], out=_filename_from_url(url))
        for url in release.download_urls
    ]
    aria2 = Aria2Client(
        host=download_client_config.get_host(session) or "",
        port=download_client_config.get_port(session),
        secret=download_client_config.get_rpc_secret(session),
        client_session=client_session,
    )
    return await aria2.enqueue(files, dir=target_dir)


async def download_release(
    session: Session,
    client_session: ClientSession,
    release: Release,
    username: str,
) -> DownloadJob:
    """Dispatch a release to the right client and record a download job.

    The job starts as ``downloading``; completion/failure tracking arrives in P9.
    A dispatch failure is recorded as ``failed`` and re-raised.
    """
    client = "aria2" if release.protocol == "direct" else "prowlarr"
    job = DownloadJob(
        release_id=release.id,
        user_username=username,
        client=client,
        status="downloading",
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    try:
        if release.protocol == "direct":
            gids = await _enqueue_direct(session, client_session, release)
            job.external_id = ",".join(gids)
        else:
            await start_download(
                session=session,
                client_session=client_session,
                guid=release.guid,
                indexer_id=release.indexer_id,
                book_ref=None,
            )
        session.add(job)
        session.commit()

        if abs_config.is_valid(session):
            try:
                await abs_trigger_scan(session, client_session)
            except Exception:
                logger.error("Failed to trigger ABS scan after release download")
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)
        session.add(job)
        session.commit()
        raise

    return job
