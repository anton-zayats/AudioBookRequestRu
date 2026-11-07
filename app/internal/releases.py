"""Release persistence and download dispatch (P12 target model).

A ``Release`` is a persisted :class:`~app.internal.models.DownloadSource` found
via search. This module handles upserting releases, bookmarking them
(:class:`~app.internal.models.ReleaseRequest`), and dispatching a download to
the right client while recording a :class:`~app.internal.models.DownloadJob`.
"""

from __future__ import annotations

import asyncio
import posixpath
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, cast
from urllib.parse import urlparse

from aiohttp import ClientSession
from pydantic import BaseModel
from sqlmodel import Session, col, desc, func, select

from app.internal.audiobookshelf.client import (
    abs_title_author_in_library,
    abs_trigger_scan,
)
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
    EventEnum,
    Release,
    ReleaseRequest,
    TorrentSource,
    UsenetSource,
)
from app.internal.notifications import send_all_notifications
from app.internal.prowlarr.prowlarr import start_download
from app.util.log import logger
from app.util.release_title import (
    clean_release_title,
    release_author_key,
    release_group_key,
    release_title_key,
)

#: User-facing download state of a release.
ReleaseState = Literal["not_downloaded", "sent", "completed", "failed"]

_STATE_LABELS: dict[ReleaseState, str] = {
    "not_downloaded": "Not downloaded",
    "sent": "Sent to download",
    "completed": "Downloaded",
    "failed": "Download failed",
}

#: Download-job statuses mapped onto the three user-facing states.
_JOB_STATES: dict[str, ReleaseState] = {
    "downloading": "sent",
    "queued": "sent",
    "completed": "completed",
    "failed": "failed",
}

#: How many Audiobookshelf "is it in the library" checks run concurrently.
_LIBRARY_CHECK_CONCURRENCY = 4


def _notification_replacements(release: Release) -> dict[str, str]:
    return {
        "sourceTitle": release.title,
        "sourceSizeMB": str(release.size_MB),
        "indexerName": release.indexer,
        "sourceProtocol": release.protocol,
    }


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
    """Map a transient ``DownloadSource`` to a persisted ``Release`` (no id).

    The release title prefers the provider's clean book title (knigavuhe sends
    one) over the raw result title (tracker upload names carry quality noise).
    """
    title = source.book_metadata.title or source.title
    if isinstance(source, TorrentSource):
        return Release(
            protocol="torrent",
            guid=source.guid,
            title=title,
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
            title=title,
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
        title=title,
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


def remove_release_request_all(session: Session, release_id: int) -> int:
    """Drop every bookmark of a release (admin action); returns rows removed."""
    requests = session.exec(
        select(ReleaseRequest).where(ReleaseRequest.release_id == release_id)
    ).all()
    for req in requests:
        session.delete(req)
    session.commit()
    return len(requests)


async def refresh_release(
    session: Session, client_session: ClientSession, release: Release
) -> Release | None:
    """Re-fetch a release from its source and update the stored copy.

    Returns ``None`` when the source no longer lists that exact release (gone or
    renamed guid). The direct-source URL refresh (knigavuhe links expire) also
    happens here, because a fresh search returns fresh download URLs.
    """
    from app.internal.search import SourceFilter, search_releases

    source_filter: SourceFilter = (
        "direct" if release.protocol == "direct" else "tracker"
    )
    query = clean_release_title(release.title)
    sources = await search_releases(session, client_session, query, source_filter)
    for source in sources:
        if source.protocol == release.protocol and source.guid == release.guid:
            return upsert_release(session, source)
    return None


class ReleaseBookmark(BaseModel):
    """A bookmarked release plus who bookmarked it."""

    release: Release
    requested_at: datetime
    users: list[str] = []


def list_release_requests(
    session: Session, username: str | None
) -> list[ReleaseBookmark]:
    """Return bookmarks with their requesters, newest first.

    ``username=None`` lists every user's bookmarks (admin view); a username
    limits the list to that user's own bookmarks.
    """
    statement = select(ReleaseRequest).order_by(desc(ReleaseRequest.created_at))
    if username is not None:
        statement = statement.where(ReleaseRequest.user_username == username)

    grouped: dict[int, ReleaseBookmark] = {}
    for req in session.exec(statement).all():
        bookmark = grouped.get(req.release_id)
        if bookmark is None:
            release = session.get(Release, req.release_id)
            if release is None:
                continue
            bookmark = ReleaseBookmark(
                release=release, requested_at=req.created_at, users=[]
            )
            grouped[req.release_id] = bookmark
        if req.user_username not in bookmark.users:
            bookmark.users.append(req.user_username)

    return list(grouped.values())


def release_states(session: Session, release_ids: list[int]) -> dict[int, ReleaseState]:
    """Latest download state per release (``not_downloaded`` when never sent)."""
    if not release_ids:
        return {}
    jobs = session.exec(
        select(DownloadJob)
        .where(col(DownloadJob.release_id).in_(release_ids))
        .order_by(desc(DownloadJob.id))
    ).all()
    states: dict[int, ReleaseState] = {}
    for job in jobs:
        if job.release_id in states:
            continue
        states[job.release_id] = _JOB_STATES.get(job.status, "sent")
    return states


def bookmark_users(session: Session, release_ids: list[int]) -> dict[int, list[str]]:
    """Usernames that bookmarked each release."""
    if not release_ids:
        return {}
    result: dict[int, list[str]] = defaultdict(list)
    requests = session.exec(
        select(ReleaseRequest).where(col(ReleaseRequest.release_id).in_(release_ids))
    ).all()
    for req in requests:
        result[req.release_id].append(req.user_username)
    return dict(result)


class ReleaseView(BaseModel):
    """A release plus the per-user presentation state of the lists/rows."""

    release: Release
    state: ReleaseState = "not_downloaded"
    bookmarked: bool = False
    in_library: bool | None = None
    requested_by: list[str] = []

    @property
    def id(self) -> int | None:
        return self.release.id

    @property
    def group_key(self) -> str:
        return release_group_key(self.release.title, self.release.authors)

    @property
    def status_label(self) -> str:
        """Short label for the row's state column."""
        if self.in_library:
            return "In library"
        return _STATE_LABELS[self.state]

    @property
    def status_tone(self) -> str:
        """DaisyUI badge tone matching :attr:`status_label`."""
        if self.in_library:
            return "badge-success"
        if self.state == "failed":
            return "badge-error"
        if self.state == "sent":
            return "badge-info"
        if self.state == "completed":
            return "badge-success"
        return "badge-ghost"


async def build_release_views(
    session: Session,
    client_session: ClientSession,
    releases: list[Release],
    username: str | None = None,
    check_library: bool = True,
) -> list[ReleaseView]:
    """Attach state, bookmark and in-library information to releases.

    ``username`` decides the ``bookmarked`` flag (that user's own bookmark);
    ``requested_by`` always lists every requester. Library checks are skipped
    when Audiobookshelf is not configured.
    """
    release_ids = [release.id for release in releases]
    states = release_states(session, release_ids)
    bookmarks = bookmark_users(session, release_ids)

    in_library: dict[int, bool] = {}
    if check_library and releases and abs_config.is_valid(session):
        semaphore = asyncio.Semaphore(_LIBRARY_CHECK_CONCURRENCY)

        async def _check(release: Release) -> None:
            async with semaphore:
                try:
                    in_library[release.id] = await abs_title_author_in_library(
                        session, client_session, release.title, release.authors
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug(
                        "ABS: library check failed",
                        release_id=release.id,
                        error=str(exc),
                    )

        await asyncio.gather(*[_check(release) for release in releases])

    views: list[ReleaseView] = []
    for release in releases:
        users = bookmarks.get(release.id, [])
        views.append(
            ReleaseView(
                release=release,
                state=states.get(release.id, "not_downloaded"),
                bookmarked=username in users if username is not None else bool(users),
                in_library=in_library.get(release.id),
                requested_by=users,
            )
        )
    return views


@dataclass
class ReleaseGroup:
    """Releases of the same book, for the grouped requests view."""

    title: str
    authors: list[str] = field(default_factory=list)
    releases: list[ReleaseView] = field(default_factory=list)

    @property
    def key(self) -> str:
        return release_group_key(self.title, self.authors)

    @property
    def has_in_library(self) -> bool:
        """Whether any release of this book is already in the library."""
        return any(view.in_library for view in self.releases)

    @property
    def requesters(self) -> list[str]:
        """Every user that bookmarked a release of this book."""
        names: list[str] = []
        for view in self.releases:
            for name in view.requested_by:
                if name not in names:
                    names.append(name)
        return names


def group_release_views(views: list[ReleaseView]) -> list[ReleaseGroup]:
    """Group release views by title + author, keeping first-seen order.

    Releases without authors (most tracker uploads) are merged into the single
    author-known group with the same cleaned title; if several different authors
    share a title, the author-less releases stay in their own group.
    """
    author_groups: dict[str, ReleaseGroup] = {}
    order: list[str] = []
    unknown_author: dict[str, list[ReleaseView]] = defaultdict(list)

    for view in views:
        release = view.release
        title_key = release_title_key(release.title)
        author_key = release_author_key(release.authors)
        if not author_key:
            unknown_author[title_key].append(view)
            continue
        key = f"{title_key}|{author_key}"
        group = author_groups.get(key)
        if group is None:
            group = ReleaseGroup(
                title=clean_release_title(release.title), authors=list(release.authors)
            )
            author_groups[key] = group
            order.append(key)
        group.releases.append(view)

    groups_by_title: dict[str, list[ReleaseGroup]] = defaultdict(list)
    for key in order:
        groups_by_title[key.split("|", 1)[0]].append(author_groups[key])

    extra: list[ReleaseGroup] = []
    for title_key, views_without_author in unknown_author.items():
        candidates = groups_by_title.get(title_key, [])
        if len(candidates) == 1:
            candidates[0].releases.extend(views_without_author)
            continue
        extra.append(
            ReleaseGroup(
                title=clean_release_title(views_without_author[0].release.title),
                releases=list(views_without_author),
            )
        )

    groups = [author_groups[key] for key in order]
    return [*groups, *extra]


async def _enqueue_direct(
    session: Session,
    client_session: ClientSession,
    release: Release,
) -> list[str]:
    if not release.download_urls:
        raise DownloadClientMisconfigured("No downloadable files (sample-only release)")
    download_client_config.raise_if_invalid(session)

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
            # Prowlarr grabs send their own success/failure notifications.
            await start_download(
                session=session,
                client_session=client_session,
                guid=release.guid,
                indexer_id=release.indexer_id,
            )
        session.add(job)
        session.commit()

        # Keep the release in the requests list so its download state stays visible.
        add_release_request(session, release.id, username)

        if release.protocol == "direct":
            try:
                await send_all_notifications(
                    EventEnum.on_successful_download,
                    _notification_replacements(release),
                )
            except Exception as exc:
                logger.error("Failed to send download notification", error=str(exc))

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

        if release.protocol == "direct":
            try:
                await send_all_notifications(
                    EventEnum.on_failed_download,
                    {"errorStatus": "500", "errorReason": str(exc)},
                )
            except Exception as notify_exc:
                logger.error(
                    "Failed to send download failure notification",
                    error=str(notify_exc),
                )
        raise

    return job


class ReleasePopularity(BaseModel):
    """A release plus how many users bookmarked it (home page "popular")."""

    release: Release
    request_count: int

    @property
    def reason(self) -> str:
        return f"{self.request_count} request{'s' if self.request_count != 1 else ''}"


def popular_releases(
    session: Session,
    limit: int = 10,
    min_requests: int = 1,
    exclude_username: str | None = None,
) -> list[ReleasePopularity]:
    """Most-bookmarked releases, newest bookmarks breaking ties."""
    request_count = func.count(col(ReleaseRequest.user_username)).label("request_count")
    last_requested = func.max(col(ReleaseRequest.created_at)).label("last_requested")

    statement = (
        select(Release, request_count, last_requested)
        .join(ReleaseRequest, col(Release.id) == ReleaseRequest.release_id)
        .group_by(col(Release.id))
        .having(request_count >= min_requests)
        .order_by(desc(request_count), desc(last_requested))
        .limit(limit)
    )

    if exclude_username:
        statement = statement.where(
            col(Release.id).not_in(
                select(ReleaseRequest.release_id).where(
                    ReleaseRequest.user_username == exclude_username
                )
            )
        )

    rows = session.exec(statement).all()
    return [
        ReleasePopularity(release=release, request_count=int(count))
        for release, count, _ in rows
    ]


def is_sample_only(release: Release) -> bool:
    """Whether the release is a trial/sample and therefore not requestable."""
    return "sample" in release.indexer_flags


class DownloadStateRefresh(BaseModel):
    """Outcome of polling a download client for in-flight jobs."""

    updated: int = 0
    completed: int = 0
    failed: int = 0
    pending_torrents: int = 0
    skipped: int = 0
    error: str | None = None


#: aria2 `tellStatus` values that mean the download finished or died.
_ARIA2_COMPLETE = "complete"
_ARIA2_FAILED = frozenset({"error", "removed"})


async def refresh_download_states(
    session: Session,
    client_session: ClientSession,
    release_ids: list[int],
) -> DownloadStateRefresh:
    """Poll aria2 for the in-flight jobs of the given releases.

    Torrent jobs are dispatched through Prowlarr, which exposes no queue for
    app grabs; they are counted in ``pending_torrents`` and left untouched
    until a download-client integration exists (P9).
    """
    result = DownloadStateRefresh()
    if not release_ids:
        return result

    jobs = session.exec(
        select(DownloadJob).where(
            col(DownloadJob.release_id).in_(release_ids),
            DownloadJob.status == "downloading",
        )
    ).all()
    if not jobs:
        return result

    result.pending_torrents = sum(1 for job in jobs if not _is_aria2_job(job))
    aria2_jobs = [job for job in jobs if _is_aria2_job(job)]
    if not aria2_jobs:
        return result

    try:
        download_client_config.raise_if_invalid(session)
    except DownloadClientMisconfigured as exc:
        result.error = str(exc)
        return result

    client = Aria2Client(
        host=download_client_config.get_host(session) or "",
        port=download_client_config.get_port(session),
        secret=download_client_config.get_rpc_secret(session),
        client_session=client_session,
    )

    for job in aria2_jobs:
        gids = [
            gid.strip() for gid in (job.external_id or "").split(",") if gid.strip()
        ]
        if not gids:
            result.skipped += 1
            continue

        statuses: list[str] = []
        errors: list[str] = []
        failed_lookup = False
        for gid in gids:
            try:
                status = await client.status(gid)
            except Exception as exc:
                logger.debug("aria2 status lookup failed", gid=gid, error=str(exc))
                failed_lookup = True
                break
            if not status or not isinstance(status.get("status"), str):
                failed_lookup = True
                break
            statuses.append(cast(str, status["status"]))
            message = status.get("errorMessage")
            if isinstance(message, str) and message:
                errors.append(message)

        if failed_lookup or not statuses:
            result.skipped += 1
            continue

        if all(status == _ARIA2_COMPLETE for status in statuses):
            job.status = "completed"
            result.completed += 1
            result.updated += 1
        elif any(status in _ARIA2_FAILED for status in statuses):
            job.status = "failed"
            job.error = errors[0] if errors else "Download failed in aria2"
            result.failed += 1
            result.updated += 1
        else:
            continue

        session.add(job)

    session.commit()
    return result


def _is_aria2_job(job: DownloadJob) -> bool:
    return job.client == "aria2" and bool(job.external_id)
