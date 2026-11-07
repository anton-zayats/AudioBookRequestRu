"""Provider settings framework (metadata + direct download sources).

Both kinds of external providers are configured the same way: a small, typed set
of fields stored in the config DB under ``<provider>_<field>`` keys. The settings
page renders the fields generically, and providers read their values through the
helpers below.

This deliberately replaces the old ``app/internal/indexers`` machinery: that one
was keyed to Prowlarr source-enrichment plugins, not to providers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from aiohttp import ClientSession
from sqlmodel import Session

from app.util.cache import StringConfigCache

ProviderKind = Literal["metadata", "direct"]

#: Section headings for the settings page, in display order.
KIND_LABELS: dict[ProviderKind, str] = {
    "metadata": "Metadata providers",
    "direct": "Direct download sources",
}


@dataclass(frozen=True)
class SessionContainer:
    """Session bundle handed to provider implementations."""

    session: Session
    client_session: ClientSession


provider_configuration_cache: StringConfigCache[str] = StringConfigCache()


@dataclass(frozen=True)
class ProviderField:
    """One configurable value of a provider."""

    key: str
    display_name: str
    type_: type[str | int | bool]
    default: str | int | bool
    required: bool = False
    description: str | None = None

    def is_str(self) -> bool:
        return self.type_ is str

    def is_int(self) -> bool:
        return self.type_ is int

    def is_bool(self) -> bool:
        return self.type_ is bool


@dataclass(frozen=True)
class ProviderDefinition:
    """A provider and the settings it exposes."""

    name: str
    kind: ProviderKind
    description: str
    meta_description: str = ""
    fields: tuple[ProviderField, ...] = ()

    @property
    def enabled_key(self) -> str:
        return f"{self.name}_enabled"

    @property
    def enabled_field(self) -> ProviderField:
        for provider_field in self.fields:
            if provider_field.key == self.enabled_key:
                return provider_field
        raise ValueError(f"{self.name} has no enabled field")


@dataclass(frozen=True)
class ProviderValues:
    """A provider definition plus its current values (for rendering)."""

    definition: ProviderDefinition
    enabled: bool
    values: dict[str, str | int | bool]

    def value(self, provider_field: ProviderField) -> str | int | bool:
        return self.values[provider_field.key]


def field_value(session: Session, provider_field: ProviderField) -> str | int | bool:
    """Read a field, falling back to its default when unset."""
    if provider_field.is_bool():
        stored = provider_configuration_cache.get_bool(session, provider_field.key)
        return stored if stored is not None else bool(provider_field.default)
    if provider_field.is_int():
        default = int(provider_field.default) if provider_field.default != "" else 0
        return provider_configuration_cache.get_int(
            session, provider_field.key, default
        )
    stored = provider_configuration_cache.get(session, provider_field.key)
    if stored is None or (provider_field.required and not stored.strip()):
        return str(provider_field.default)
    return stored


def get_str(session: Session, provider_field: ProviderField) -> str:
    return str(field_value(session, provider_field))


def get_int(session: Session, provider_field: ProviderField) -> int:
    return int(field_value(session, provider_field))


def get_bool(session: Session, provider_field: ProviderField) -> bool:
    return bool(field_value(session, provider_field))


def set_field_value(
    session: Session, provider_field: ProviderField, raw: str | bool | int
) -> None:
    """Coerce and store a field value, raising ``ValueError`` when invalid."""
    if provider_field.is_bool():
        enabled = (
            raw
            if isinstance(raw, bool)
            else str(raw).lower()
            in {
                "1",
                "true",
                "on",
                "yes",
            }
        )
        provider_configuration_cache.set_bool(session, provider_field.key, enabled)
        return
    if provider_field.is_int():
        try:
            value = int(str(raw).strip())
        except ValueError as exc:
            raise ValueError(f"{provider_field.display_name} must be a number") from exc
        provider_configuration_cache.set_int(session, provider_field.key, value)
        return
    value = str(raw).strip()
    if provider_field.required and not value:
        raise ValueError(f"{provider_field.display_name} is required")
    provider_configuration_cache.set(session, provider_field.key, value)


def is_enabled(session: Session, definition: ProviderDefinition) -> bool:
    return bool(field_value(session, definition.enabled_field))


def values_for(session: Session, definition: ProviderDefinition) -> ProviderValues:
    return ProviderValues(
        definition=definition,
        enabled=is_enabled(session, definition),
        values={
            provider_field.key: field_value(session, provider_field)
            for provider_field in definition.fields
        },
    )
