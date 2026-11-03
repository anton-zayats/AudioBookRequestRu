from typing import Literal

from sqlmodel import Session

from app.internal.metadata.base import MetadataProvider
from app.internal.metadata.livelib import LivelibProvider
from app.util.cache import StringConfigCache
from app.util.log import logger

MetadataSourceKey = Literal["metadata_source"]


class MetadataSourceConfig(StringConfigCache[MetadataSourceKey]):
    def get_source(self, session: Session) -> str:
        return self.get(session, "metadata_source", "livelib") or "livelib"


metadata_source = MetadataSourceConfig()

_livelib_provider = LivelibProvider()


def get_provider(session: Session) -> MetadataProvider:
    """Return the configured metadata provider (livelib only for now)."""
    source = metadata_source.get_source(session)
    if source != "livelib":
        logger.warning(
            "unknown metadata source configured, using livelib", source=source
        )
    return _livelib_provider
