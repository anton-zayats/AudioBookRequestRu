from typing import Literal

from sqlmodel import Session

from app.internal.metadata.base import MetadataProvider
from app.internal.metadata.livelib import LivelibProvider
from app.internal.providers.configuration import get_int, get_str, is_enabled
from app.internal.providers.definitions import LIVELIB, field_by_key
from app.util.cache import StringConfigCache
from app.util.log import logger

MetadataSourceKey = Literal["metadata_source"]


class MetadataSourceConfig(StringConfigCache[MetadataSourceKey]):
    def get_source(self, session: Session) -> str:
        return self.get(session, "metadata_source", "livelib") or "livelib"


metadata_source = MetadataSourceConfig()


def is_metadata_enabled(session: Session) -> bool:
    """Whether metadata lookups (suggestions/enrichment) are enabled at all."""
    return is_enabled(session, LIVELIB)


def _livelib_from_config(session: Session) -> LivelibProvider:
    base_url = field_by_key(LIVELIB, "livelib_base_url")
    delay = field_by_key(LIVELIB, "livelib_request_delay_ms")
    timeout = field_by_key(LIVELIB, "livelib_timeout_seconds")
    return LivelibProvider(
        base_url=get_str(session, base_url) if base_url else "",
        delay=(get_int(session, delay) / 1000) if delay else 0.5,
        timeout=float(get_int(session, timeout)) if timeout else 30.0,
    )


def get_provider(session: Session) -> MetadataProvider:
    """Return the configured metadata provider (livelib only for now)."""
    source = metadata_source.get_source(session)
    if source != "livelib":
        logger.warning(
            "unknown metadata source configured, using livelib", source=source
        )
    return _livelib_from_config(session)
