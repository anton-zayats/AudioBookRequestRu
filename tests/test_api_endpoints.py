"""Exercise every /api endpoint against mock external services."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from tests.constants import MANUAL_ID
from tests.fakes import fake_provider, fake_search_releases
from tests.http import as_dict, as_list, json_list, json_object
from tests.mock_services import MockServer


def test_health(admin_client: TestClient) -> None:
    assert admin_client.get("/api/health").status_code == 200


# -- users -----------------------------------------------------------------
def test_users(admin_client: TestClient, trusted_client: TestClient) -> None:
    assert admin_client.get("/api/users/").status_code == 200
    assert trusted_client.get("/api/users/").status_code == 403
    assert json_object(admin_client.get("/api/users/me"))["username"] == "root"
    assert admin_client.get("/api/users/root").status_code == 200
    assert admin_client.get("/api/users/missing").status_code == 404

    created = admin_client.post(
        "/api/users/",
        json={"username": "new", "password": "pw", "group": "trusted"},
    )
    assert created.status_code == 201
    assert json_object(created)["username"] == "new"

    duplicate = admin_client.post(
        "/api/users/", json={"username": "new", "password": "pw"}
    )
    assert duplicate.status_code == 409

    updated = admin_client.put(
        "/api/users/new", json={"group": "untrusted", "extra_data": "x"}
    )
    assert updated.status_code == 200
    assert json_object(updated)["group"] == "untrusted"

    assert (
        admin_client.put("/api/users/root", json={"group": "trusted"}).status_code
        == 400
    )
    assert admin_client.delete("/api/users/root").status_code == 400
    assert admin_client.delete("/api/users/new").status_code == 204
    assert admin_client.delete("/api/users/new").status_code == 404


# -- account ---------------------------------------------------------------
def test_account_api_keys(trusted_client: TestClient) -> None:
    assert json_list(trusted_client.get("/api/settings/account/api-keys")) == []

    created = trusted_client.post(
        "/api/settings/account/api-keys", json={"name": "test"}
    )
    assert created.status_code == 200
    key_id = json_object(created)["key"]
    assert key_id

    duplicate = trusted_client.post(
        "/api/settings/account/api-keys", json={"name": "test"}
    )
    assert duplicate.status_code == 400

    keys = json_list(trusted_client.get("/api/settings/account/api-keys"))
    assert len(keys) == 1
    kid = as_dict(keys[0])["id"]
    assert (
        trusted_client.patch(f"/api/settings/account/api-keys/{kid}/toggle").status_code
        == 204
    )
    assert (
        trusted_client.delete(f"/api/settings/account/api-keys/{kid}").status_code
        == 204
    )
    assert (
        trusted_client.patch(
            "/api/settings/account/api-keys/not-a-uuid/toggle"
        ).status_code
        == 400
    )
    assert (
        trusted_client.patch(
            f"/api/settings/account/api-keys/{uuid.uuid4()}/toggle"
        ).status_code
        == 404
    )


def test_account_password(trusted_client: TestClient) -> None:
    bad = trusted_client.put(
        "/api/settings/account/password",
        json={
            "old_password": "wrong",
            "new_password": "newpw",
            "confirm_password": "newpw",
        },
    )
    assert bad.status_code == 400

    changed = trusted_client.put(
        "/api/settings/account/password",
        json={
            "old_password": "trustedpass",
            "new_password": "newpw",
            "confirm_password": "newpw",
        },
    )
    assert changed.status_code == 204


# -- download settings -----------------------------------------------------
def test_download_settings(admin_client: TestClient) -> None:
    body = {
        "auto_download": True,
        "flac_range": {"from_kbits": 1, "to_kbits": 1000},
        "m4b_range": {"from_kbits": 1, "to_kbits": 1000},
        "mp3_range": {"from_kbits": 1, "to_kbits": 1000},
        "unknown_audio_range": {"from_kbits": 1, "to_kbits": 1000},
        "unknown_range": {"from_kbits": 1, "to_kbits": 1000},
        "min_seeders": 3,
        "name_ratio": 70,
        "title_ratio": 80,
    }
    assert admin_client.patch("/api/settings/download", json=body).status_code == 204
    got = json_object(admin_client.get("/api/settings/download"))
    assert got["auto_download"] is True
    assert got["min_seeders"] == 3
    assert got["name_ratio"] == 70


# -- security --------------------------------------------------------------
def test_security_basic(admin_client: TestClient) -> None:
    got = admin_client.get("/api/settings/security")
    assert got.status_code == 200
    assert json_object(got)["login_type"] == "basic"

    updated = admin_client.patch(
        "/api/settings/security",
        json={
            "login_type": "basic",
            "access_token_expiry": 120,
            "min_password_length": 3,
        },
    )
    assert updated.status_code == 204
    got = json_object(admin_client.get("/api/settings/security"))
    assert got["access_token_expiry"] == 120
    assert got["min_password_length"] == 3

    invalid = admin_client.patch(
        "/api/settings/security",
        json={"login_type": "basic", "min_password_length": 0},
    )
    assert invalid.status_code == 400

    assert admin_client.post("/api/settings/security/reset-auth").status_code == 204


def test_security_oidc(admin_client: TestClient, oidc_service: MockServer) -> None:
    body = {
        "login_type": "oidc",
        "oidc_endpoint": f"{oidc_service.base_url}/oidc/config",
        "oidc_client_id": "mock-client",
        "oidc_client_secret": "mock-secret",
        "oidc_scope": "openid profile",
        "oidc_username_claim": "preferred_username",
        "oidc_group_claim": "groups",
        "oidc_redirect_scheme": "auto",
    }
    response = admin_client.patch("/api/settings/security", json=body)
    assert response.status_code == 204, response.text
    assert oidc_service.requests_to("/oidc/config")


# -- audiobookshelf --------------------------------------------------------
def test_audiobookshelf(admin_client: TestClient, abs_service: MockServer) -> None:
    got = admin_client.get("/api/settings/audiobookshelf")
    assert got.status_code == 200
    assert len(as_list(json_object(got)["abs_libraries"])) == 1

    assert (
        admin_client.put(
            "/api/settings/audiobookshelf/base-url",
            data={"base_url": f"{abs_service.base_url}/abs"},
        ).status_code
        == 204
    )
    assert (
        admin_client.put(
            "/api/settings/audiobookshelf/api-token", data={"api_token": "mock-token"}
        ).status_code
        == 204
    )
    assert (
        admin_client.put(
            "/api/settings/audiobookshelf/library", data={"library_id": "lib-1"}
        ).status_code
        == 204
    )
    assert (
        admin_client.put(
            "/api/settings/audiobookshelf/check-downloaded",
            data={"check_downloaded": "true"},
        ).status_code
        == 204
    )

    # The same endpoints accept JSON bodies
    assert (
        admin_client.put(
            "/api/settings/audiobookshelf/base-url",
            json={"base_url": f"{abs_service.base_url}/abs"},
        ).status_code
        == 204
    )
    assert (
        admin_client.put(
            "/api/settings/audiobookshelf/api-token", json={"api_token": "mock-token"}
        ).status_code
        == 204
    )
    assert (
        admin_client.put(
            "/api/settings/audiobookshelf/library", json={"library_id": "lib-1"}
        ).status_code
        == 204
    )
    assert (
        admin_client.put(
            "/api/settings/audiobookshelf/check-downloaded",
            json={"check_downloaded": False},
        ).status_code
        == 204
    )
    got = json_object(admin_client.get("/api/settings/audiobookshelf"))
    assert got["abs_base_url"] == f"{abs_service.base_url}/abs"
    assert got["abs_api_token"] == "mock-token"
    assert got["abs_library_id"] == "lib-1"
    assert got["abs_check_downloaded"] is False
    assert (
        admin_client.put(
            "/api/settings/audiobookshelf/check-downloaded",
            json={"check_downloaded": True},
        ).status_code
        == 204
    )

    tested = admin_client.get("/api/settings/audiobookshelf/test-connection")
    assert tested.status_code == 200, tested.text
    tested_body = json_object(tested)
    assert tested_body["library_count"] == 1
    assert tested_body["triggered_scan"] is True


# -- prowlarr --------------------------------------------------------------
def test_prowlarr_settings(admin_client: TestClient, prowlarr: MockServer) -> None:
    got = admin_client.get("/api/settings/prowlarr")
    assert got.status_code == 200, got.text
    data = json_object(got)
    assert isinstance(data["base_url"], str)
    assert data["base_url"].endswith("/prowlarr")
    indexers = as_dict(data["indexers"])
    assert indexers["state"] == "ok"
    assert "1" in as_dict(indexers["indexers"])

    assert (
        admin_client.put(
            "/api/settings/prowlarr/api-key", json={"api_key": "new-key"}
        ).status_code
        == 204
    )
    assert (
        admin_client.put(
            "/api/settings/prowlarr/base-url",
            json={"base_url": prowlarr.base_url + "/prowlarr"},
        ).status_code
        == 204
    )
    assert (
        admin_client.put(
            "/api/settings/prowlarr/categories", json={"categories": [3030, 3040]}
        ).status_code
        == 204
    )
    got = json_object(admin_client.get("/api/settings/prowlarr"))
    assert isinstance(got["base_url"], str)


def test_prowlarr_unconfigured(admin_client: TestClient) -> None:
    got = admin_client.get("/api/settings/prowlarr")
    assert got.status_code == 200
    indexers = as_dict(json_object(got)["indexers"])
    assert indexers["state"] == "failedFetch"


# -- notifications ---------------------------------------------------------
def test_notifications(admin_client: TestClient, mock_server: MockServer) -> None:
    assert admin_client.get("/api/settings/notifications").status_code == 200

    created = admin_client.post(
        "/api/settings/notifications",
        json={
            "name": "mock",
            "url": f"{mock_server.base_url}/notify/hook",
            "event_type": "onNewRequest",
            "body": '{"text": "{bookTitle}"}',
            "body_type": "json",
            "headers": '{"X-Test": "yes"}',
        },
    )
    assert created.status_code == 200, created.text
    notification = json_object(created)
    notif_id = notification["id"]
    assert notification["enabled"] is True

    assert len(json_list(admin_client.get("/api/settings/notifications"))) == 1

    tested = admin_client.post(f"/api/settings/notifications/{notif_id}/test")
    assert tested.status_code == 200, tested.text
    posted = mock_server.requests_to("/notify/hook")
    assert posted
    assert posted[-1]["headers"].get("X-Test") == "yes"

    toggled = admin_client.patch(f"/api/settings/notifications/{notif_id}/enable")
    assert toggled.status_code == 200
    assert json_object(toggled)["enabled"] is False

    inline = admin_client.post(
        "/api/settings/notifications/test",
        json={
            "name": "inline",
            "url": f"{mock_server.base_url}/notify/inline",
            "event_type": "onNewRequest",
            "body": "hello",
            "body_type": "text",
            "headers": "{}",
        },
    )
    assert inline.status_code == 200, inline.text
    assert mock_server.requests_to("/notify/inline")

    deleted = admin_client.delete(f"/api/settings/notifications/{notif_id}")
    assert deleted.status_code == 204
    assert (
        admin_client.delete(f"/api/settings/notifications/{notif_id}").status_code
        == 404
    )


def test_notifications_validation(admin_client: TestClient) -> None:
    invalid_headers = admin_client.post(
        "/api/settings/notifications",
        json={
            "name": "bad",
            "url": "http://example.com",
            "event_type": "onNewRequest",
            "body": "{}",
            "body_type": "json",
            "headers": "not json",
        },
    )
    assert invalid_headers.status_code == 400

    invalid_event = admin_client.post(
        "/api/settings/notifications",
        json={
            "name": "bad",
            "url": "http://example.com",
            "event_type": "nope",
            "body": "{}",
            "body_type": "json",
        },
    )
    assert invalid_event.status_code == 400

    invalid_body = admin_client.post(
        "/api/settings/notifications",
        json={
            "name": "bad",
            "url": "http://example.com",
            "event_type": "onNewRequest",
            "body": "[1]",
            "body_type": "json",
        },
    )
    assert invalid_body.status_code == 422


# -- providers -------------------------------------------------------------
def test_providers_list(admin_client: TestClient) -> None:
    got = admin_client.get("/api/providers")
    assert got.status_code == 200, got.text
    providers = [as_dict(item) for item in json_list(got)]
    assert [provider["name"] for provider in providers] == ["livelib", "knigavuhe"]

    livelib = providers[0]
    assert livelib["kind"] == "metadata"
    fields = {
        as_dict(field)["key"]: as_dict(field)["value"]
        for field in as_list(livelib["fields"])
    }
    assert fields["livelib_base_url"] == "https://www.livelib.ru"
    assert fields["livelib_enabled"] is True

    sections = json_object(admin_client.get("/api/providers/sections"))
    assert set(sections) == {"Metadata providers", "Direct download sources"}


def test_update_provider(admin_client: TestClient) -> None:
    updated = admin_client.patch(
        "/api/providers/livelib",
        json={"livelib_request_delay_ms": "750", "livelib_enabled": "false"},
    )
    assert updated.status_code == 204, updated.text

    providers = [
        as_dict(item) for item in json_list(admin_client.get("/api/providers"))
    ]
    fields = {
        as_dict(field)["key"]: as_dict(field)["value"]
        for field in as_list(providers[0]["fields"])
    }
    assert fields["livelib_request_delay_ms"] == 750
    assert fields["livelib_enabled"] is False

    assert (
        admin_client.patch("/api/providers/unknown", json={"x": "y"}).status_code == 404
    )
    assert (
        admin_client.patch("/api/providers/livelib", json={"nope": "y"}).status_code
        == 400
    )
    assert admin_client.patch("/api/providers/livelib", json=[1, 2]).status_code == 400
    assert (
        admin_client.patch(
            "/api/providers/livelib", json={"livelib_request_delay_ms": "abc"}
        ).status_code
        == 400
    )


# -- requests --------------------------------------------------------------
def test_manual_requests(admin_client: TestClient) -> None:
    created = admin_client.post(
        "/api/requests/manual",
        json={"title": "New Manual", "author": "A,B", "narrator": "C"},
    )
    assert created.status_code == 201

    listed = admin_client.get("/api/requests/manual")
    assert listed.status_code == 200
    ids = [as_dict(item)["id"] for item in json_list(listed)]
    assert str(MANUAL_ID) in ids

    updated = admin_client.put(
        f"/api/requests/manual/{MANUAL_ID}",
        json={"title": "Updated", "author": "X"},
    )
    assert updated.status_code == 204

    downloaded = admin_client.patch(f"/api/requests/manual/{MANUAL_ID}/downloaded")
    assert downloaded.status_code == 204

    deleted = admin_client.delete(f"/api/requests/manual/{MANUAL_ID}")
    assert deleted.status_code == 204
    assert admin_client.delete(f"/api/requests/manual/{MANUAL_ID}").status_code == 404


# -- search ----------------------------------------------------------------
def test_search(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.routers.api.search.search_releases", fake_search_releases)

    results = admin_client.get("/api/search", params={})
    assert results.status_code == 200, results.text
    assert json_list(results) == []

    results = admin_client.get("/api/search?q=harry+potter&num_results=1")
    assert results.status_code == 200, results.text
    releases = json_list(results)
    assert len(releases) == 1
    release = as_dict(releases[0])
    assert release["guid"] == "torrent:1"
    assert release["protocol"] == "torrent"
    assert release["title"] == "Fake harry potter mp3"
    assert as_dict(release["book_metadata"])["narrators"] == ["Narrator A"]


def test_search_suggestions(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.routers.api.search.get_provider", fake_provider)
    suggestions = admin_client.get("/api/search/suggestions?q=harry")
    assert suggestions.status_code == 200, suggestions.text
    assert json_list(suggestions) == ["Fake Suggestion"]


# -- openapi ---------------------------------------------------------------
def _get(value: object, *keys: str) -> object:
    for key in keys:
        value = as_dict(value)[key]
    return value


def test_openapi_spec(admin_client: TestClient) -> None:
    spec = json_object(admin_client.get("/openapi.json"))
    paths = as_dict(spec["paths"])

    # Manually parsed bodies are still documented.
    provider = as_dict(_get(paths, "/api/providers/{provider}", "patch"))
    assert "requestBody" in provider
    assert "204" in as_dict(provider["responses"])

    for endpoint in ("base-url", "api-token", "library", "check-downloaded"):
        body = as_dict(
            _get(
                paths,
                f"/api/settings/audiobookshelf/{endpoint}",
                "put",
                "requestBody",
            )
        )
        content = as_dict(body["content"])
        assert set(content) == {
            "application/json",
            "application/x-www-form-urlencoded",
        }
        for media in content.values():
            assert as_dict(as_dict(media)["schema"])["properties"]

    event_type = as_dict(
        _get(
            spec,
            "components",
            "schemas",
            "NotificationRequest",
            "properties",
            "event_type",
        )
    )
    assert event_type["enum"] == [
        "onNewRequest",
        "onSuccessfulDownload",
        "onFailedDownload",
    ]

    # Response codes match what the handlers return.
    assert "204" in as_dict(
        _get(paths, "/api/requests/manual/{id}", "delete", "responses")
    )
    assert "201" in as_dict(_get(paths, "/api/releases/download", "post", "responses"))
    assert "201" in as_dict(
        _get(paths, "/api/releases/requests/{release_id}/download", "post", "responses")
    )
