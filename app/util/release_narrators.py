"""Narrator extraction from tracker upload titles.

Russian trackers (rutracker being the main one) use a fairly consistent shape
for audiobook uploads::

    Автор - Название [Исполнитель, Год, Битрейт, Формат]

e.g. ``Достоевский Фёдор - Идиот [Александр Бордуков, 2025, 320 kbps, MP3]``.
The narrator is the **first comma-separated field inside the trailing bracket**.
Some uploads put a genre there instead
(``[Александр Васильев, 2009, Русская классика, 96 kbps]`` — narrator still
first), so candidates are validated against years, bitrates, audio formats,
sample rates and genre words before being accepted. Anything ambiguous yields
``[]`` on purpose: a wrong narrator is worse than an empty column, and the user
can always pick the release by hand.

Supported shapes:

1. explicit marker anywhere: ``Исполнитель: X``, ``Читает X``, ``(Читает X)``, ``исп. X``, ``Чтец: X``
2. trailing bracket with metadata: ``[X, 2020, 128 kbps, MP3]``
3. trailing bracket with just a name: ``[X]``
4. nothing recognisable -> ``[]``
"""

from __future__ import annotations

import re

from app.util.normalize import normalize_for_match

#: ``2020``, ``2014 г.``
_YEAR_RE = re.compile(r"^\d{4}\s*(г\.?|год)?$")
#: ``128 kbps``, ``64 Kbps.``, ``96кбит``
_BITRATE_RE = re.compile(r"^\d{2,4}\s*(kbps|kbit|kb/s|кбит)", re.IGNORECASE)
#: ``32 000 Hz``, ``44.1 кгц``
_SAMPLE_RATE_RE = re.compile(r"^\d{2}[\s.,]?\d{3}\s*(hz|кгц)", re.IGNORECASE)
#: audio container formats
_FORMAT_RE = re.compile(
    r"^(mp3|m4a|m4b|mp4|flac|aac|ogg|oga|opus|wav|ape|alac|aax|wma|mkv|mp3[,\s]*vbr)$",
    re.IGNORECASE,
)
#: markers that prefix a narrator ("Исполнитель: X", "Читает X", "исп. X")
_MARKER_RE = re.compile(
    r"^(?:исполнител[ьи]|читает|чтец|reader|исп)\b[\s.:-]*", re.IGNORECASE
)
#: trailing bracket at the end of the title
_BRACKET_RE = re.compile(r"\[([^\[\]]*)\]\s*$")
#: explicit marker with the value following it
_EXPLICIT_RE = re.compile(
    r"(?<![а-яёa-z])(?:исполнител[ьи]|читает|чтец|reader|исп)\b\s*[:\-–—]?\s*"
    + r"([^;,|)\]}\n]+)",
    re.IGNORECASE,
)
#: parenthetical noise that is not part of a name, e.g. ``VanLionHeart(ЛИ)``
_PAREN_NOISE_RE = re.compile(r"\([^)]*\)")

#: Fields that look like a name but never are.
_IGNORED = frozenset(
    {
        "разные",
        "разные артисты",
        "разные исполнители",
        "артисты театров",
        "неизвестен",
        "неизвестный",
        "автор",
        "авторы",
        "сборник",
        "лицензия",
    }
)

#: Words that mark a genre field rather than a narrator.
_GENRE_WORDS = frozenset(
    {
        "классика",
        "проза",
        "фантастика",
        "фэнтези",
        "детектив",
        "триллер",
        "история",
        "биография",
        "мемуары",
        "психология",
        "поэзия",
        "сказки",
        "детская",
        "аудиокнига",
        "аудиоспектакль",
        "радиоспектакль",
        "роман",
        "повесть",
    }
)

#: A narrator field is never longer than this (long fields are descriptions).
_MAX_LEN = 60


def _clean(candidate: str) -> str:
    value = candidate.strip().strip("«»\"'“”")
    value = _MARKER_RE.sub("", value).strip()
    value = _PAREN_NOISE_RE.sub("", value).strip()
    return re.sub(r"\s+", " ", value).strip(" .-–—,;")


def _looks_like_narrator(candidate: str) -> bool:
    value = _clean(candidate)
    if not value or len(value) > _MAX_LEN:
        return False

    normalized = normalize_for_match(value)
    if not normalized or normalized in _IGNORED:
        return False
    if _YEAR_RE.match(value) or _BITRATE_RE.match(value):
        return False
    if _SAMPLE_RATE_RE.match(value) or _FORMAT_RE.match(value):
        return False
    if re.fullmatch(r"[\d\s.,]+", value):
        return False
    if not re.search(r"[^\W\d_]", value):
        return False

    # Genre fields ("Русская классика") are never narrators.
    words = set(normalized.split(" "))
    return not words & _GENRE_WORDS


def _split_fields(content: str) -> list[str]:
    parts = re.split(r"[,;]", content)
    return [part for part in parts if part.strip()]


def extract_narrators(title: str) -> list[str]:
    """Extract narrator name(s) from a release title; ``[]`` when unsure."""
    if not title.strip():
        return []

    explicit = _EXPLICIT_RE.search(title)
    if explicit is not None:
        candidate = _clean(explicit.group(1))
        if _looks_like_narrator(candidate):
            return [candidate]

    bracket = _BRACKET_RE.search(title)
    if bracket is not None:
        for field in _split_fields(bracket.group(1)):
            candidate = _clean(field)
            if _looks_like_narrator(candidate):
                return [candidate]

    return []
