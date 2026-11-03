from abc import ABC, abstractmethod

from app.internal.models import Audiobook


class MetadataProvider(ABC):
    """Abstract source of searchable audiobook metadata.

    Implementations are expected to return transient :class:`Audiobook`
    objects (no ``id``) that the caller persists, keyed by
    ``(source, external_id)``.
    """

    @abstractmethod
    async def search(
        self,
        query: str,
        num_results: int = 20,
        page: int = 0,
    ) -> list[Audiobook]: ...

    @abstractmethod
    async def get_book(self, external_id: str) -> Audiobook | None: ...

    @abstractmethod
    async def suggestions(self, query: str) -> list[str]: ...

    @abstractmethod
    async def similar(self, book: Audiobook) -> list[Audiobook]: ...

    @abstractmethod
    async def categories(self) -> dict[str, list[Audiobook]]: ...

    def external_link(self, book: Audiobook) -> str:
        return book.url or ""
