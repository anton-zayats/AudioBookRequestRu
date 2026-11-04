"""Base protocol for direct (non-tracker) download source providers.

Direct sources are discovered by scraping/ad-hoc APIs rather than via Prowlarr,
so they get their own lightweight provider seam. This is intentionally separate
from :mod:`app.internal.indexers` (which enriches Prowlarr sources with extra
metadata) to avoid mixing the two search notions.
"""

from abc import ABC, abstractmethod

from app.internal.indexers.abstract import SessionContainer
from app.internal.indexers.configuration import (
    Configurations,
    indexer_configuration_cache,
)
from app.internal.models import DirectSource, RankReference


class DirectSourceProvider(ABC):
    """A provider that discovers direct download sources for a book."""

    name: str

    @abstractmethod
    def get_configurations(self, container: SessionContainer) -> Configurations:
        """Return the configurable options for this provider.

        Should not execute any slow operations.
        """

    def is_enabled(
        self,
        container: SessionContainer,
        configurations: Configurations,
    ) -> bool:
        """Return True if the provider is active and can be used."""
        _ = configurations
        enabled = indexer_configuration_cache.get_bool(
            container.session, f"{self.name}_enabled"
        )
        return enabled is not False

    def set_enabled(self, container: SessionContainer, enabled: bool) -> None:
        """Enable or disable the provider."""
        indexer_configuration_cache.set_bool(
            container.session, f"{self.name}_enabled", enabled
        )

    @abstractmethod
    async def search(
        self,
        book: RankReference,
        container: SessionContainer,
    ) -> list[DirectSource]:
        """Search for direct download sources for the given book/query."""
