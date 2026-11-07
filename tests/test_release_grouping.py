"""Release title cleanup + grouping keys (P12 requests grouping)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.internal.models import Release
from app.internal.releases import ReleaseView, group_release_views
from app.util.release_title import (
    clean_release_title,
    release_group_key,
    release_title_key,
)


def test_clean_release_title_strips_upload_noise() -> None:
    assert clean_release_title("Test.Book.2020.MP3.64kbps-GRP") == "Test Book 2020"
    assert clean_release_title("Test Book mp3") == "Test Book"
    assert clean_release_title("Test Book [mp3, 64 kbps]") == "Test Book"
    assert clean_release_title("Test Book (mp3)") == "Test Book"


def test_clean_release_title_keeps_meaningful_words() -> None:
    assert clean_release_title("Test Book (Иван Петров)") == "Test Book (Иван Петров)"
    assert clean_release_title("Гарри Поттер и философский камень") == (
        "Гарри Поттер и философский камень"
    )
    # A title that is nothing but noise falls back to the original.
    assert clean_release_title("mp3") == "mp3"


def test_release_title_key_ignores_case_punctuation_and_yo() -> None:
    assert release_title_key("Ёжик в тумане") == release_title_key("ежик в тумане")
    assert release_title_key("Test Book: Part 1") == release_title_key(
        "test book part 1"
    )


def test_release_group_key_separates_authors() -> None:
    assert release_group_key("Test Book", ["Author A"]) == release_group_key(
        "test.book", ["author a"]
    )
    assert release_group_key("Test Book", ["Author A"]) != release_group_key(
        "Test Book", ["Author B"]
    )
    assert release_group_key("Test Book") != release_group_key(
        "Test Book", ["Author A"]
    )


def _release(title: str, authors: list[str] | None = None) -> Release:
    return Release(
        protocol="torrent",
        guid=f"guid:{title}:{authors}",
        title=title,
        authors=authors or [],
        indexer_id=1,
        indexer="MockIndexer",
        size=1,
        publish_date=datetime(2020, 1, 1, tzinfo=UTC),
    )


def _view(title: str, authors: list[str] | None = None) -> ReleaseView:
    return ReleaseView(release=_release(title, authors))


def test_group_release_views_merges_authorless_tracker_rows() -> None:
    views = [
        _view("Test Book mp3"),
        _view("Test Book", ["Mock Author"]),
        _view("Other Book", ["Mock Author"]),
    ]

    groups = group_release_views(views)

    assert [group.title for group in groups] == ["Test Book", "Other Book"]
    assert len(groups[0].releases) == 2


def test_group_release_views_keeps_ambiguous_authorless_rows_apart() -> None:
    views = [
        _view("Сказки", ["Author A"]),
        _view("Сказки", ["Author B"]),
        _view("Сказки"),
    ]

    groups = group_release_views(views)

    assert len(groups) == 3
    assert all(len(group.releases) == 1 for group in groups)
