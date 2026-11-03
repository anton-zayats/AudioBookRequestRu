from typing import Annotated

from aiohttp import ClientSession
from fastapi import APIRouter, Depends, HTTPException, Response, Security
from pydantic import BaseModel
from sqlmodel import Session

from app.internal.auth.authentication import AnyAuth, DetailedUser
from app.internal.downloads.aria2 import Aria2Client, DownloadClientError
from app.internal.downloads.config import (
    DownloadClientMisconfigured,
    download_client_config,
)
from app.internal.models import GroupEnum
from app.internal.ranking.quality import IndexerFlag, QualityRange, quality_config
from app.util.connection import get_connection
from app.util.db import get_session

router = APIRouter(prefix="/download")


class DownloadSettings(BaseModel):
    auto_download: bool
    flac_range: QualityRange
    m4b_range: QualityRange
    mp3_range: QualityRange
    unknown_audio_range: QualityRange
    unknown_range: QualityRange
    min_seeders: int
    name_ratio: int
    title_ratio: int
    indexer_flags: list[IndexerFlag]


@router.get("", response_model=DownloadSettings)
def get_download_settings(
    session: Annotated[Session, Depends(get_session)],
    _: Annotated[DetailedUser, Security(AnyAuth(GroupEnum.admin))],
):
    return DownloadSettings(
        auto_download=quality_config.get_auto_download(session),
        flac_range=quality_config.get_range(session, "quality_flac"),
        m4b_range=quality_config.get_range(session, "quality_m4b"),
        mp3_range=quality_config.get_range(session, "quality_mp3"),
        unknown_audio_range=quality_config.get_range(session, "quality_unknown_audio"),
        unknown_range=quality_config.get_range(session, "quality_unknown"),
        min_seeders=quality_config.get_min_seeders(session),
        name_ratio=quality_config.get_name_exists_ratio(session),
        title_ratio=quality_config.get_title_exists_ratio(session),
        indexer_flags=quality_config.get_indexer_flags(session),
    )


class UpdateDownloadSettings(BaseModel):
    auto_download: bool
    flac_range: QualityRange
    m4b_range: QualityRange
    mp3_range: QualityRange
    unknown_audio_range: QualityRange
    unknown_range: QualityRange
    min_seeders: int
    name_ratio: int
    title_ratio: int


@router.patch("", status_code=204)
def update_download_settings(
    body: UpdateDownloadSettings,
    session: Annotated[Session, Depends(get_session)],
    _: Annotated[DetailedUser, Security(AnyAuth(GroupEnum.admin))],
):
    quality_config.set_auto_download(session, body.auto_download)
    quality_config.set_range(session, "quality_flac", body.flac_range)
    quality_config.set_range(session, "quality_m4b", body.m4b_range)
    quality_config.set_range(session, "quality_mp3", body.mp3_range)
    quality_config.set_range(session, "quality_unknown_audio", body.unknown_audio_range)
    quality_config.set_range(session, "quality_unknown", body.unknown_range)
    quality_config.set_min_seeders(session, body.min_seeders)
    quality_config.set_name_exists_ratio(session, body.name_ratio)
    quality_config.set_title_exists_ratio(session, body.title_ratio)

    return Response(status_code=204)


class DownloadClientSettings(BaseModel):
    download_client_host: str
    download_client_port: int
    download_client_rpc_secret: str
    download_client_dir: str


class UpdateDownloadClientSettings(BaseModel):
    download_client_host: str
    download_client_port: int
    download_client_rpc_secret: str | None = None
    download_client_dir: str


@router.get("/client", response_model=DownloadClientSettings)
def get_download_client_settings(
    session: Annotated[Session, Depends(get_session)],
    _: Annotated[DetailedUser, Security(AnyAuth(GroupEnum.admin))],
):
    return DownloadClientSettings(
        download_client_host=download_client_config.get_host(session) or "",
        download_client_port=download_client_config.get_port(session),
        download_client_rpc_secret=download_client_config.get_rpc_secret(session) or "",
        download_client_dir=download_client_config.get_dir(session) or "",
    )


@router.put("/client", status_code=204)
def update_download_client_settings(
    body: UpdateDownloadClientSettings,
    session: Annotated[Session, Depends(get_session)],
    _: Annotated[DetailedUser, Security(AnyAuth(GroupEnum.admin))],
):
    download_client_config.set_host(session, body.download_client_host)
    download_client_config.set_port(session, body.download_client_port)
    if body.download_client_rpc_secret:
        download_client_config.set_rpc_secret(session, body.download_client_rpc_secret)
    download_client_config.set_dir(session, body.download_client_dir)
    return Response(status_code=204)


class DownloadClientTestResponse(BaseModel):
    ok: bool


@router.post("/client/test", response_model=DownloadClientTestResponse)
async def test_download_client_connection(
    session: Annotated[Session, Depends(get_session)],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    _: Annotated[DetailedUser, Security(AnyAuth(GroupEnum.admin))],
):
    try:
        download_client_config.raise_if_invalid(session)
    except DownloadClientMisconfigured as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    client = Aria2Client(
        host=download_client_config.get_host(session) or "",
        port=download_client_config.get_port(session),
        secret=download_client_config.get_rpc_secret(session),
        client_session=client_session,
    )
    try:
        ok = await client.test_connection()
    except DownloadClientError as e:
        raise HTTPException(status_code=400, detail=e.message) from e

    if not ok:
        raise HTTPException(status_code=400, detail="aria2 connection failed")
    return DownloadClientTestResponse(ok=True)
