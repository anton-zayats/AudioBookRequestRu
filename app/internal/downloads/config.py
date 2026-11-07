from typing import Literal

from sqlmodel import Session

from app.util.cache import StringConfigCache


class DownloadClientMisconfigured(ValueError):
    pass


DownloadClientConfigKey = Literal[
    "download_client_host",
    "download_client_port",
    "download_client_rpc_secret",
    "download_client_dir",
    "torrent_client_url",
    "torrent_client_username",
    "torrent_client_password",
]

_DEFAULT_PORT = 6800


class DownloadClientConfig(StringConfigCache[DownloadClientConfigKey]):
    def get_host(self, session: Session) -> str | None:
        host = self.get(session, "download_client_host")
        if host:
            return host.rstrip("/")
        return None

    def set_host(self, session: Session, host: str):
        self.set(session, "download_client_host", host)

    def get_port(self, session: Session) -> int:
        return (
            self.get_int(session, "download_client_port", _DEFAULT_PORT)
            or _DEFAULT_PORT
        )

    def set_port(self, session: Session, port: int):
        self.set_int(session, "download_client_port", port)

    def get_rpc_secret(self, session: Session) -> str | None:
        return self.get(session, "download_client_rpc_secret")

    def set_rpc_secret(self, session: Session, rpc_secret: str):
        self.set(session, "download_client_rpc_secret", rpc_secret)

    def get_dir(self, session: Session) -> str | None:
        return self.get(session, "download_client_dir")

    def set_dir(self, session: Session, dir: str):
        self.set(session, "download_client_dir", dir)

    def is_configured(self, session: Session) -> bool:
        return self.get_host(session) is not None and self.get_dir(session) is not None

    def get_torrent_client_url(self, session: Session) -> str | None:
        url = self.get(session, "torrent_client_url")
        if url:
            return url.rstrip("/")
        return None

    def set_torrent_client_url(self, session: Session, url: str):
        self.set(session, "torrent_client_url", url.strip())

    def get_torrent_client_username(self, session: Session) -> str:
        return self.get(session, "torrent_client_username") or ""

    def set_torrent_client_username(self, session: Session, username: str):
        self.set(session, "torrent_client_username", username.strip())

    def get_torrent_client_password(self, session: Session) -> str:
        return self.get(session, "torrent_client_password") or ""

    def set_torrent_client_password(self, session: Session, password: str):
        self.set(session, "torrent_client_password", password)

    def is_torrent_client_configured(self, session: Session) -> bool:
        """Whether a torrent download client is configured for status tracking."""
        return bool(self.get_torrent_client_url(session))

    def raise_if_invalid(self, session: Session):
        if not self.get_host(session):
            raise DownloadClientMisconfigured("Download client host not set")
        if not self.get_port(session):
            raise DownloadClientMisconfigured("Download client port not set")
        if not self.get_dir(session):
            raise DownloadClientMisconfigured("Download client base directory not set")


download_client_config = DownloadClientConfig()
