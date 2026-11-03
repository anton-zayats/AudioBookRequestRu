"""String normalization for fuzzy matching.

`normalize_for_match` converts free-form title/author/narrator text into a
deterministic, canonical form before feeding it into the fuzzy matchers. It is
intentionally lossy in ways that make meaningful comparisons easier: case,
punctuation, whitespace and the Russian ``ё``/``Ё`` distinction are all treated
as insignificant.
"""

from __future__ import annotations

import re
import unicodedata

# Anything outside this class (lowercase Latin letters and digits, lowercase
# Cyrillic letters, and ``ё``) is treated as a separator. After the ``ё`` ->
# ``е`` fold and lowercasing below, the ``ё`` member is a no-op, but it is kept
# so the character class reads as a complete alphabet of preserved characters.
_NON_KEPT_RE = re.compile(r"[^a-z0-9а-яё]+")

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_for_match(s: str) -> str:
    """Normalize a string for fuzzy comparison.

    The normalization applies, in order:

    1. Unicode NFKC normalization (folds compatibility and full-width forms
       into canonical equivalents, e.g. ``①`` -> ``1`` and full-width Latin
       letters into their ASCII counterparts).
    2. Russian ``ё``/``Ё`` -> ``е``/``Е`` folding.
    3. Lowercasing.
    4. Replacement of every run of characters outside ``[a-z0-9а-яё]`` with a
       single space.
    5. Collapsing internal whitespace to single spaces and stripping leading /
       trailing whitespace.

    The result is deterministic and contains only lowercase Latin letters,
    digits, lowercase Cyrillic letters and single spaces.

    Args:
        s: The string to normalize.

    Returns:
        The normalized string.
    """
    normalized = unicodedata.normalize("NFKC", s)
    normalized = normalized.replace("ё", "е").replace("Ё", "Е")
    normalized = normalized.lower()
    normalized = _NON_KEPT_RE.sub(" ", normalized)
    normalized = _WHITESPACE_RE.sub(" ", normalized)
    return normalized.strip()
