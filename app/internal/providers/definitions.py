"""Registry of provider definitions and their settings fields.

A provider is either a **metadata provider** (livelib: clean titles, authors,
covers, suggestions) or a **direct download source** (knigavuhe: MP3 releases for
aria2). Both kinds are configured through the same small typed field list, which
the settings page renders generically.
"""

from __future__ import annotations

from sqlmodel import Session

from app.internal.providers.configuration import (
    KIND_LABELS,
    ProviderDefinition,
    ProviderField,
    ProviderValues,
    field_value,
    is_enabled,
    values_for,
)

LIVELIB_DEFAULT_BASE_URL = "https://www.livelib.ru"
KNGAVUHE_DEFAULT_BASE_URL = "https://knigavuhe.org"
KNGAVUHE_DEFAULT_MAX_CANDIDATES = 3

LIVELIB = ProviderDefinition(
    name="livelib",
    kind="metadata",
    description=(
        "Russian book metadata from livelib.ru: clean titles, authors, covers and "
        "suggestions. Used for metadata and recommendations only - release search "
        "does not go through livelib."
    ),
    fields=(
        ProviderField(
            key="livelib_enabled",
            display_name="Enabled",
            type_=bool,
            default=True,
        ),
        ProviderField(
            key="livelib_base_url",
            display_name="Base URL",
            type_=str,
            default=LIVELIB_DEFAULT_BASE_URL,
            required=True,
            description="Only change this for a mirror or proxy.",
        ),
        ProviderField(
            key="livelib_request_delay_ms",
            display_name="Delay between requests (ms)",
            type_=int,
            default=500,
            description=(
                "Polite pause between livelib requests. Lower it only if livelib "
                "keeps up without rate-limiting you."
            ),
        ),
        ProviderField(
            key="livelib_timeout_seconds",
            display_name="Request timeout (s)",
            type_=int,
            default=30,
        ),
    ),
)

KNGAVUHE = ProviderDefinition(
    name="knigavuhe",
    kind="direct",
    description=(
        "Direct MP3 audiobook downloads scraped from knigavuhe.org. Releases found "
        "here are handed to the aria2 download client."
    ),
    fields=(
        ProviderField(
            key="knigavuhe_enabled",
            display_name="Enabled",
            type_=bool,
            default=True,
        ),
        ProviderField(
            key="knigavuhe_base_url",
            display_name="Base URL",
            type_=str,
            default=KNGAVUHE_DEFAULT_BASE_URL,
            required=True,
        ),
        ProviderField(
            key="knigavuhe_max_candidates",
            display_name="Max candidates",
            type_=int,
            default=KNGAVUHE_DEFAULT_MAX_CANDIDATES,
            description="How many book pages are scraped per search.",
        ),
        ProviderField(
            key="knigavuhe_request_delay_ms",
            display_name="Delay between book pages (ms)",
            type_=int,
            default=1000,
            description="Polite pause while scraping a book's playlist.",
        ),
    ),
)

PROVIDERS: tuple[ProviderDefinition, ...] = (LIVELIB, KNGAVUHE)


def provider_definitions() -> list[ProviderDefinition]:
    return list(PROVIDERS)


def definition_for(name: str) -> ProviderDefinition | None:
    for definition in PROVIDERS:
        if definition.name == name:
            return definition
    return None


def field_by_key(definition: ProviderDefinition, key: str) -> ProviderField | None:
    for provider_field in definition.fields:
        if provider_field.key == key:
            return provider_field
    return None


def grouped_values(session: Session) -> dict[str, list[ProviderValues]]:
    """Settings grouped by section heading, in display order."""
    grouped: dict[str, list[ProviderValues]] = {
        label: [] for label in KIND_LABELS.values()
    }
    for definition in PROVIDERS:
        grouped[KIND_LABELS[definition.kind]].append(values_for(session, definition))
    return grouped


def enabled_for(session: Session, name: str) -> bool:
    definition = definition_for(name)
    if definition is None:
        return False
    return is_enabled(session, definition)


def config_value(session: Session, definition: ProviderDefinition, key: str) -> object:
    provider_field = field_by_key(definition, key)
    if provider_field is None:
        return None
    return field_value(session, provider_field)
