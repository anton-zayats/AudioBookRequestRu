from typing import Annotated

from fastapi import APIRouter, Depends, Response, Security
from pydantic import BaseModel
from sqlmodel import Session

from app.internal.auth.authentication import AnyAuth, DetailedUser
from app.internal.indexers.configuration import indexer_configuration_cache
from app.internal.models import GroupEnum
from app.internal.sources.knigavuhe import (
    BASE_URL_KEY,
    DEFAULT_BASE_URL,
    DEFAULT_MAX_CANDIDATES,
    ENABLED_KEY,
    MAX_CANDIDATES_KEY,
)
from app.util.db import get_session

router = APIRouter(prefix="/sources")


class DirectSourcesSettings(BaseModel):
    enabled: bool
    base_url: str
    max_candidates: int


@router.get("", response_model=DirectSourcesSettings)
def get_direct_sources_settings(
    session: Annotated[Session, Depends(get_session)],
    _user: Annotated[DetailedUser, Security(AnyAuth(GroupEnum.admin))],
):
    return DirectSourcesSettings(
        enabled=indexer_configuration_cache.get_bool(session, ENABLED_KEY) is not False,
        base_url=indexer_configuration_cache.get(session, BASE_URL_KEY)
        or DEFAULT_BASE_URL,
        max_candidates=indexer_configuration_cache.get_int(
            session, MAX_CANDIDATES_KEY, DEFAULT_MAX_CANDIDATES
        )
        or DEFAULT_MAX_CANDIDATES,
    )


class UpdateDirectSourcesSettings(BaseModel):
    enabled: bool
    base_url: str
    max_candidates: int


@router.patch("", status_code=204)
def update_direct_sources_settings(
    body: UpdateDirectSourcesSettings,
    session: Annotated[Session, Depends(get_session)],
    _user: Annotated[DetailedUser, Security(AnyAuth(GroupEnum.admin))],
):
    indexer_configuration_cache.set_bool(session, ENABLED_KEY, body.enabled)
    indexer_configuration_cache.set(session, BASE_URL_KEY, body.base_url)
    indexer_configuration_cache.set_int(
        session, MAX_CANDIDATES_KEY, body.max_candidates
    )
    return Response(status_code=204)
