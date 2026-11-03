#!/usr/bin/env python3
"""
T0.3 spike — prove that knigavuhe.org audio files can be discovered by scraping.

Standalone proof-of-concept (no app coupling). It:

  1. searches knigavuhe.org for a Russian book (default: "Мастер и Маргарита"),
  2. picks the top result card,
  3. fetches the book page,
  4. extracts the player playlist JSON from the inline ``<script>`` blob and
     prints every discovered audio file URL.

Key findings encoded here (full details in T0.3-knigavuhe.md):

* Search results are server-rendered HTML. Result cards are ``<div class="bookkitem">``
  elements; the book URL slug is ``<a class="bookkitem_cover" href="/book/<slug>/">``.
* Book pages embed the whole playlist as JSON inside an inline script, in the form
  ``new BookPlayer(<book_id>, [ {id,title,url,player_data{...},error,duration,duration_float}, ... ])``.
* Audio URLs look like
  ``https://s1.knigavuhe.org/1/audio/<book_id>/<md5>/<track_no>.mp3?<v>``
* No HTML parser is required: the payload is extracted with a small bracket-matcher
  and parsed with stdlib :mod:`json` (URLs use the JSON backslash-slash escape,
  which :mod:`json` accepts natively).

This script needs no third-party dependencies: it uses ``requests`` when available and
falls back to the stdlib :mod:`urllib` otherwise. The production implementation will
reuse the app's own HTTP client instead.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request

try:  # preferred HTTP client, matching the app's dependency set
    import requests  # type: ignore

    _HAS_REQUESTS = True
except ImportError:  # pragma: no cover - stdlib fallback keeps the spike runnable anywhere
    _HAS_REQUESTS = False


BASE = "https://knigavuhe.org"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# ---- HTTP helper (polite: small delay + tiny retry) ---------------------------


def http_get(url: str, referer: str | None = None, attempts: int = 2) -> str:
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru,en;q=0.8",
    }
    if referer:
        headers["Referer"] = referer

    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            if _HAS_REQUESTS:
                resp = requests.get(url, headers=headers, timeout=30)
                resp.raise_for_status()
                return resp.text
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                return resp.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001 - spike: retry then propagate
            last_exc = exc
            time.sleep(1.0 * (attempt + 1))
    raise last_exc  # type: ignore[misc]


# ---- search page --------------------------------------------------------------


def search_first_result(query: str) -> dict[str, str]:
    """Return metadata of the top ``bookkitem`` card for ``query``."""
    url = f"{BASE}/search/?q={urllib.parse.quote(query)}"
    html = http_get(url)

    # Top card = the first <a class="bookkitem_cover" href="/book/<slug>/">.
    m = re.search(r'class="bookkitem_cover"\s+href="/book/([^"]+)/"', html)
    if not m:
        raise RuntimeError(f"no bookkitem cover link found for query {query!r}")
    slug = m.group(1)
    # Scoped to the first card only (up to the second card, or end of list).
    card_start = html.find('<div class="bookkitem">')
    card_end = html.find('<div class="bookkitem">', card_start + 1)
    card = html[card_start : card_end if card_end != -1 else html.find('</div>', card_start)]

    title = _first_group(card, r'<img class="bookkitem_cover_img" src="[^"]*" alt="([^"]*)"')
    if not title:
        # title== tag content may hold highlight spans; fall back to alt-less parse
        title = re.sub(r"<[^>]+>", "", _first_group(card, r'class="bookkitem_name">(.*?)</a>', flags=re.S) or "")
    cover = _first_group(card, r'<img class="bookkitem_cover_img" src="([^"]+)"')
    duration = _first_group(card, r'<span class="bookkitem_meta_time">([^<]+)</span>')
    # Author link text may contain highlight spans, so capture the whole anchor and strip tags.
    author = re.sub(r"<[^>]+>", "", _first_group(
        card, r'href="/author/[^"]*"[^>]*>(.*?)</a>', flags=re.S) or "").strip()
    # A card may list multiple readers ("Читает" vs "Читают"); collect all /reader/ links.
    readers = re.findall(r'href="/reader/[^"]*/">([^<]+)</a>', card)
    readers = [re.sub(r"<[^>]+>", "", r).strip() for r in readers]

    return {
        "slug": slug,
        "url": f"{BASE}/book/{slug}/",
        "title": title.strip() if title else query,
        "author": author,
        "readers": ", ".join(r for r in readers if r),
        "duration": duration or "",
        "cover": cover or "",
    }


def _first_group(text: str, pattern: str, flags: int = 0) -> str:
    m = re.search(pattern, text, flags)
    return m.group(1) if m else ""


# ---- book page / playlist JSON ------------------------------------------------


def extract_playlist(html: str) -> tuple[int, list[dict]]:
    """Parse the inline ``new BookPlayer(<book_id>, [ ...items... ])`` payload.

    The array is valid JSON (URLs are escaped as ``https:\\/\\/...``, which
    :mod:`json` accepts), so we locate the array with a small bracket matcher and
    hand it to ``json.loads`` — no HTML/JS parser needed.
    """
    i = html.find("new BookPlayer(")
    if i == -1:
        raise RuntimeError("BookPlayer payload not found on book page")
    b = html.find("[", i)
    if b == -1:
        raise RuntimeError("BookPlayer playlist array not found")

    book_id = int(re.search(r"new BookPlayer\(\s*(\d+)", html[i:]).group(1))  # type: ignore[union-attr]

    depth = 0
    in_str = False
    esc = False
    end = -1
    for pos in range(b, len(html)):
        c = html[pos]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                end = pos
                break
    if end == -1:
        raise RuntimeError("unterminated BookPlayer playlist array")

    items = json.loads(html[b : end + 1])
    if not isinstance(items, list):
        raise RuntimeError(f"expected a list, got {type(items).__name__}")
    return book_id, items


def item_summary(i: int, item: dict) -> str:
    url = item.get("url", "")
    title = item.get("title", "")
    dur = item.get("duration")
    return (
        f"  [{i:>3}] {title:<6} {dur:>5}s  {url}"
    )


# ---- main ---------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", default="Мастер и Маргарита")
    parser.add_argument("--max-urls", type=int, default=0,
                        help="cap number of printed URLs (0 = all; >0 prints first N and a count)")
    args = parser.parse_args(argv)

    print(f"== search: {args.query!r} -> {BASE}/search/?q={urllib.parse.quote(args.query)}")
    try:
        top = search_first_result(args.query)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR during search: {exc}", file=sys.stderr)
        return 1
    print(f"   top result: /book/{top['slug']}/")
    print(f"   title     : {top['title']}")
    print(f"   author    : {top['author']}")
    print(f"   reader(s) : {top['readers']}")
    print(f"   duration  : {top['duration']}")
    print(f"   cover     : {top['cover']}")

    time.sleep(1.0)  # be polite between page requests

    print(f"== fetch book page: {top['url']}")
    try:
        html = http_get(top["url"], referer=f"{BASE}/search/")
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR fetching book page: {exc}", file=sys.stderr)
        return 1

    book_id, items = extract_playlist(html)
    total_sec = sum(int(it.get("duration", 0)) for it in items)
    h, m = divmod(total_sec, 3600)
    m, s = divmod(m, 60)
    print(f"   book_id   : {book_id}")
    print(f"   tracks    : {len(items)}")
    print(f"   total     : {h}h {m:02d}m {s:02d}s")

    print("== discovered audio URLs:")
    shown = 0
    for i, item in enumerate(items, 1):
        if args.max_urls and i > args.max_urls:
            continue
        print(item_summary(i, item))
        shown += 1
    if args.max_urls and len(items) > args.max_urls:
        print(f"   ... and {len(items) - args.max_urls} more (pass --max-urls 0 for all)")
    print(f"== total audio files discovered: {len(items)} (shown {shown})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())