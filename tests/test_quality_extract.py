import asyncio
from datetime import UTC, datetime

from aiohttp import ClientSession
from sqlmodel import Session

from app.internal.models import Audiobook, TorrentSource
from app.internal.ranking.quality_extract import extract_qualities
from tests.mock_services import MockServer


def test_torrent_quality_survives_zero_runtime(prowlarr: MockServer, db: Session) -> None:
    """livelib books have no runtime; torrent/usenet sources must not be dropped."""
    _ = prowlarr
    book = Audiobook(
        source="livelib",
        external_id="1",
        title="Господа Головлевы",
        subtitle=None,
        authors=["Салтыков-Щедрин"],
        narrators=[],
        cover_image=None,
        release_date=datetime(1970, 1, 1, tzinfo=UTC),
        runtime_length_min=0,
    )
    source = TorrentSource(
        protocol="torrent",
        guid="g",
        indexer_id=1,
        indexer="RuTracker",
        title="Господа Головлевы mp3",
        seeders=10,
        leechers=1,
        size=100_000_000,
        info_url=None,
        indexer_flags=[],
        download_url=None,
        magnet_url=None,
        publish_date=datetime(1970, 1, 1, tzinfo=UTC),
    )

    async def run():
        async with ClientSession() as cs:
            return await extract_qualities(db, cs, source, book)

    qualities = asyncio.run(run())
    assert len(qualities) == 1
    assert qualities[0].file_format == "mp3"
    assert qualities[0].kbits == 0
