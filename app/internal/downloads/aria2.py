from typing import cast, override

from aiohttp import ClientSession

from app.internal.downloads.client import DownloadClient, DownloadFile
from app.util.connection import USER_AGENT
from app.util.log import logger


class DownloadClientError(Exception):
    message: str

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class Aria2Client(DownloadClient):
    """aria2 remote-control client over JSON-RPC (POST /jsonrpc)."""

    host: str
    port: int
    secret: str | None
    _client_session: ClientSession | None

    def __init__(
        self,
        host: str,
        port: int,
        secret: str | None = None,
        client_session: ClientSession | None = None,
    ) -> None:
        self.host = host.rstrip("/")
        self.port = port
        self.secret = secret
        self._client_session = client_session
        self._id: int = 0

    @property
    def endpoint(self) -> str:
        return f"http://{self.host}:{self.port}/jsonrpc"

    def _token(self) -> str:
        return f"token:{self.secret}" if self.secret else ""

    async def _rpc(self, method: str, params: list[object]) -> object:
        self._id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": f"abr-{self._id}",
            "method": method,
            "params": params,
        }

        async def send(session: ClientSession) -> object:
            try:
                async with session.post(self.endpoint, json=payload) as response:
                    try:
                        data = cast(dict[str, object], await response.json())
                    except Exception as e:
                        raise DownloadClientError(
                            f"aria2 returned non-JSON: {e}"
                        ) from e
                    error = data.get("error")
                    if error is not None:
                        raise DownloadClientError(f"aria2 RPC error: {error}")
                    return data.get("result")
            except TimeoutError as e:
                raise DownloadClientError(f"aria2 RPC timed out ({method})") from e

        if self._client_session is not None:
            return await send(self._client_session)
        async with ClientSession() as session:
            return await send(session)

    @override
    async def test_connection(self) -> bool:
        result = await self._rpc("system.listMethods", [])
        logger.info("aria2 connection test", endpoint=self.endpoint)
        return isinstance(result, list)

    @override
    async def enqueue(
        self,
        files: list[DownloadFile],
        *,
        dir: str,
        headers: list[str] | None = None,
    ) -> list[str]:
        gids: list[str] = []
        for file in files:
            options: dict[str, object] = {
                "dir": dir,
                "out": file.out,
                "header": headers or [],
                "user-agent": USER_AGENT,
                "continue": True,
                "allow-overwrite": False,
                "auto-file-renaming": True,
            }
            result = await self._rpc(
                "aria2.addUri", [self._token(), file.urls, options]
            )
            if not isinstance(result, str):
                raise DownloadClientError("aria2.addUri did not return a GID")
            gids.append(result)
        return gids

    @override
    async def status(self, gid: str) -> dict[str, object] | None:
        result = await self._rpc("aria2.tellStatus", [self._token(), gid])
        if isinstance(result, dict):
            return cast(dict[str, object], result)
        return None
