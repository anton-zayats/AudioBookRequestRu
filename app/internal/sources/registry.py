"""Registry of direct-source providers and the combined search entry point."""

from aiohttp import ClientSession
from sqlmodel import Session

from app.internal.models import DirectSource, RankReference
from app.internal.providers.configuration import SessionContainer
from app.internal.sources.base import DirectSourceProvider
from app.internal.sources.knigavuhe import KnigavuheProvider
from app.util.log import logger

direct_source_providers: list[DirectSourceProvider] = [KnigavuheProvider()]


async def search_direct_sources(
    session: Session,
    client_session: ClientSession,
    book: RankReference,
) -> list[DirectSource]:
    """Search every enabled direct-source provider for a book.

    Returns the combined list of sources. Provider failures are logged and do
    not prevent other providers from contributing results.
    """
    container = SessionContainer(session=session, client_session=client_session)
    sources: list[DirectSource] = []
    for provider in direct_source_providers:
        try:
            if not provider.is_enabled(container):
                logger.debug("direct source provider disabled", name=provider.name)
                continue
            found = await provider.search(book, container)
            sources.extend(found)
        except Exception as exc:
            logger.error(
                "direct source provider failed",
                provider=provider.name,
                error=str(exc),
            )
    return sources
