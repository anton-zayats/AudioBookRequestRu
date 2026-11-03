"""Offline parser tests for the livelib provider (no network)."""

from __future__ import annotations

from pathlib import Path

from app.internal.metadata.livelib import (
    external_id_from_url,
    parse_autocomplete,
    parse_detail_jsonld,
    parse_search_results,
    parse_work_editions,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "livelib"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


JSONLD_DETAIL = """
<html>
<script type="application/ld+json">
{"@context": "https://schema.org", "@graph": [
  {"@type": "Book", "name": "Мастер и Маргарита",
   "url": "https://www.livelib.ru/book/1018128325-master-i-margarita",
   "image": {"url": "https://s1.livelib.ru/boocover/1018128325/200x305/x.jpg"},
   "author": {"name": "Михаил Булгаков", "url": "/author/408"},
   "datePublished": "2026-01-01T00:00:00+03:00",
   "genre": ["Мистика", "Русская классика"]},
  {"@type": "ItemList", "itemListElement": [
     {"item": {"name": "Белая гвардия", "url": "https://www.livelib.ru/book/1002919927-belaya"}},
     {"item": {"name": "Собачье сердце", "url": "https://www.livelib.ru/book/999-heart"}}
  ]}
]}
</script>
</html>
"""


def test_parse_search_results() -> None:
    results = parse_search_results(_read("search-master-i-margarita.html"))
    assert len(results) == 20

    top = results[0]
    assert top["work_id"] == "664551"
    assert top["title"] == "Мастер и Маргарита"
    assert top["author"] == "Михаил Булгаков"
    assert top["edition_url"] == (
        "https://www.livelib.ru/book/1018128325-master-i-margarita-mihail-bulgakov"
    )
    cover = top["cover"]
    assert cover is not None
    assert cover.startswith("https://s1.livelib.ru/boocover/1018128325/")
    assert "Мистика" in top["genres"]

    # Every card carries a canonical work id.
    assert all(item["work_id"] for item in results)


def test_parse_work_editions() -> None:
    editions = parse_work_editions(_read("work-664551.html"))
    assert len(editions) == 20
    assert editions[0]["edition_id"] == "1018128325"
    assert editions[0]["slug"] == "master-i-margarita-mihail-bulgakov"
    assert editions[0]["url"] == (
        "https://www.livelib.ru/book/1018128325-master-i-margarita-mihail-bulgakov"
    )
    ids = [edition["edition_id"] for edition in editions]
    assert len(ids) == len(set(ids))


def test_parse_detail_jsonld() -> None:
    detail = parse_detail_jsonld(JSONLD_DETAIL)
    assert detail["title"] == "Мастер и Маргарита"
    assert detail["author"] == "Михаил Булгаков"
    assert detail["year"] == "2026"
    cover = detail["cover"]
    assert cover is not None
    assert cover.startswith("https://s1.livelib.ru/boocover/")
    assert detail["url"] == "https://www.livelib.ru/book/1018128325-master-i-margarita"

    similar = detail["similar"]
    assert len(similar) == 2
    assert similar[0]["title"] == "Белая гвардия"
    assert similar[0]["url"] == "https://www.livelib.ru/book/1002919927-belaya"


def test_parse_autocomplete_extracts_titles() -> None:
    html = """
    <div><a href="/book/1-one">Мастер и Маргарита</a></div>
    <div><a href="/author/408">Михаил Булгаков</a></div>
    <div><a href="/book/1-one">Мастер и Маргарита</a></div>
    """
    assert parse_autocomplete(html) == ["Мастер и Маргарита", "Михаил Булгаков"]


def test_external_id_from_url() -> None:
    assert external_id_from_url("https://www.livelib.ru/book/123-slug") == "123"
    assert external_id_from_url("/work/1000269075-master") == "1000269075"
    assert external_id_from_url(None) == ""
    assert (
        external_id_from_url("https://www.livelib.ru/books")
        == "https://www.livelib.ru/books"
    )
