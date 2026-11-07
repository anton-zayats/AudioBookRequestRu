"""Settings page for metadata providers and direct download sources."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Security
from sqlmodel import Session

from app.internal.auth.authentication import ABRAuth, DetailedUser
from app.internal.models import GroupEnum
from app.internal.providers.configuration import set_field_value
from app.internal.providers.definitions import definition_for, grouped_values
from app.util.db import get_session
from app.util.templates import catalog_response, catalog_response_toast
from app.util.toast import ToastException

router = APIRouter(prefix="/providers")


@router.get("")
def read_providers(
    session: Annotated[Session, Depends(get_session)],
    admin_user: Annotated[DetailedUser, Security(ABRAuth(GroupEnum.admin))],
):
    return catalog_response(
        "Settings.Providers",
        user=admin_user,
        groups=grouped_values(session),
    )


@router.post("/hx-save")
async def save_provider(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    admin_user: Annotated[DetailedUser, Security(ABRAuth(GroupEnum.admin))],
    provider: Annotated[str, Form()],
):
    _ = admin_user
    definition = definition_for(provider)
    if definition is None:
        raise ToastException("Unknown provider", "error")

    form = dict(await request.form())
    for provider_field in definition.fields:
        if provider_field.is_bool():
            raw: str | bool = provider_field.key in form
        else:
            submitted = form.get(provider_field.key)
            raw = (
                submitted if isinstance(submitted, str) else str(provider_field.default)
            )
        try:
            set_field_value(session, provider_field, raw)
        except ValueError as e:
            raise ToastException(str(e), "error") from e

    return catalog_response_toast(
        "Settings.ProvidersContent",
        f"{definition.name} settings saved",
        "success",
        user=admin_user,
        groups=grouped_values(session),
    )
