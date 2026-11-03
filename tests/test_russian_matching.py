"""Tests for Russian-aware matching normalization and fuzzy matchers."""

from __future__ import annotations

from app.internal.ranking.download_ranking import (
    exists_in_title,
    fuzzy_author_narrator_match,
    vaguely_exist_in_title,
)
from app.util.normalize import normalize_for_match


def test_normalize_maps_yo_to_ye() -> None:
    assert normalize_for_match("ёЁ") == "ее"
    assert normalize_for_match("Алексей Пёхов") == "алексей пехов"


def test_normalize_folds_case() -> None:
    assert normalize_for_match("ЕЖ") == "еж"
    assert normalize_for_match("Мастер и Маргарита") == "мастер и маргарита"


def test_normalize_strips_punctuation() -> None:
    assert normalize_for_match("Мастер, и… Маргарита!") == "мастер и маргарита"


def test_normalize_collapses_whitespace() -> None:
    assert normalize_for_match("  some   spaced\t text ") == "some spaced text"


def test_normalize_preserves_mixed_latin_cyrillic_letters() -> None:
    assert normalize_for_match("iPhone 12 Pro") == "iphone 12 pro"
    assert normalize_for_match("Тест 123 Mixed") == "тест 123 mixed"


def test_exists_in_title_russian_yo_folding() -> None:
    assert exists_in_title("Еж и Петруччо", "еж и петруччо", 90) is True
    assert exists_in_title("Алексей Пёхов", "алексей пехов", 90) is True


def test_fuzzy_author_narrator_match_russian() -> None:
    assert (
        fuzzy_author_narrator_match(["Сергей Лукьяненко"], ["сергей лукьяненко"], 75)
        >= 1
    )


def test_fuzzy_author_narrator_match_negative() -> None:
    assert fuzzy_author_narrator_match(["Иван Иванов"], ["Пётр Петров"], 75) == 0


def test_vaguely_exist_in_title_russian() -> None:
    assert vaguely_exist_in_title(["Пёхов"], "Алексей Пехов. Цикл", 75) == 1
