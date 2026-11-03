#!/usr/bin/env python3
"""T0.2 spike — prove livelib.ru can be scraped for Russian audiobook metadata.

Standalone proof-of-concept with NO coupling to the AudioBookRequest app.

What it demonstrates:
  1. Search  https://www.livelib.ru/find/books/<query>  (URL-encode the Cyrillic)
     and parse result cards: title, author, cover URL, genres, the canonical
     WORK id (`data-book_id`), and the edition page link (`data-link`).
  2. Fetch the book/detail page reached from the top search result and parse the
     schema.org JSON-LD (title, authors, cover, genres, description/annotation,
     publication year, ISBN, publisher, rating) plus the "similar books" list.
  3. Fetch the work's edition list  /book/<work_id>/editions  (lists editions
     with their numeric ids + slugs). NOTE: /work/<work_id> now returns HTTP 404
     for the legacy ids found in search results — see spikes/T0.2-livelib.md.
  4. (Optional) POST /main/search — the search-box autocomplete — to show the
     unofficial JSON endpoint (returns JSON wrapping an HTML snippet).

Only Python stdlib is used for HTML extraction (re + json): the rich metadata is
embedded as schema.org JSON-LD and the search cards are regular enough to parse
with regex. This lets the spike run without selectolax/bs4/lxml. For a production
parser, selectolax is the recommended dependency (see the .md).

Usage:
    python spikes/T0.2-livelib-scrape.py                     # live (network)
    python spikes/T0.2-livelib-scrape.py --use-fixtures DIR  # search+editions offline
    python spikes/T0.2-livelib-scrape.py --skip-autocomplete

Be polite to the site: single requests only, with a delay between them.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36 "
    "AudioBookRequestRu/0.1 (+spike; single-request; polite)"
)
BASE = "https://www.livelib.ru"
DELAY_SECONDS = 1.5
TOP_N = 5


# --------------------------------------------------------------------------- #
# HTTP (requests if available, otherwise stdlib urllib so the spike is deps-free)
# --------------------------------------------------------------------------- #

try:  # pragma: no cover - depends on the environment
    import requests  # type: ignore

    _REQUESTS = True
except ImportError:  # pragma: no cover
    import urllib.error
    import urllib.request

    _REQUESTS = False


def _http_get(url: str) -> tuple[int, bytes]:
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "ru-RU,ru;q=0.9"}
    if _REQUESTS:
        r = requests.get(url, headers=headers, timeout=30)
        return r.status_code, r.content
    req = urllib.request.Request(url, headers=headers)  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        # Return the status so callers can apply an HTTP/2 (curl) fallback.
        return exc.code, b""


def _curl_get_http2(url: str) -> tuple[int, bytes]:
    """livelib's ddos-guard rejects HTTP/1.1 with 403 on Next.js pages and
    requires HTTP/2; curl is used as a fallback HTTP/2 client."""
    if shutil.which("curl") is None:
        raise RuntimeError("curl (--http2) is required to reach livelib Next.js pages")
    proc = subprocess.run(
        [
            "curl", "-sS", "--http2", "-A", USER_AGENT,
            "-H", "Accept-Language: ru-RU,ru;q=0.9", "--output", "-",
            "-w", "%{http_code}", url,
        ],
        capture_output=True,
        timeout=60,
    )
    out = proc.stdout
    status = int(out.decode("utf-8", "replace")[-3:] if len(out) >= 3 else 0)
    body = out[:-3]
    return status, body


def _http_post_form(url: str, data: dict[str, str]) -> tuple[int, bytes]:
    headers = {
        "User-Agent": USER_AGENT,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": BASE + "/",
    }
    if _REQUESTS:
        r = requests.post(url, data=data, headers=headers, timeout=30)
        return r.status_code, r.content
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers)  # noqa: S310
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, resp.read()


# --------------------------------------------------------------------------- #
# Parsers (stdlib only)
# --------------------------------------------------------------------------- #

_SEARCH_CARD_SPLIT = re.compile(r'<div class="object-wrapper object-edition ll-redirect"')


def parse_search_results(html: str) -> list[dict]:
    """Parse /find/books/<query> result cards."""
    results: list[dict] = []
    for chunk in _SEARCH_CARD_SPLIT.split(html)[1:]:
        # Stop a card at the "book-data" action block so we don't bleed into the next card.
        card = chunk.split('class="book-data"')[0]

        m_link = re.search(r'data-link="([^"]+)"', card)
        m_title = re.search(r'class="title">([^<]+)</a>', card)
        m_author = re.search(r'href="/author/[^"]*"[^>]*>([^<]+)</a>', card)
        m_cover = re.search(r'object-cover"[^>]*background:url\(([^)]+)\)', card)
        genres = re.findall(r'label-genre"[^>]*>([^<]+)</a>', card)
        m_work = re.search(r'data-book_id="(\d+)"', card)

        # data-book_id is the canonical WORK id; ub-book-<id> is an identical backup.
        work_id = m_work.group(1) if m_work else None
        if work_id is None:
            m_ub = re.search(r'ub-book-(\d+)', card)
            work_id = m_ub.group(1) if m_ub else None

        results.append(
            {
                "title": m_title.group(1).strip() if m_title else None,
                "author": m_author.group(1).strip() if m_author else None,
                "cover": m_cover.group(1) if m_cover else None,
                "genres": genres,
                "work_id": work_id,
                "edition_url": (BASE + m_link.group(1)) if m_link else None,
            }
        )
    return results


def parse_detail_jsonld(html: str) -> dict:
    """Parse the schema.org JSON-LD embedded in a book/work (Next.js) page.

    Returns keys mirroring the schema.org ``Book`` node plus a ``similar`` list
    taken from the ``ItemList`` node.
    """
    out: dict = {"similar": []}
    for block in re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        for node in data.get("@graph", [data]):
            ntype = node.get("@type")
            if ntype == "Book":
                author = node.get("author") or {}
                publisher = node.get("publisher") or {}
                image = node.get("image") or {}
                rating = node.get("aggregateRating") or {}
                out.update(
                    {
                        "title": node.get("name"),
                        "url": node.get("url"),
                        "cover": image.get("url") if isinstance(image, dict) else None,
                        "genres": node.get("genre") or [],
                        "description": node.get("description"),
                        "isbn": node.get("isbn"),
                        "year": (node.get("datePublished") or "")[:4] or None,
                        "publisher": publisher.get("name") if isinstance(publisher, dict) else None,
                        "author": author.get("name") if isinstance(author, dict) else None,
                        "author_url": author.get("url") if isinstance(author, dict) else None,
                        "rating": rating.get("ratingValue") if isinstance(rating, dict) else None,
                        "rating_count": rating.get("ratingCount") if isinstance(rating, dict) else None,
                    }
                )
            elif ntype == "ItemList" and not out["similar"]:
                for item in node.get("itemListElement", []):
                    entry = item.get("item") or {}
                    out["similar"].append(
                        {
                            "title": entry.get("name") if isinstance(entry, dict) else None,
                            "url": item.get("url") or (entry.get("url") if isinstance(entry, dict) else None),
                        }
                    )
    return out


def parse_work_editions(html: str) -> list[dict]:
    """Parse /book/<work_id>/editions -> list of {edition_id, slug, url}."""
    seen: set[str] = set()
    editions: list[dict] = []
    for m in re.finditer(r'href="/book/(\d+)-([a-z0-9-]+)"', html):
        edition_id, slug = m.group(1), m.group(2)
        if edition_id in seen:
            continue
        seen.add(edition_id)
        editions.append({"edition_id": edition_id, "slug": slug, "url": f"{BASE}/book/{edition_id}-{slug}"})
    return editions


# --------------------------------------------------------------------------- #
# Fetchers
# --------------------------------------------------------------------------- #


def fetch(url: str) -> str:
    status, body = _http_get(url)
    if status != 200:
        # Next.js pages (book/work/editions) reject HTTP/1.1 -> retry via curl --http2.
        status, body = _curl_get_http2(url)
    if status != 200:
        raise RuntimeError(f"GET {url} -> HTTP {status}")
    return body.decode("utf-8", "replace")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--use-fixtures", metavar="DIR", help="parse saved HTML files instead of hitting the network")
    ap.add_argument("--skip-autocomplete", action="store_true", help="skip the /main/search autocomplete demo")
    args = ap.parse_args()

    query = "Мастер и Маргарита"
    fx = Path(args.use_fixtures) if args.use_fixtures else None

    print("=" * 78)
    print("1) SEARCH — https://www.livelib.ru/find/books/<query>")
    print("=" * 78)
    if fx:
        search_html = (fx / "search-master-i-margarita.html").read_text(encoding="utf-8")
    else:
        search_html = fetch(BASE + "/find/books/" + quote(query))
        time.sleep(DELAY_SECONDS)

    results = parse_search_results(search_html)
    print(f"parsed {len(results)} result cards. Top {TOP_N}:\n")
    for i, r in enumerate(results[:TOP_N], 1):
        print(f"  [{i}] title   = {r['title']}")
        print(f"      author  = {r['author']}")
        print(f"      cover   = {r['cover']}")
        print(f"      genres  = {', '.join(r['genres']) if r['genres'] else '-'}")
        print(f"      work_id = {r['work_id']}   (canonical WORK id from data-book_id)")
        print(f"      edition = {r['edition_url']}\n")

    top = results[0]
    assert top["work_id"], "expected a work_id on the top result"

    print("=" * 78)
    print("2) BOOK/DETAIL PAGE — schema.org JSON-LD (title, authors, cover,")
    print("   genres, description, publication year, ISBN, publisher, rating,")
    print("   similar books)")
    print("=" * 78)
    if fx:
        print("  (skipped in --use-fixtures mode: the detail page is ~690 KB, so it is")
        print("   not shipped as a fixture. Its JSON-LD schema is documented in T0.2-livelib.md.)\n")
        detail = {}
    else:
        assert top["edition_url"]
        detail_html = fetch(top["edition_url"])
        time.sleep(DELAY_SECONDS)
        detail = parse_detail_jsonld(detail_html)

    if detail:
        for k in ("title", "author", "author_url", "cover", "genres", "year", "isbn", "publisher", "rating", "rating_count"):
            if detail.get(k):
                print(f"    {k:<13}= {detail[k]}")
        desc = detail.get("description") or ""
        print(f"    description  = {desc[:160]!r}{'...' if len(desc) > 160 else ''}")
        print(f"    similar      = {len(detail['similar'])} items")
        for s in detail["similar"][:5]:
            print(f"       - {s['title']}  <{s['url']}>")

    print()
    print("=" * 78)
    print("3) WORK EDITIONS — https://www.livelib.ru/book/<work_id>/editions")
    print("=" * 78)
    if fx:
        editions_html = (fx / "work-664551.html").read_text(encoding="utf-8")
    else:
        editions_html = fetch(f"{BASE}/book/{top['work_id']}/editions")
        time.sleep(DELAY_SECONDS)
    editions = parse_work_editions(editions_html)
    print(f"  first-page edition count for work {top['work_id']}: {len(editions)}")
    for e in editions[:5]:
        print(f"    edition_id={e['edition_id']:<12} {e['url']}")

    if not args.skip_autocomplete and not fx:
        print()
        print("=" * 78)
        print("4) AUTOCOMPLETE — POST /main/search (unofficial JSON endpoint)")
        print("=" * 78)
        status, body = _http_post_form(
            BASE + "/main/search",
            {"text": "Мастер", "object_alias": "booksauthors", "result_element": "x", "width": "400", "limit": "3"},
        )
        text = body.decode("utf-8", "replace")
        parsed = json.loads(text) if text.lstrip()[:1] == "{" else {"error_code": "n/a", "content": "<non-json>"}
        print(f"  HTTP {status}, content_type looks like JSON: {text.lstrip()[:1] == '{'}")
        print(f"  error_code = {parsed.get('error_code')}")
        print(f"  content is an HTML snippet ({len(parsed.get('content', ''))} chars), e.g.:")
        print("   ", re.sub(r"\s+", " ", parsed.get("content", ""))[:200])

    print()
    print("done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)