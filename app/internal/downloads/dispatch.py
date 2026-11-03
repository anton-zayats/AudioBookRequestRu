import posixpath
import re
from urllib.parse import urlparse

from aiohttp import ClientSession
from sqlmodel import Session

from app.internal.audiobookshelf.client import abs_trigger_scan
from app.internal.audiobookshelf.config import abs_config
from app.internal.downloads.aria2 import Aria2Client
from app.internal.downloads.client import DownloadFile
from app.internal.downloads.config import download_client_config
from app.internal.models import Audiobook, DirectSource, EventEnum, ManualBookRequest
from app.internal.notifications import (
    send_all_manual_notifications,
    send_all_notifications,
)
from app.util.log import logger


class DirectDownloadFailed(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message: str = message


def _sanitize_segment(value: str) -> str:
    """Make a value safe to use as a filesystem path segment."""
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


def _replacements(source: DirectSource) -> dict[str, str]:
    return {
        "sourceTitle": source.title,
        "sourceSizeMB": str(source.size_MB),
        "indexerName": source.indexer,
        "sourceProtocol": "direct",
    }


async def start_direct_download(
    session: Session,
    client_session: ClientSession,
    book: Audiobook | ManualBookRequest,
    source: DirectSource,
) -> list[str]:
    """Enqueue a direct source's files with the configured download client."""
    download_client_config.raise_if_invalid(session)

    authors = ", ".join(book.authors) if book.authors else "Unknown Author"
    target_dir = posixpath.join(
        download_client_config.get_dir(session) or "",
        _sanitize_segment(f"{authors} - {book.title}"),
    )

    files = [
        DownloadFile(urls=[url], out=_filename_from_url(url))
        for url in source.download_urls
    ]

    aria2 = Aria2Client(
        host=download_client_config.get_host(session) or "",
        port=download_client_config.get_port(session),
        secret=download_client_config.get_rpc_secret(session),
        client_session=client_session,
    )

    try:
        gids = await aria2.enqueue(files, dir=target_dir)
    except Exception as e:
        logger.error("Failed to start direct download", error=str(e))
        replacements = {"errorStatus": "500", "errorReason": str(e)}
        try:
            if isinstance(book, ManualBookRequest):
                await send_all_manual_notifications(
                    EventEnum.on_failed_download, book, replacements
                )
            else:
                await send_all_notifications(
                    EventEnum.on_failed_download, book.id, replacements
                )
        except Exception:
            logger.error("Failed to send download failure notification", error=str(e))
        raise DirectDownloadFailed(str(e)) from e

    logger.info("Direct download successfully started", gids=gids)
    if isinstance(book, ManualBookRequest):
        book.downloaded = True
        session.add(book)
        session.commit()
        await send_all_manual_notifications(
            EventEnum.on_successful_download, book, _replacements(source)
        )
    else:
        book.downloaded = True
        session.add(book)
        session.commit()
        await send_all_notifications(
            EventEnum.on_successful_download, book.id, _replacements(source)
        )

    if abs_config.is_valid(session):
        try:
            await abs_trigger_scan(session, client_session)
        except Exception:
            logger.error("Failed to trigger ABS scan after direct download")

    return gids
