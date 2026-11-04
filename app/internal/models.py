import json
import uuid
from datetime import datetime
from enum import Enum
from typing import Annotated, Literal, cast, override

from pydantic import BaseModel, ConfigDict
from pydantic import Field as PydanticField
from sqlalchemy import UniqueConstraint
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator
from sqlmodel import JSON, Column, DateTime, Field, SQLModel, func
from sqlmodel._compat import SQLModelConfig
from sqlmodel.main import Relationship


class BaseSQLModel(SQLModel):
    pass


class GroupEnum(str, Enum):
    untrusted = "untrusted"
    trusted = "trusted"
    admin = "admin"


class User(BaseSQLModel, table=True):
    username: str = Field(primary_key=True)
    password: str
    group: GroupEnum = Field(
        default=GroupEnum.untrusted,
        sa_column_kwargs={"server_default": "untrusted"},
    )
    root: bool = False
    extra_data: str | None = None

    # TODO: Add last_login
    # last_login: datetime = Field(
    #     default_factory=datetime.now, sa_column_kwargs={"server_default": "now()"}
    # )

    """
    untrusted: Requests need to be manually reviewed
    trusted: Requests are automatically downloaded if possible
    admin: Can approve or deny requests, change settings, etc.
    """

    def is_above(self, group: GroupEnum) -> bool:
        if group == "admin":
            return self.group == GroupEnum.admin
        if group == "trusted":
            return self.group in [GroupEnum.admin, GroupEnum.trusted]
        return True

    def can_download(self):
        return self.is_above(GroupEnum.trusted)

    def is_admin(self):
        return self.group == GroupEnum.admin

    def is_self(self, username: str):
        # To prevent '==' in Jinja2, since that breaks formatting
        return self.username == username


class Audiobook(BaseSQLModel, table=True):
    """A cached audiobook result, keyed by (source, external_id).

    external_id is the old asin, generalized to a per-provider identifier.
    """

    __table_args__: tuple[UniqueConstraint] = (
        UniqueConstraint(
            "source", "external_id", name="uq_audiobook_source_external_id"
        ),
    )

    id: int = Field(default=None, primary_key=True)
    source: str = "livelib"
    external_id: str
    url: str | None = None
    title: str
    subtitle: str | None
    authors: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    narrators: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    cover_image: str | None
    release_date: datetime
    runtime_length_min: int
    updated_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(
            onupdate=func.now(),
            server_default=func.now(),
            type_=DateTime,
            nullable=False,
        ),
    )
    downloaded: bool = False

    requests: list[AudiobookRequest] = Relationship(back_populates="audiobook")  # pyright: ignore[reportAny]

    model_config: SQLModelConfig = cast(
        SQLModelConfig, cast(object, ConfigDict(arbitrary_types_allowed=True))
    )

    @property
    def runtime_length_hrs(self):
        return round(self.runtime_length_min / 60, 1)


class AudiobookWithRequests(BaseModel):
    book: Audiobook
    requests: list[AudiobookRequest]
    username: str | None

    @property
    def already_requested(self):
        if self.username:
            return any(req.user_username == self.username for req in self.requests)
        return len(self.requests) > 0


class SelectedRelease(BaseModel):
    """A concrete release the requester chose to download.

    ``title`` and ``narrators`` are display-only; dispatch keys on
    ``(protocol, guid)``.
    """

    protocol: Literal["torrent", "usenet", "direct"]
    guid: str
    title: str | None = None
    narrators: list[str] = PydanticField(default_factory=list)


class _SelectedReleaseJSON(TypeDecorator[SelectedRelease]):
    """JSON column type that round-trips :class:`SelectedRelease`.

    SQLAlchemy's plain JSON type can only serialize dicts/lists, so a pydantic
    model would fail on write and come back as a dict on read. This wrapper
    serializes the model on bind and rebuilds it on load.
    """

    impl = JSON  # pyright: ignore[reportUnannotatedClassAttribute]
    cache_ok = True  # pyright: ignore[reportUnannotatedClassAttribute]

    @override
    def process_bind_param(
        self, value: SelectedRelease | None, dialect: Dialect
    ) -> dict[str, object] | None:
        if value is None:
            return None
        return value.model_dump(mode="json")

    @override
    def process_result_value(
        self, value: object | None, dialect: Dialect
    ) -> SelectedRelease | None:
        if value is None:
            return None
        return SelectedRelease.model_validate(value)


class AudiobookRequest(BaseSQLModel, table=True):
    book_id: int = Field(
        primary_key=True,
        foreign_key="audiobook.id",
        ondelete="CASCADE",
    )
    user_username: str = Field(
        primary_key=True,
        foreign_key="user.username",
        ondelete="CASCADE",
    )
    updated_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(
            onupdate=func.now(),
            server_default=func.now(),
            type_=DateTime,
            nullable=False,
        ),
    )
    selected_release: SelectedRelease | None = Field(
        default=None,
        sa_column=Column(_SelectedReleaseJSON()),
    )

    audiobook: Audiobook = Relationship(back_populates="requests")  # pyright: ignore[reportAny]
    user: User = Relationship()  # pyright: ignore[reportAny]

    model_config: SQLModelConfig = cast(
        SQLModelConfig, cast(object, ConfigDict(arbitrary_types_allowed=True))
    )


class ReleaseSnapshot(BaseSQLModel, table=True):
    """Persisted release search results for a book (P8).

    Stores the ranked :class:`DownloadSource` list so the release chooser can
    render instantly on revisit instead of re-querying external providers.
    """

    book_id: int = Field(
        primary_key=True,
        foreign_key="audiobook.id",
        ondelete="CASCADE",
    )
    sources: list[dict[str, object]] = Field(
        default_factory=list, sa_column=Column(JSON)
    )
    updated_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(
            onupdate=func.now(),
            server_default=func.now(),
            type_=DateTime,
            nullable=False,
        ),
    )


class AudiobookWishlistResult(BaseModel):
    book: Audiobook
    requests: list[AudiobookRequest]
    download_error: str | None = None

    @property
    def amount_requested(self):
        return len(self.requests)

    @property
    def requested_by_usernames(self):
        return "\n".join(req.user_username for req in self.requests)

    @property
    def selected_release(self) -> SelectedRelease | None:
        for req in self.requests:
            if req.selected_release is not None:
                return req.selected_release
        return None


class ManualBookRequest(BaseSQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_username: str = Field(foreign_key="user.username", ondelete="CASCADE")
    title: str
    subtitle: str | None = None
    authors: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    narrators: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    publish_date: str | None = None
    additional_info: str | None = None
    updated_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(
            onupdate=func.now(),
            server_default=func.now(),
            type_=DateTime,
            nullable=False,
        ),
    )
    downloaded: bool = False

    model_config: SQLModelConfig = cast(
        SQLModelConfig, cast(object, ConfigDict(arbitrary_types_allowed=True))
    )


class BookMetadata(BaseSQLModel):
    """extra metadata that can be added to sources to better rank them"""

    title: str | None = None
    subtitle: str | None = None
    authors: list[str] = []
    narrators: list[str] = []
    filetype: str | None = None


class BaseSource(BaseSQLModel):
    guid: str
    indexer_id: int
    indexer: str
    title: str
    size: int  # in bytes
    publish_date: datetime
    info_url: str | None
    indexer_flags: list[str]
    download_url: str | None = None
    magnet_url: str | None = None

    book_metadata: BookMetadata = BookMetadata()

    @property
    def size_MB(self):
        return round(self.size / 1e6, 1)


class TorrentSource(BaseSource):
    protocol: Literal["torrent"] = "torrent"
    seeders: int
    leechers: int


class UsenetSource(BaseSource):
    protocol: Literal["usenet"] = "usenet"
    grabs: int


class DirectSource(BaseSource):
    protocol: Literal["direct"] = "direct"
    download_urls: list[str]
    filetype: str | None = None


ProwlarrSource = Annotated[
    TorrentSource | UsenetSource, Field(discriminator="protocol")
]

DownloadSource = Annotated[
    TorrentSource | UsenetSource | DirectSource, Field(discriminator="protocol")
]


class Indexer(BaseModel, frozen=True):
    id: int
    name: str
    enable: bool
    privacy: str


class Config(BaseSQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str


class EventEnum(str, Enum):
    on_new_request = "onNewRequest"
    on_successful_download = "onSuccessfulDownload"
    on_failed_download = "onFailedDownload"


class NotificationBodyTypeEnum(str, Enum):
    text = "text"
    json = "json"


class Notification(BaseSQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str
    url: str
    headers: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    event: EventEnum
    body_type: NotificationBodyTypeEnum
    body: str
    enabled: bool

    @property
    def serialized_headers(self):
        return json.dumps(self.headers)


class APIKeyResponse(BaseModel):
    id: uuid.UUID
    name: str
    enabled: bool


class APIKey(BaseSQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_username: str = Field(foreign_key="user.username", ondelete="CASCADE")
    name: str
    key_hash: str
    created_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(
            server_default=func.now(),
            type_=DateTime,
            nullable=False,
        ),
    )
    last_used: datetime | None = Field(
        default=None,
        sa_column=Column(
            type_=DateTime,
            nullable=True,
        ),
    )
    enabled: bool = True
