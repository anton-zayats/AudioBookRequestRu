"""Base protocol for direct (non-tracker) download source providers.

Direct sources are discovered by scraping/ad-hoc APIs rather than via Prowlarr,
so they get their own lightweight provider seam. Settings come from the shared
provider configuration framework (:mod:`app.internal.providers.configuration`).
"""

from abc import ABC, abstractmethod

from app.internal.models import DirectSource, RankReference
from app.internal.providers.configuration import SessionContainer
from app.internal.providers.definitions import enabled_for


class DirectSourceProvider(ABC):
    """A provider that discovers direct download sources for a book."""

    name: str

    def is_enabled(self, container: SessionContainer) -> bool:
        """Return True if the provider is active and can be used."""
        return enabled_for(container.session, self.name)

    @abstractmethod
    async def search(
        self,
        book: RankReference,
        container: SessionContainer,
    ) -> list[DirectSource]:
        """Search for direct download sources for the given book/query."""
