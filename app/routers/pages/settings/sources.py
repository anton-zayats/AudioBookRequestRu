from typing import Annotated

from fastapi import APIRouter, Depends, Form, Security
from sqlmodel import Session

from app.internal.auth.authentication import ABRAuth, DetailedUser
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
from app.util.templates import catalog_response, catalog_response_toast

router = APIRouter(prefix="/sources")


@router.get("")
def read_direct_sources(
    session: Annotated[Session, Depends(get_session)],
    admin_user: Annotated[DetailedUser, Security(ABRAuth(GroupEnum.admin))],
):
    return catalog_response(
        "Settings.Sources",
        user=admin_user,
        enabled=indexer_configuration_cache.get_bool(session, ENABLED_KEY) is not False,
        knigavuhe_base_url=indexer_configuration_cache.get(session, BASE_URL_KEY)
        or DEFAULT_BASE_URL,
        max_candidates=indexer_configuration_cache.get_int(
            session, MAX_CANDIDATES_KEY, DEFAULT_MAX_CANDIDATES
        )
        or DEFAULT_MAX_CANDIDATES,
    )


@router.post("/hx-save")
def update_direct_sources(
    base_url: Annotated[str, Form()],
    max_candidates: Annotated[int, Form()],
    session: Annotated[Session, Depends(get_session)],
    admin_user: Annotated[DetailedUser, Security(ABRAuth(GroupEnum.admin))],
    enabled: Annotated[bool, Form()] = False,
):
    indexer_configuration_cache.set_bool(session, ENABLED_KEY, enabled)
    indexer_configuration_cache.set(session, BASE_URL_KEY, base_url)
    indexer_configuration_cache.set_int(session, MAX_CANDIDATES_KEY, max_candidates)

    return catalog_response_toast(
        "Settings.DirectSources",
        "Direct sources updated",
        "success",
        enabled=enabled,
        knigavuhe_base_url=base_url,
        max_candidates=max_candidates,
    )
