from abc import ABC, abstractmethod

from pydantic import BaseModel


class DownloadFile(BaseModel):
    urls: list[str]
    out: str | None = None


class DownloadClient(ABC):
    """External download client abstraction for direct (non-tracker) sources."""

    @abstractmethod
    async def test_connection(self) -> bool: ...

    @abstractmethod
    async def enqueue(
        self,
        files: list[DownloadFile],
        *,
        dir: str,
        headers: list[str] | None = None,
    ) -> list[str]:
        """Enqueue downloads and return one identifier (GID) per file."""

    @abstractmethod
    async def status(self, gid: str) -> dict[str, object] | None: ...
