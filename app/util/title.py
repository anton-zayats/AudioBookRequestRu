"""Title helpers for combined-edition handling."""

from __future__ import annotations

_SEPARATORS = (". ", " / ", "; ")


def split_combined_title(title: str) -> list[str]:
    """Split a combined-edition title into its component works.

    Russian print editions often bundle several novels under a single title
    (e.g. ``"Господа Головлёвы. Сказки"``). Such a combined book rarely has a
    matching audiobook, so the split parts let the user re-search each novel
    separately. Returns an empty list when the title reads as a single work.
    """
    for separator in _SEPARATORS:
        parts = [part.strip(" .") for part in title.split(separator)]
        parts = [part for part in parts if part]
        if len(parts) > 1:
            return parts
    return []
