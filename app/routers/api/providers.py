"""Provider settings API (metadata providers and direct sources)."""

from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response, Security
from pydantic import BaseModel
from sqlmodel import Session

from app.internal.auth.authentication import AnyAuth, DetailedUser
from app.internal.models import GroupEnum
from app.internal.providers.configuration import (
    KIND_LABELS,
    ProviderDefinition,
    is_enabled,
    set_field_value,
    values_for,
)
from app.internal.providers.definitions import (
    definition_for,
    grouped_values,
    provider_definitions,
)
from app.util.db import get_session

router = APIRouter(prefix="/providers", tags=["Providers"])


class ProviderFieldResponse(BaseModel):
    key: str
    display_name: str
    type: str
    required: bool
    description: str | None = None
    value: str | int | bool


class ProviderResponse(BaseModel):
    name: str
    kind: str
    section: str
    description: str
    enabled: bool
    fields: list[ProviderFieldResponse]


def _to_response(session: Session, definition: ProviderDefinition) -> ProviderResponse:
    values = values_for(session, definition)
    return ProviderResponse(
        name=definition.name,
        kind=definition.kind,
        section=KIND_LABELS[definition.kind],
        description=definition.description,
        enabled=is_enabled(session, definition),
        fields=[
            ProviderFieldResponse(
                key=provider_field.key,
                display_name=provider_field.display_name,
                type=provider_field.type_.__name__,
                required=provider_field.required,
                description=provider_field.description,
                value=values.value(provider_field),
            )
            for provider_field in definition.fields
        ],
    )


@router.get("", response_model=list[ProviderResponse])
def list_providers(
    session: Annotated[Session, Depends(get_session)],
    _: Annotated[DetailedUser, Security(AnyAuth(GroupEnum.admin))],
):
    return [_to_response(session, definition) for definition in provider_definitions()]


@router.get("/sections")
def list_provider_sections(
    session: Annotated[Session, Depends(get_session)],
    _: Annotated[DetailedUser, Security(AnyAuth(GroupEnum.admin))],
):
    """Providers grouped by settings section (metadata / direct sources)."""
    return {
        section: [_to_response(session, values.definition) for values in providers]
        for section, providers in grouped_values(session).items()
    }


@router.patch(
    "/{provider}",
    status_code=204,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "description": "Key-value mapping of provider settings to update.",
                        "additionalProperties": True,
                    }
                }
            },
        }
    },
)
async def update_provider(
    provider: str,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    _: Annotated[DetailedUser, Security(AnyAuth(GroupEnum.admin))],
):
    definition = definition_for(provider)
    if definition is None:
        raise HTTPException(status_code=404, detail="Unknown provider")

    raw_body = cast(object, await request.json())
    if not isinstance(raw_body, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")

    for key, raw in cast(dict[object, object], raw_body).items():
        if not isinstance(key, str):
            raise HTTPException(
                status_code=400, detail="All keys in the body must be strings"
            )
        provider_field = next((f for f in definition.fields if f.key == key), None)
        if provider_field is None:
            raise HTTPException(status_code=400, detail=f"Unknown field: {key}")
        try:
            set_field_value(
                session,
                provider_field,
                raw if isinstance(raw, (str, bool, int)) else str(raw),
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    return Response(status_code=204)
