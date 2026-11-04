"""Release search: query Prowlarr + direct sources directly and rank the results."""

from __future__ import annotations

from typing import Literal, cast

from aiohttp import ClientSession
from sqlmodel import Session

from app.internal.models import DownloadSource, RankReference, SearchRef
from app.internal.prowlarr.prowlarr import query_prowlarr
from app.internal.prowlarr.util import prowlarr_config
from app.internal.ranking.download_ranking import rank_sources
from app.internal.sources import search_direct_sources

SourceFilter = Literal["both", "tracker", "direct"]


async def search_releases(
    session: Session,
    client_session: ClientSession,
    query: str,
    source: SourceFilter = "both",
) -> list[DownloadSource]:
    """Search releases across trackers and direct sources for a raw query.

    No livelib in this path: trackers come from Prowlarr, direct sources from
    the registered providers (knigavuhe). Results are merged and ranked against
    the raw query.
    """
    ref: RankReference = SearchRef(title=query.strip())

    prowlarr_sources: list[DownloadSource] = []
    if source in ("both", "tracker") and prowlarr_config.is_valid(session):
        found = await query_prowlarr(
            session,
            client_session,
            ref,
            indexer_ids=prowlarr_config.get_indexers(session),
        )
        if found is not None:
            prowlarr_sources = [cast(DownloadSource, s) for s in found]

    direct_sources: list[DownloadSource] = []
    if source in ("both", "direct"):
        direct_sources = [
            cast(DownloadSource, s)
            for s in await search_direct_sources(session, client_session, ref)
        ]

    merged: list[DownloadSource] = [*prowlarr_sources, *direct_sources]
    return await rank_sources(session, client_session, merged, ref)
