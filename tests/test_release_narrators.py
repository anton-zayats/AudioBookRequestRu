"""Narrator extraction from tracker upload titles (rutracker shapes)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.util.release_narrators import extract_narrators

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "rutracker" / "titles.json"


def _cases() -> list[dict[str, object]]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    cases = data["cases"]
    assert isinstance(cases, list)
    return cases


@pytest.mark.parametrize(
    "case",
    _cases(),
    ids=[str(case["title"])[:60] for case in _cases()],
)
def test_extract_narrators_from_rutracker_titles(case: dict[str, object]) -> None:
    title = case["title"]
    expected = case["narrators"]
    assert isinstance(title, str)
    assert isinstance(expected, list)
    assert extract_narrators(title) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Книга [Иван Литвинов]", ["Иван Литвинов"]),
        ("Книга. Исполнитель: Иван Литвинов", ["Иван Литвинов"]),
        ("Книга (исп. Пётр Маркин)", ["Пётр Маркин"]),
        ("Книга [Разные исполнители, 2020, 128 kbps]", []),
        ("Книга [MP3]", []),
        ("Книга [128 kbps]", []),
        ("Книга [2019, 96 kbps, MP3]", []),
        ("Книга", []),
        ("", []),
    ],
)
def test_extract_narrators_edge_cases(title: str, expected: list[str]) -> None:
    assert extract_narrators(title) == expected


def test_release_gets_narrator_from_tracker_title(
    admin_client,
    db,  # pyright: ignore[reportMissingParameterType, reportUnknownParameterType]
) -> None:
    from sqlmodel import select

    from app.internal.models import Release
    from tests.fakes import torrent_source
    from tests.http import json_object

    source = torrent_source(
        guid="torrent:narrator",
        title="Достоевский Фёдор - Идиот [Александр Бордуков, 2025, 320 kbps, MP3]",
    )
    # Tracker sources arrive without provider metadata; only the title carries it.
    source.book_metadata.narrators = []
    response = admin_client.post(
        "/api/releases/requests", json=source.model_dump(mode="json")
    )
    assert response.status_code == 201, response.text
    assert json_object(response)["narrators"] == ["Александр Бордуков"]

    release = db.exec(select(Release).where(Release.guid == "torrent:narrator")).first()
    assert release is not None
    assert release.narrators == ["Александр Бордуков"]


def test_direct_provider_narrator_wins(admin_client) -> None:  # pyright: ignore[reportMissingParameterType, reportUnknownParameterType]
    from tests.fakes import direct_source
    from tests.http import json_object

    source = direct_source()
    response = admin_client.post(
        "/api/releases/requests", json=source.model_dump(mode="json")
    )
    assert response.status_code == 201, response.text
    # The provider names its reader; the title is not parsed on top of it.
    assert json_object(response)["narrators"] == ["Reader B"]


def test_prowlarr_sources_expose_extracted_narrators(
    admin_client,
    prowlarr,  # pyright: ignore[reportMissingParameterType, reportUnknownParameterType]
) -> None:
    from tests.http import as_dict, as_list, json_list

    prowlarr.state["search_results"] = [
        {
            "guid": "rutracker:1",
            "indexerId": 1,
            "indexer": "RuTracker",
            "title": "Булгаков Михаил - Мастер и Маргарита [Виктор Зозулин, 2006, 128 kbps]",
            "size": 500_000_000,
            "infoUrl": None,
            "indexerFlags": [],
            "downloadUrl": None,
            "magnetUrl": "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
            "publishDate": "2006-01-01T00:00:00",
            "protocol": "torrent",
            "seeders": 10,
            "leechers": 0,
        }
    ]

    response = admin_client.get("/api/search", params={"q": "мастер и маргарита"})
    assert response.status_code == 200, response.text
    releases = [as_dict(item) for item in as_list(json_list(response))]
    tracker = [item for item in releases if item["guid"] == "rutracker:1"]
    assert len(tracker) == 1
    metadata = as_dict(tracker[0]["book_metadata"])
    assert metadata["narrators"] == ["Виктор Зозулин"]
