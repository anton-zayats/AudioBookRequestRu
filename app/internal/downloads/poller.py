"""Background poller for in-flight download jobs.

The requests page can refresh status on demand; this poller does the same in the
background so badges update on their own. It only talks to the download clients
when there is at least one in-flight job.
"""

from __future__ import annotations

from aiohttp import ClientSession
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlmodel import Session, col, select

from app.internal.audiobookshelf.client import abs_trigger_scan
from app.internal.audiobookshelf.config import abs_config
from app.internal.models import DownloadJob
from app.internal.releases import DownloadStateRefresh, refresh_download_states
from app.util.db import get_session
from app.util.log import logger

#: How often in-flight download jobs are polled (seconds).
POLL_INTERVAL_SECONDS = 60

_scheduler: AsyncIOScheduler | None = None


def in_flight_release_ids(session: Session) -> list[int]:
    """Releases that have at least one job the download client can be asked about."""
    release_ids = session.exec(
        select(DownloadJob.release_id)
        .where(
            DownloadJob.status == "downloading",
            col(DownloadJob.external_id).is_not(None),
        )
        .distinct()
    ).all()
    return [int(release_id) for release_id in release_ids]


async def poll_download_states() -> DownloadStateRefresh | None:
    """Poll every in-flight download once; ``None`` when there is nothing to do."""
    with next(get_session()) as session:
        release_ids = in_flight_release_ids(session)
        if not release_ids:
            return None

        async with ClientSession() as client_session:
            result = await refresh_download_states(session, client_session, release_ids)
            if result.completed and abs_config.is_valid(session):
                try:
                    await abs_trigger_scan(session, client_session)
                except Exception as exc:
                    logger.error(
                        "ABS scan after completed download failed", error=str(exc)
                    )
            if result.updated:
                logger.info(
                    "Download status poll updated jobs",
                    updated=result.updated,
                    completed=result.completed,
                    failed=result.failed,
                )
            return result


async def _tick() -> None:
    try:
        await poll_download_states()
    except Exception as exc:
        # A broken download client must never kill the scheduler.
        logger.error("Download status poll failed", error=str(exc))


def start_poller(interval_seconds: int = POLL_INTERVAL_SECONDS) -> None:
    """Start the background poller (idempotent)."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _tick,
        "interval",
        seconds=interval_seconds,
        id="download-status-poll",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info("Download status poller started", interval_seconds=interval_seconds)


def stop_poller() -> None:
    """Stop the background poller (idempotent)."""
    global _scheduler
    scheduler = _scheduler
    _scheduler = None
    if scheduler is not None and scheduler.running:
        scheduler.shutdown(wait=False)


def poller_running() -> bool:
    return _scheduler is not None and _scheduler.running
