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

    def raise_if_invalid(self, session: Session):
        if not self.get_host(session):
            raise DownloadClientMisconfigured("Download client host not set")
        if not self.get_port(session):
            raise DownloadClientMisconfigured("Download client port not set")
        if not self.get_dir(session):
            raise DownloadClientMisconfigured("Download client base directory not set")


download_client_config = DownloadClientConfig()
