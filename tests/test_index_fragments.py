from datetime import UTC, datetime
from typing import override

import pytest
from fastapi.testclient import TestClient

from app.internal.metadata.base import MetadataProvider
from app.internal.models import Audiobook
from tests.constants import BOOK_ID
from tests.fakes import FakeMetadataProvider
from tests.http import json_list


def test_index_popular_fragment(admin_client: TestClient) -> None:
    popular = admin_client.get("/api/recommendations/popular")
    assert popular.status_code == 200
    assert json_list(popular)

    fragment = admin_client.get("/hx-popular")
    assert fragment.status_code == 200
    assert "Test Book" in fragment.text


def test_index_recent_fragment(trusted_client: TestClient) -> None:
    assert trusted_client.get("/hx-recent").status_code == 200


def _categories_provider(_session: object) -> MetadataProvider:
    class _CategoriesProvider(FakeMetadataProvider):
        @override
        async def categories(self) -> dict[str, list[Audiobook]]:
            return {
                "детективы": [
                    Audiobook(
                        source="livelib",
                        external_id="work-cat-1",
                        title="Category Book",
                        subtitle=None,
                        authors=["Cat Author"],
                        narrators=[],
                        cover_image=None,
                        release_date=datetime(2021, 1, 1, tzinfo=UTC),
                        runtime_length_min=300,
                    )
                ]
            }

    return _CategoriesProvider()


def test_index_categories_fragment(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.routers.api.recommendations.get_provider", _categories_provider
    )
    fragment = admin_client.get("/hx-categories")
    assert fragment.status_code == 200, fragment.text
    assert "Category Book" in fragment.text


def test_popular_fragment_renders_reasons(admin_client: TestClient) -> None:
    assert "Test Book" in admin_client.get("/hx-popular").text


def test_popular_shown_when_another_user_has_request(admin_client: TestClient) -> None:
    response = admin_client.get("/")
    assert response.status_code == 200
    assert "/hx-popular" in response.text


def test_popular_hidden_when_user_is_sole_requester(
    trusted_client: TestClient,
) -> None:
    response = trusted_client.get("/")
    assert response.status_code == 200
    assert "/hx-popular" not in response.text


def test_popular_hidden_when_other_requests_are_downloaded(
    admin_client: TestClient,
) -> None:
    assert admin_client.patch(f"/api/requests/{BOOK_ID}/downloaded").status_code == 204
    response = admin_client.get("/")
    assert response.status_code == 200
    assert "/hx-popular" not in response.text
