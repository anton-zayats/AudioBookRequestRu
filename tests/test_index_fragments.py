"""Home page fragments: popular releases, lazy loading and visibility rules."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.fakes import direct_source, torrent_source


def _bookmark(client: TestClient, title: str, guid: str) -> None:
    response = client.post(
        "/api/releases/requests",
        json=direct_source(guid=guid, book_title=title).model_dump(mode="json"),
    )
    assert response.status_code == 201, response.text


def test_index_popular_fragment(
    admin_client: TestClient, trusted_client: TestClient
) -> None:
    _bookmark(trusted_client, "Test Book", "https://knigavuhe.org/book/1/")

    fragment = admin_client.get("/hx-popular")
    assert fragment.status_code == 200, fragment.text
    assert "Test Book" in fragment.text
    assert "1 request" in fragment.text


def test_popular_counts_requests(
    admin_client: TestClient,
    trusted_client: TestClient,
    untrusted_client: TestClient,
) -> None:
    # Two different users request the same release; a third user sees the count.
    _bookmark(trusted_client, "Test Book", "https://knigavuhe.org/book/1/")
    _bookmark(admin_client, "Test Book", "https://knigavuhe.org/book/1/")

    response = untrusted_client.get("/hx-popular")
    assert response.status_code == 200, response.text
    assert "2 requests" in response.text


def test_popular_excludes_own_requests(
    admin_client: TestClient, trusted_client: TestClient
) -> None:
    _bookmark(admin_client, "Test Book", "https://knigavuhe.org/book/1/")
    _bookmark(trusted_client, "Other Book", "https://knigavuhe.org/book/2/")

    response = admin_client.get("/hx-popular")
    assert response.status_code == 200, response.text
    assert "Other Book" in response.text
    assert "Test Book" not in response.text


def test_popular_shown_when_another_user_has_a_request(
    admin_client: TestClient, trusted_client: TestClient
) -> None:
    _bookmark(trusted_client, "Test Book", "https://knigavuhe.org/book/1/")

    response = admin_client.get("/")
    assert response.status_code == 200
    assert "/hx-popular" in response.text


def test_popular_hidden_when_user_is_sole_requester(
    trusted_client: TestClient,
) -> None:
    _bookmark(trusted_client, "Test Book", "https://knigavuhe.org/book/1/")

    response = trusted_client.get("/")
    assert response.status_code == 200
    assert "/hx-popular" not in response.text


def test_popular_hidden_without_requests(admin_client: TestClient) -> None:
    response = admin_client.get("/")
    assert response.status_code == 200
    assert "/hx-popular" not in response.text


def test_home_shows_bookmarked_torrents(
    admin_client: TestClient, trusted_client: TestClient
) -> None:
    assert (
        trusted_client.post(
            "/api/releases/requests",
            json=torrent_source(title="Torrent Book mp3").model_dump(mode="json"),
        ).status_code
        == 201
    )

    fragment = admin_client.get("/hx-popular")
    assert fragment.status_code == 200, fragment.text
    assert "Torrent Book mp3" in fragment.text
    assert "torrent" in fragment.text
