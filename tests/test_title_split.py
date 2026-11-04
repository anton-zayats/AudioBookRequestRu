"""Title-splitting for combined livelib editions (P10)."""

from __future__ import annotations

from app.util.title import split_combined_title


def test_split_combined_title_on_period() -> None:
    assert split_combined_title("Господа Головлёвы. Сказки") == [
        "Господа Головлёвы",
        "Сказки",
    ]


def test_split_combined_title_on_slash() -> None:
    assert split_combined_title("Война и мир / Анна Каренина") == [
        "Война и мир",
        "Анна Каренина",
    ]


def test_split_combined_title_three_parts() -> None:
    assert split_combined_title("Одно. Два. Три") == ["Одно", "Два", "Три"]


def test_split_combined_title_single_work_is_empty() -> None:
    assert split_combined_title("Господа Головлёвы") == []
