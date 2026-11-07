"""qBittorrent-compatible torrent client (works with Decypharr).

Torrent releases are grabbed through Prowlarr, so ABR never learns a torrent
hash from Prowlarr. It computes the infohash itself before the grab and stores
it on the ``DownloadJob``; this client then looks the hash up in the download
client's queue to report completion/failure back to the UI.

Decypharr exposes a qBittorrent-compatible API, so it can be configured here
too (its API token is used as the username).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal, cast

import aiohttp
from aiohttp import ClientSession
from pydantic import BaseModel

from app.util.connection import USER_AGENT
from app.util.log import logger

TorrentJobState = Literal["completed", "failed", "downloading", "unknown"]

#: qBittorrent `state` values that mean the torrent is finished.
_COMPLETE_STATES = frozenset(
    {
        "uploading",
        "stalledup",
        "pausedup",
        "queuedup",
        "forcedup",
        "checkingup",
        "moving",
    }
)

#: qBittorrent `state` values that mean the torrent is still in flight.
_DOWNLOADING_STATES = frozenset(
    {
        "downloading",
        "stalleddl",
        "metadl",
        "queueddl",
        "pauseddl",
        "forceddl",
        "checkingdl",
        "checkingresumedata",
        "allocating",
    }
)

#: qBittorrent `state` values that mean the torrent died.
_FAILED_STATES = frozenset({"error", "missingfiles"})


class TorrentClientError(Exception):
    message: str

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class TorrentStatus(BaseModel):
    hash: str
    name: str | None = None
    state: str
    progress: float = 0.0

    @property
    def job_state(self) -> TorrentJobState:
        state = self.state.lower().replace(" ", "")
        if state in _COMPLETE_STATES:
            return "completed"
        if state in _FAILED_STATES:
            return "failed"
        if state in _DOWNLOADING_STATES:
            return "downloading"
        return "unknown"


class QbittorrentClient:
    """Minimal qBittorrent v2 API client (login + torrent queue)."""

    base_url: str
    username: str
    password: str
    _client_session: ClientSession | None

    def __init__(
        self,
        base_url: str,
        username: str = "",
        password: str = "",
        client_session: ClientSession | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._client_session = client_session
        self._sid: str | None = None

    @property
    def _headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": USER_AGENT,
            "Referer": self.base_url,
            "Origin": self.base_url,
        }
        if self._sid:
            headers["Cookie"] = f"SID={self._sid}"
        return headers

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[ClientSession]:
        if self._client_session is not None:
            yield self._client_session
            return
        async with ClientSession() as session:
            yield session

    async def _login(self) -> None:
        if not self.username and not self.password:
            return
        data = {"username": self.username, "password": self.password}
        try:
            async with (
                self._session() as session,
                session.post(
                    f"{self.base_url}/api/v2/auth/login",
                    headers=self._headers,
                    data=data,
                ) as response,
            ):
                body = (await response.text()).strip()
                if response.status >= 400 or body not in {"Ok.", ""}:
                    raise TorrentClientError(
                        f"Torrent client login failed ({response.status}): {body or 'no body'}"
                    )
                sid = response.cookies.get("SID")
                if sid is not None:
                    self._sid = sid.value
        except aiohttp.ClientError as e:
            raise TorrentClientError(f"Torrent client unreachable: {e}") from e

    async def test_connection(self) -> bool:
        await self._login()
        try:
            async with (
                self._session() as session,
                session.get(
                    f"{self.base_url}/api/v2/app/version", headers=self._headers
                ) as response,
            ):
                if response.status >= 400:
                    raise TorrentClientError(
                        f"Torrent client returned {response.status}"
                    )
                return True
        except aiohttp.ClientError as e:
            raise TorrentClientError(f"Torrent client unreachable: {e}") from e

    async def statuses(self, hashes: list[str]) -> dict[str, TorrentStatus]:
        """Return the queue entries for the given infohashes (lowercased keys)."""
        if not hashes:
            return {}
        await self._login()
        params = {"hashes": "|".join(hashes)}
        try:
            async with (
                self._session() as session,
                session.get(
                    f"{self.base_url}/api/v2/torrents/info",
                    headers=self._headers,
                    params=params,
                ) as response,
            ):
                if response.status >= 400:
                    raise TorrentClientError(
                        f"Torrent client returned {response.status}"
                    )
                raw_payload = cast(object, await response.json())
        except aiohttp.ClientError as e:
            raise TorrentClientError(f"Torrent client unreachable: {e}") from e
        except ValueError as e:
            raise TorrentClientError(f"Torrent client returned non-JSON: {e}") from e

        if not isinstance(raw_payload, list):
            raise TorrentClientError("Torrent client returned an unexpected payload")

        result: dict[str, TorrentStatus] = {}
        for raw_entry in cast(list[object], raw_payload):
            if not isinstance(raw_entry, dict):
                continue
            entry = cast(dict[str, object], raw_entry)
            raw_hash = entry.get("hash")
            raw_state = entry.get("state")
            if not isinstance(raw_hash, str) or not isinstance(raw_state, str):
                continue
            progress = entry.get("progress")
            name = entry.get("name")
            result[raw_hash.lower()] = TorrentStatus(
                hash=raw_hash.lower(),
                name=name if isinstance(name, str) else None,
                state=raw_state,
                progress=float(progress) if isinstance(progress, (int, float)) else 0.0,
            )
        logger.debug(
            "Torrent client status lookup", requested=len(hashes), found=len(result)
        )
        return result
