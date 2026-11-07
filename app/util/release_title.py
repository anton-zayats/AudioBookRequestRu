"""Release title cleanup and grouping keys.

Tracker uploads put quality noise in the title
(``Book.Name.2020.MP3.64kbps-GRP``) while direct sources (knigavuhe) carry a clean
book title plus authors. Grouping release bookmarks by title+author therefore
needs a normalization step that strips that upload noise first.

Everything here is lossy on purpose: it is only used for grouping and for the
matching done against the Audiobookshelf library, never for display titles
stored on a release.
"""

from __future__ import annotations

import re
import unicodedata

from app.util.normalize import normalize_for_match

#: Matches a bracketed group, e.g. ``[mp3, 64kbps]``, ``(Narrator)``.
_BRACKETS_RE = re.compile(r"[\[\(\{][^\[\]\(\)\{\}]*[\]\)\}]")
_SEPARATORS_RE = re.compile(r"[._]+")
_WHITESPACE_RE = re.compile(r"\s+")

#: Tokens that never carry meaning for grouping.
_NOISE_TOKENS = frozenset(
    {
        "mp3",
        "m4a",
        "m4b",
        "flac",
        "aac",
        "ogg",
        "oga",
        "opus",
        "wav",
        "ape",
        "alac",
        "aax",
        "aaxc",
        "wma",
        "vbr",
        "cbr",
        "kbps",
        "kbit",
        "kbits",
        "khz",
        "audiobook",
        "audiobooks",
        "unabridged",
    }
)

#: Bitrates that only ever appear as quality markers.
_BITRATES = frozenset({32, 48, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320})

_BITRATE_RE = re.compile(r"^(\d{2,5})\s?(kbps|kbit|kbits|khz)$")
_TRAILING_JUNK = " \t-–—_,;:|"


def _is_noise_token(token: str) -> bool:
    value = token.strip(_TRAILING_JUNK).lower()
    if not value:
        return False
    if value in _NOISE_TOKENS:
        return True
    match = _BITRATE_RE.match(value) or _BITRATE_RE.match(value.split("-", 1)[0])
    if match:
        return True
    if value.isdigit():
        return int(value) in _BITRATES
    return False


def clean_release_title(title: str) -> str:
    """Strip upload/quality noise from a release title.

    ``"Test.Book.2020.MP3.64kbps-GRP"`` -> ``"Test Book 2020-GRP"`` and
    ``"Test Book mp3"`` -> ``"Test Book"``. Bracketed groups that consist only
    of noise tokens are dropped; bracketed narrator names are kept.

    The original title is returned unchanged when cleaning would leave nothing
    behind.
    """
    normalized = unicodedata.normalize("NFKC", title)

    def _replace_bracket(match: re.Match[str]) -> str:
        inner = match.group(0)[1:-1]
        tokens = [token for token in re.split(r"[\s.,_\-]+", inner) if token]
        if tokens and all(_is_noise_token(token) for token in tokens):
            return " "
        return match.group(0)

    cleaned = _BRACKETS_RE.sub(_replace_bracket, normalized)
    cleaned = _SEPARATORS_RE.sub(" ", cleaned)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()

    # Drop quality tokens anywhere but at the start: a title like ``mp3`` alone
    # must survive, while ``Book.MP3.64kbps`` becomes ``Book``.
    tokens = [
        token
        for index, token in enumerate(cleaned.split(" "))
        if index == 0 or not _is_noise_token(token)
    ]
    cleaned = " ".join(tokens).strip(_TRAILING_JUNK)

    return cleaned or title.strip()


def release_title_key(title: str) -> str:
    """Normalized grouping key for a release title (empty author part)."""
    return normalize_for_match(clean_release_title(title))


def release_author_key(authors: list[str] | None) -> str:
    """Normalized, order-independent author key (empty when unknown)."""
    normalized = {normalize_for_match(author) for author in authors or [] if author}
    normalized.discard("")
    return "/".join(sorted(normalized))


def release_group_key(title: str, authors: list[str] | None = None) -> str:
    """Grouping key for a release: cleaned title plus authors when known."""
    title_key = release_title_key(title)
    author_key = release_author_key(authors)
    if not author_key:
        return title_key
    return f"{title_key}|{author_key}"
