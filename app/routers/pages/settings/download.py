from typing import Annotated

from aiohttp import ClientSession
from fastapi import APIRouter, Depends, Form, Response, Security
from sqlmodel import Session

from app.internal.auth.authentication import ABRAuth, DetailedUser
from app.internal.downloads.aria2 import Aria2Client, DownloadClientError
from app.internal.downloads.config import (
    DownloadClientMisconfigured,
    download_client_config,
)
from app.internal.downloads.qbittorrent import QbittorrentClient, TorrentClientError
from app.internal.models import GroupEnum
from app.internal.ranking.quality import IndexerFlag, QualityRange, quality_config
from app.routers.api.settings.download import (
    UpdateDownloadSettings,
)
from app.routers.api.settings.download import (
    update_download_settings as api_update_download_settings,
)
from app.util.connection import get_connection
from app.util.db import get_session
from app.util.templates import catalog_response, catalog_response_toast

router = APIRouter(prefix="/download")


@router.get("")
def read_download(
    session: Annotated[Session, Depends(get_session)],
    admin_user: Annotated[DetailedUser, Security(ABRAuth(GroupEnum.admin))],
):
    auto_download = quality_config.get_auto_download(session)
    flac_range = quality_config.get_range(session, "quality_flac")
    m4b_range = quality_config.get_range(session, "quality_m4b")
    mp3_range = quality_config.get_range(session, "quality_mp3")
    unknown_audio_range = quality_config.get_range(session, "quality_unknown_audio")
    unknown_range = quality_config.get_range(session, "quality_unknown")
    min_seeders = quality_config.get_min_seeders(session)
    name_ratio = quality_config.get_name_exists_ratio(session)
    title_ratio = quality_config.get_title_exists_ratio(session)
    flags = quality_config.get_indexer_flags(session)
    download_client_host = download_client_config.get_host(session)
    download_client_port = download_client_config.get_port(session)
    download_client_rpc_secret = download_client_config.get_rpc_secret(session)
    download_client_dir = download_client_config.get_dir(session)
    torrent_client_url = download_client_config.get_torrent_client_url(session)
    torrent_client_username = download_client_config.get_torrent_client_username(
        session
    )
    torrent_client_password_set = bool(
        download_client_config.get_torrent_client_password(session)
    )

    return catalog_response(
        "Settings.Download.Index",
        user=admin_user,
        auto_download=auto_download,
        flac_range=flac_range,
        m4b_range=m4b_range,
        mp3_range=mp3_range,
        unknown_audio_range=unknown_audio_range,
        unknown_range=unknown_range,
        min_seeders=min_seeders,
        name_ratio=name_ratio,
        title_ratio=title_ratio,
        indexer_flags=flags,
        download_client_host=download_client_host,
        download_client_port=download_client_port,
        download_client_rpc_secret=download_client_rpc_secret,
        download_client_dir=download_client_dir,
        torrent_client_url=torrent_client_url,
        torrent_client_username=torrent_client_username,
        torrent_client_password_set=torrent_client_password_set,
    )


@router.post("/hx-sliders")
def update_download(
    flac_from: Annotated[float, Form()],
    flac_to: Annotated[float, Form()],
    m4b_from: Annotated[float, Form()],
    m4b_to: Annotated[float, Form()],
    mp3_from: Annotated[float, Form()],
    mp3_to: Annotated[float, Form()],
    unknown_audio_from: Annotated[float, Form()],
    unknown_audio_to: Annotated[float, Form()],
    unknown_from: Annotated[float, Form()],
    unknown_to: Annotated[float, Form()],
    min_seeders: Annotated[int, Form()],
    name_ratio: Annotated[int, Form()],
    title_ratio: Annotated[int, Form()],
    session: Annotated[Session, Depends(get_session)],
    admin_user: Annotated[DetailedUser, Security(ABRAuth(GroupEnum.admin))],
    auto_download: Annotated[bool, Form()] = False,
):
    flac = QualityRange(from_kbits=flac_from, to_kbits=flac_to)
    m4b = QualityRange(from_kbits=m4b_from, to_kbits=m4b_to)
    mp3 = QualityRange(from_kbits=mp3_from, to_kbits=mp3_to)
    unknown_audio = QualityRange(
        from_kbits=unknown_audio_from, to_kbits=unknown_audio_to
    )
    unknown = QualityRange(from_kbits=unknown_from, to_kbits=unknown_to)

    api_update_download_settings(
        UpdateDownloadSettings(
            auto_download=auto_download,
            flac_range=flac,
            m4b_range=m4b,
            mp3_range=mp3,
            unknown_audio_range=unknown_audio,
            unknown_range=unknown,
            min_seeders=min_seeders,
            name_ratio=name_ratio,
            title_ratio=title_ratio,
        ),
        session,
        admin_user,
    )

    return catalog_response_toast(
        "Settings.Download.Sliders",
        "Settings updated",
        "success",
        auto_download=auto_download,
        flac_range=flac,
        m4b_range=m4b,
        mp3_range=mp3,
        unknown_audio_range=unknown_audio,
        unknown_range=unknown,
        min_seeders=min_seeders,
        name_ratio=name_ratio,
        title_ratio=title_ratio,
    )


@router.delete("")
def reset_download_setings(
    session: Annotated[Session, Depends(get_session)],
    admin_user: Annotated[DetailedUser, Security(ABRAuth(GroupEnum.admin))],
):
    _ = admin_user
    quality_config.reset_all(session)
    return Response(status_code=204, headers={"HX-Refresh": "true"})


@router.post("/hx-flags")
def add_indexer_flag(
    session: Annotated[Session, Depends(get_session)],
    flag: Annotated[str, Form()],
    score: Annotated[int, Form()],
    admin_user: Annotated[DetailedUser, Security(ABRAuth(GroupEnum.admin))],
):
    _ = admin_user
    flags = quality_config.get_indexer_flags(session)
    if not any(f.flag == flag for f in flags):
        flags.append(IndexerFlag(flag=flag.lower(), score=score))
        quality_config.set_indexer_flags(session, flags)

    return catalog_response(
        "Settings.Download.IndexerFlags",
        indexer_flags=flags,
    )


@router.delete("/hx-flags/{flag}")
def remove_indexer_flag(
    flag: str,
    session: Annotated[Session, Depends(get_session)],
    admin_user: Annotated[DetailedUser, Security(ABRAuth(GroupEnum.admin))],
):
    _ = admin_user
    flags = quality_config.get_indexer_flags(session)
    flags = [f for f in flags if f.flag != flag]
    quality_config.set_indexer_flags(session, flags)
    return catalog_response(
        "Settings.Download.IndexerFlags",
        indexer_flags=flags,
    )


@router.post("/hx-download-client")
def update_download_client(
    download_client_host: Annotated[str, Form()],
    download_client_port: Annotated[int, Form()],
    download_client_dir: Annotated[str, Form()],
    session: Annotated[Session, Depends(get_session)],
    admin_user: Annotated[DetailedUser, Security(ABRAuth(GroupEnum.admin))],
    download_client_rpc_secret: Annotated[str, Form()] = "",
):
    _ = admin_user
    download_client_config.set_host(session, download_client_host)
    download_client_config.set_port(session, download_client_port)
    if download_client_rpc_secret:
        download_client_config.set_rpc_secret(session, download_client_rpc_secret)
    download_client_config.set_dir(session, download_client_dir)

    return catalog_response_toast(
        "Settings.Download.Client",
        "Download client updated",
        "success",
        download_client_host=download_client_config.get_host(session),
        download_client_port=download_client_config.get_port(session),
        download_client_rpc_secret=download_client_config.get_rpc_secret(session),
        download_client_dir=download_client_config.get_dir(session),
    )


@router.post("/hx-download-client-test")
async def test_download_client(
    session: Annotated[Session, Depends(get_session)],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    admin_user: Annotated[DetailedUser, Security(ABRAuth(GroupEnum.admin))],
):
    _ = admin_user
    try:
        download_client_config.raise_if_invalid(session)
    except DownloadClientMisconfigured as e:
        return catalog_response_toast(
            "Settings.Download.Client",
            str(e),
            "error",
            download_client_host=download_client_config.get_host(session),
            download_client_port=download_client_config.get_port(session),
            download_client_rpc_secret=download_client_config.get_rpc_secret(session),
            download_client_dir=download_client_config.get_dir(session),
        )

    client = Aria2Client(
        host=download_client_config.get_host(session) or "",
        port=download_client_config.get_port(session),
        secret=download_client_config.get_rpc_secret(session),
        client_session=client_session,
    )
    try:
        ok = await client.test_connection()
    except DownloadClientError as e:
        return catalog_response_toast(
            "Settings.Download.Client",
            e.message,
            "error",
            download_client_host=download_client_config.get_host(session),
            download_client_port=download_client_config.get_port(session),
            download_client_rpc_secret=download_client_config.get_rpc_secret(session),
            download_client_dir=download_client_config.get_dir(session),
        )

    if not ok:
        return catalog_response_toast(
            "Settings.Download.Client",
            "aria2 connection failed",
            "error",
            download_client_host=download_client_config.get_host(session),
            download_client_port=download_client_config.get_port(session),
            download_client_rpc_secret=download_client_config.get_rpc_secret(session),
            download_client_dir=download_client_config.get_dir(session),
        )

    return catalog_response_toast(
        "Settings.Download.Client",
        "aria2 connection successful",
        "success",
        download_client_host=download_client_config.get_host(session),
        download_client_port=download_client_config.get_port(session),
        download_client_rpc_secret=download_client_config.get_rpc_secret(session),
        download_client_dir=download_client_config.get_dir(session),
    )


def _torrent_client_template_kwargs(session: Session) -> dict[str, object]:
    return {
        "torrent_client_url": download_client_config.get_torrent_client_url(session),
        "torrent_client_username": download_client_config.get_torrent_client_username(
            session
        ),
        "torrent_client_password_set": bool(
            download_client_config.get_torrent_client_password(session)
        ),
    }


@router.post("/hx-torrent-client")
def update_torrent_client(
    torrent_client_url: Annotated[str, Form()],
    session: Annotated[Session, Depends(get_session)],
    admin_user: Annotated[DetailedUser, Security(ABRAuth(GroupEnum.admin))],
    torrent_client_username: Annotated[str, Form()] = "",
    torrent_client_password: Annotated[str, Form()] = "",
):
    _ = admin_user
    download_client_config.set_torrent_client_url(session, torrent_client_url)
    download_client_config.set_torrent_client_username(session, torrent_client_username)
    if torrent_client_password:
        download_client_config.set_torrent_client_password(
            session, torrent_client_password
        )

    return catalog_response_toast(
        "Settings.Download.TorrentClient",
        "Torrent client updated",
        "success",
        **_torrent_client_template_kwargs(session),  # pyright: ignore[reportArgumentType]
    )


@router.post("/hx-torrent-client-test")
async def test_torrent_client(
    session: Annotated[Session, Depends(get_session)],
    client_session: Annotated[ClientSession, Depends(get_connection)],
    admin_user: Annotated[DetailedUser, Security(ABRAuth(GroupEnum.admin))],
):
    _ = admin_user
    url = download_client_config.get_torrent_client_url(session)
    if not url:
        return catalog_response_toast(
            "Settings.Download.TorrentClient",
            "Torrent client URL not set",
            "error",
            **_torrent_client_template_kwargs(session),  # pyright: ignore[reportArgumentType]
        )

    client = QbittorrentClient(
        base_url=url,
        username=download_client_config.get_torrent_client_username(session),
        password=download_client_config.get_torrent_client_password(session),
        client_session=client_session,
    )
    try:
        ok = await client.test_connection()
    except TorrentClientError as e:
        return catalog_response_toast(
            "Settings.Download.TorrentClient",
            e.message,
            "error",
            **_torrent_client_template_kwargs(session),  # pyright: ignore[reportArgumentType]
        )

    return catalog_response_toast(
        "Settings.Download.TorrentClient",
        "Torrent client connection successful"
        if ok
        else "Torrent client connection failed",
        "success" if ok else "error",
        **_torrent_client_template_kwargs(session),  # pyright: ignore[reportArgumentType]
    )
