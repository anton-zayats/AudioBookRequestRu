# AudioBookRequestRu — Implementation Plan

Living plan for forking **AudioBookRequest** (upstream `markbeep/AudioBookRequest`, currently v1.11.0) into a
Russian-language audiobook request manager.

> Status legend: `[ ]` todo · `[~]` in progress · `[x]` done

---

## 1. Goal

Adapt AudioBookRequest (ABR) so it can serve **Russian-language audiobook requests**:

1. Search audiobook **releases** directly — Prowlarr trackers + direct sources (knigavuhe) — and show **releases** as search results (no printed-book intermediary).
2. Use **livelib.ru** only as a metadata/enrichment source (clean title, author, cover) for releases that have already been grabbed.
3. Track **releases** (not books) as the requestable/downloadable unit, with per-release download status (pending / downloading / completed / failed).
4. Delegate downloads: trackers → Prowlarr (→ Decypharr, Path B); direct (knigavuhe) → aria2. Non-tracker sources stay pluggable.

---

## 2. Locked decisions

Decided with the owner (2026-11-03):

1. **Identifier model**: generalize `Audiobook` to `(source, external_id)` with a surrogate primary key, and **remove Audible entirely**.
   - Only `source = "livelib"` ships initially; the `source` column is kept for future providers.
2. **Direct downloads**: delegate direct (non-tracker) grabs to a **configurable external download client**.
   - **Confirmed (T0.1): aria2 JSON-RPC** is the direct-HTTP client. Decypharr is orthogonal (see §6).
3. **Canonical livelib identifier**: the **work**, not editions. Search cards' `data-book_id` is the work id (confirmed 20/20 cards). Canonical pair = `(work_id, edition data-link)`; full metadata/similar-books come from the edition page's JSON-LD.
4. **Direct-source framework**: **code-level plugin interface** (Python classes registered in a list), knigavuhe as the first implementation.
5. **Search pivot (2026-11-04)**: search goes straight to **Prowlarr + direct sources** and returns **releases**; livelib is used only post-grab for metadata enrichment (title/author/cover). Livelib's own search is **not** the user-facing search.

### Secondary decisions / notes

5. **Decypharr**: the current ABR→Prowlarr→download-client flow needs a separate investigation for Decypharr compatibility. Tracked in §6 / Task T0.1.
6. **Deployment**: this repository ships **no** `docker-compose.yml` (owner decision, 2026-11-07). AudioBookRequestRu is one service inside the owner's external homelab stack (Prowlarr, torrent client, aria2, Audiobookshelf). See §8.
7. **Metadata fallback**: if livelib scraping proves too fragile/complex, **knigavuhe can serve as the initial search/metadata source** as a fallback (and then livelib is used only for enrichment). Design the metadata layer to allow this swap.
8. **Every phase has acceptance tests** before it is considered finished (§7).
9. **Chaptarr**: Audible-ASIN-only, so removing Audible leaves it inert. **Confirmed (2026-11-03): remove the Chaptarr backend completely in Phase 1.**

---

## 3. Current architecture (baseline)

Key files (all under `app/`):

| Concern | Location |
|---|---|
| Data model (ASIN-keyed) | `internal/models.py` |
| Audible search/single/similar/categories | `internal/audible/` |
| Recommendations | `internal/recommendations/`, `routers/api/recommendations.py` |
| Query orchestration | `internal/query.py` |
| Prowlarr search + grab | `internal/prowlarr/prowlarr.py`, `prowlarr/util.py` |
| Download backend switch | `internal/download_backend.py` (`prowlarr` \| `chaptarr`) |
| Indexer enrichment plugin (MAM) | `internal/indexers/` |
| Source ranking (fuzzy match) | `internal/ranking/download_ranking.py`, `quality.py` |
| Configuration cache | `util/cache.py` (`StringConfigCache` / `SimpleCache`) |
| Templates | `templates/` (JinjaX + htmx) |
| DB migrations | `alembic/versions/` |
| Tests + HTTP doubles | `tests/` (`conftest.py`, `mock_services.py`) |

**Core constraints discovered:**

- `Audiobook.asin` is the primary key; `AudiobookRequest` is `(asin, user_username)`.
- ABR never downloads files itself: it delegates to Prowlarr (`POST /api/v1/search` with `{guid, indexerId}`) or Chaptarr.
- The existing "indexer" abstraction only **enriches** Prowlarr sources (does not search or download).
- All config flows through `StringConfigCache` → `Config` table; settings have UI + optional JSON file.

---

## 4. Target architecture (revised 2026-11-04 — release-centric)

**Search returns releases; livelib is a dictionary.** The user searches a title/author, ABR queries
Prowlarr (torrent/usenet) + direct sources (knigavuhe) directly, ranks the results, and shows a
**release list** (title, author, narrator, source, quality, seeders, sample flag). No printed-book
intermediary. After a release is grabbed/downloaded, ABR optionally looks it up in livelib
(title+author → clean title/author/cover) to enrich the entry and push metadata to ABS.

```
app/internal/
  sources/                  # direct (non-tracker) download-source providers + search
    base.py                 # DirectSourceProvider ABC: search(query) -> list[DirectSource]
    knigavuhe.py            # first provider
    registry.py             # search_direct_sources(query)
  prowlarr/                 # torrent/usenet search + grab (search by raw query)
  ranking/                  # rank_sources over DownloadSource, reference = the query
  metadata/
    livelib.py              # enrichment only: find book page by title+author -> cover/metadata
    registry.py
  downloads/                # DownloadClient ABC + aria2 (+ future DecypharrClient, P7)
  models.py                 # Release + ReleaseRequest + Audiobook (enrichment) + DownloadJob
  query.py                  # merge prowlarr + direct results, rank, dispatch by protocol
```

**Data model (target):**

- `Release` — the primary, requestable entity (a persisted `DownloadSource`): title, authors,
  narrators, protocol (`torrent|usenet|direct`), guid, indexer_id, indexer, size,
  seeders/leechers/grabs, file_format, publish_date, info_url, indexer_flags, download_urls.
- `ReleaseRequest` — `(release_id, user_username, created_at)`; a bookmark ("download later"), **no status workflow**. Download status lives in `DownloadJob` (P9).
- `Audiobook` — demoted to the **enrichment/grouping** entity: livelib `(source, external_id)`,
  clean title/authors/cover. No `downloaded` flag (that moves to per-release status).
- `DownloadJob` — P9 lifecycle row: `(release_request_id, client, external_id, status, error)`.
- `SearchHistory` — `(user, query, timestamps)` (+ optional result snapshot).

**Search flow:** `GET /api/search?q=` → `query_prowlarr(q)` + `search_direct_sources(q)` → merge →
`rank_sources(..., reference=q)` → return a **flat, paginated** ranked `DownloadSource` list with a
source filter (`tracker | direct | both`). Livelib is **not** in this path.

**Download dispatch:** `torrent`/`usenet` → Prowlarr grab (`start_download`); `direct` → aria2
(`start_direct_download`). Completion is tracked per-release by P9 (poll + move-to-library).

---

## 5. Phases

### Phase 0 — Investigation spikes (complete)
- [x] **T0.1** Decypharr / external download-client investigation (§6) — **resolved: aria2**.
- [x] **T0.2** Livelib scraping spike — **resolved**: `data-book_id`=work id; edition JSON-LD; detail pages need HTTP/2.
- [x] **T0.3** Knigavuhe scraping spike — **resolved**: inline `BookPlayer` JSON; mp3 URL pattern; no HTML parser needed for playlist.
- [x] **T0.4** Parser/network deps — **resolved**: `selectolax` (livelib search cards) + `httpx[http2]` (livelib detail pages, HTTP/2 required); knigavuhe = `re` + `json`, no parser.

**Acceptance:** written findings per task + recorded HTML/JSON fixtures in `tests/fixtures/` + parser approach decision.

### Phase 1 — Identifier decoupling, Audible/Chaptarr removal, migration (DONE)
- [x] `models.py`: surrogate PK (`Audiobook.id`) + `(source, external_id)` unique; `AudiobookRequest.book_id` FK.
- [x] Alembic migration `bb0ee00ba98e` (drops/rebuilds `audiobook` + `audiobookrequest`; fresh DB).
- [x] Sweep `asin` → `external_id`/`book_id`/`url` across `models`, `db_queries`, `notifications`, `audiobookshelf/client`, `query`, `prowlarr`, routers, templates.
- [x] Remove `app/internal/audible/`, `app/internal/chaptarr/`, `download_backend.py`, region settings/templates.
- [x] Loosen ABS ASIN requirement (title+author duplicate detection; dropped "skip if no ASIN").
- [x] Update tests/mocks for the new schema.

**Acceptance met:** `uv run pytest` = 74 passed; `uv run ruff check` + `ruff format --check` clean; `uv run alembic upgrade head` OK on fresh SQLite; `rg` for asin/audible/chaptarr/region clean (one explanatory comment in `models.py` only).

### Phase 2 — Metadata provider + livelib (DONE)
- [x] `metadata/base.py` (`MetadataProvider` ABC), `registry.py` (`metadata_source` config, default `livelib`).
- [x] `metadata/livelib.py`: `search` / `get_book` / `suggestions` / `similar` / `categories` via `httpx[http2]` + `selectolax`.
- [x] `db_queries.upsert_audiobooks()` — upsert by `(source, external_id)` (critical because the PK is a surrogate id).
- [x] Deps added: `httpx[http2]`, `selectolax`.
- [x] Retarget `routers/api/search.py` (search + suggestions), `recommendations` (`popular`/`recent`/`categories`/`fallback`), `pages/index.py` (`hx-categories`), templates (`Categories.jinja` restored).
- [ ] (deferred, optional) `/authors` & `/narrators` recommendations + knigavuhe-as-metadata fallback.

**Acceptance met:** 85 tests pass (`tests/test_livelib.py`, `tests/test_metadata_registry.py`, `tests/fakes.py`); `ruff check`/`format` clean; `basedpyright` = 0 errors on new `metadata/` + test files; no Audible/Chaptarr reintroduced.

### Phase 3 — Direct source model + external download client (DONE)
- [x] `DirectSource` + generalize union (`DownloadSource`); extend `rank_sources` for `direct`.
- [x] `downloads/config.py` + Settings page (host, port, secret, base dir) — `Settings/Download/Client.jinja`.
- [x] `downloads/client.py` ABC + `downloads/aria2.py` (aria2 JSON-RPC).
- [x] `query.py` direct dispatch + notifications (auto + manual per-source); `app/internal/sources/` seam returns `[]` for now.
- [x] docker-compose: aria2 service (`p3terx/aria2-pro`, RPC 6800, `./tmp/downloads`).
- [x] Direct-grab success runs the same ABS mark-downloaded + scan flow — see §11.2.
- [x] Scoping: release/narrator selection is a separate follow-up phase (Phase 6), not this plumbing.

**Acceptance met:** 90 tests pass (`tests/test_direct_download.py`); `ruff check`/`format` clean; `basedpyright` = 0 errors on `downloads/` + ranking/query; `docker compose config` OK.

### Phase 4 — Knigavuhe provider (DONE; split 4a provider + 4b matching)
- [x] `sources/base.py` (`DirectSourceProvider` ABC) + `registry.py` + `search_direct_sources` (aggregates enabled providers).
- [x] `sources/knigavuhe.py`: config (enabled/base_url/max_candidates); search-card parse (selectolax) → book page `new BookPlayer(...)` JSON (re+json) → `DirectSource` with `download_urls` + `book_metadata.narrators`/`filetype`.
- [x] Settings: separate **Direct sources** page (`Settings/Sources.jinja` + api/pages settings routes) — deliberately apart from `indexers/` so Prowlarr source-enrichment (MAM) stays isolated.
- [x] Russian-aware fuzzy matching: `app/util/normalize.py` (NFKC, ё→е, lowercase, punctuation/whitespace collapsing) wired into `download_ranking.py`.
- Confirmed (T0.3): `url` = `https://s{N}.knigavuhe.org/1/audio/{book_id}/{md5_32}/{track}.mp3?{v}`; url_refresh ~2.5 days → never cache URLs.
- [x] knigavuhe `search()` returns per-release narrator/reader metadata for the release chooser (§11.1).

**Acceptance met:** 104 tests pass (`tests/test_knigavuhe.py`, `tests/test_russian_matching.py`); `ruff check`/`format` clean; `basedpyright` = 0 errors on `sources/`, `normalize.py`, `download_ranking.py`.


### Phase 5 — Hardening, docs, release prep (DONE)
- [x] Full regression: `uv run pytest` = 113 passed; ruff / basedpyright clean.
- [x] `README.md` rewrite (livelib + knigavuhe + aria2 + Prowlarr + release selection + ABS).
- [x] `docker-compose.yml` + `.env.local` / `.env.example` cleanup (aria2 service, shared `/downloads` volume); `docker compose config` OK.
- [x] **T5.1** Decypharr investigation (parallel) resolved — see §6.1.
- Deferred follow-up: implement the **Decypharr torrent client** per §6.1 (tracked as P7).

### Phase 6 — Release/narrator selection (follow-up, after Phase 4) (DONE)
- [x] Persistence: nullable JSON `selected_release` on `AudiobookRequest` (custom `_SelectedReleaseJSON` type + `SelectedRelease` model {protocol, guid, title, narrators}).
- [x] Surface candidate releases + persist the choice: `GET /request/{book_id}/releases` chooser + `POST /request/hx-select/{book_id}`.
- [x] Auto-download dispatches the **chosen** release (matched by protocol+guid) instead of `ranked[0]` (fallback = top-ranked).
- [x] API: `POST /api/requests/{book_ref}` body selection + `PATCH /api/requests/{book_ref}/release`.
- [x] Prowlarr-optional: `query_sources` skips Prowlarr when unconfigured (direct-only knigavuhe works).

**Acceptance met:** 113 tests pass (`tests/test_selected_release.py`); ruff/basedpyright clean; migration `b3a1c2d4e5f6` applies; `selected_release` column present.

### Phase 7 — Release-centric core + search simplification (P12, DONE except follow-ups)
- [x] **Search → releases**: `GET /api/search` queries Prowlarr + direct sources directly (no livelib), returns a **flat, paginated** ranked release list with a source filter (`tracker | direct | both`); the search page renders release rows with the source filter, a **Send to download** / **Add to requests** action pair per row, and an **in-library** badge (ABS title+author check, cached).
- [x] Requests backend: admin-wide bookmark listing with requester names, per-release download **state label** (`Not downloaded | Sent to download | Downloaded | Download failed`), in-library flag, and title+author grouping helpers (`app/util/release_title.py`, `group_release_views`).
- [x] Requests page UI: `/requests` groups bookmarks by title+author (expandable), shows the per-release **state label** and in-library badge, with **download**, **refresh** (re-fetch source / fresh direct URLs) and **remove** per release; admins see everyone's bookmarks with requester names. Manual requests moved to the `/requests/manual` tab.
- [x] **Retired the book-centric surfaces**: removed `/wishlist`, the per-book `Sources` table, the `/request/{book_id}/releases` chooser and `/api/recommendations`; dropped `AudiobookRequest`, `SelectedRelease`, `ReleaseSnapshot`, `Audiobook.downloaded`, `app/internal/query.py`, `downloads/dispatch.py`, the book-card home sections (home now shows **popular releases**) and the old tests. Legacy URLs redirect (`/wishlist` → `/requests`, chooser → `/search?q=<title>`). Migration `a1b2c3d4e5f6`.
- [ ] Narrator extraction from Prowlarr titles (`[Narrator]` / `(Narrator)` patterns) into `release.narrators`.
- [ ] Livelib enrichment service (post-grab): title+author → cover/clean metadata; push author/narrator to ABS.
- [ ] Search history + release request history (rows are written; no UI yet).
- [ ] Rework the now-inert `Auto Download` setting for the release flow (currently only affects the manual-request note).

### Phase 8 — Download lifecycle (P9, NEXT)
- [ ] `DownloadJob` + status poller (aria2 `tellStatus`, Decypharr `/api/torrents`) — terminal states completed/failed.
- [ ] Completion-only status: mark a release completed only when files finish; failure → failed + notification + Retry/Repair.
- [ ] Move-to-library on completion: staging `/downloads` → library `/audiobooks`; ABS watches `/audiobooks` only.
- [ ] Retry/backoff for transient failures; knigavuhe URL refresh at download time.
- [ ] ABS metadata push (author/narrator) after completion.

**Quick wins (deferred):** button icon/tooltip (#6).

---

## 6. Decypharr investigation (T0.1) — RESOLVED

Full findings: `spikes/T0.1-decypharr-download-client.md`.

**Bottom line:** Decypharr is the wrong tool for direct HTTP (knigavuhe mp3) — it only accepts magnet/`.torrent`/NZB and forwards them to debrid providers; knigavuhe is not a debrid hoster. **aria2 (JSON-RPC)** is the chosen client for `direct` sources.

**Decypharr↔Prowlarr:** no first-party bug; ABR's `POST /api/v1/search {guid, indexerId}` is Prowlarr's standard grab endpoint. The practical failure is Prowlarr-side config: Decypharr's qBittorrent mock requires a non-empty `category` that resolves to an Arr (`arr is required` → 502 otherwise), valid credentials, a configured debrid provider, and a materializing `download_action`. If Decypharr is desired for tracker sources, fix it in Prowlarr (category + credentials), not in ABR.

**aria2 surface to model:** `host`, `port` (6800), `rpc_secret` (`token:<TOKEN>`), `dir`, `out`; per-download `header`/`referer`/`user-agent`/`continue`. Endpoint `POST /jsonrpc`, methods `system.listMethods`, `aria2.addUri`, `aria2.tellStatus` (see the spike doc for exact JSON).

**Dispatch design (confirmed):** `DownloadClient` ABC (`Aria2Client` + optional `ProwlarrClient`); route by **protocol**: `torrent`/`usenet` → Prowlarr, `direct` → aria2.

### 6.1 Decypharr for torrent sources (investigation, added 2026-11-03)

For **torrent**-based results, investigate adding **Decypharr** as the download client — either directly in ABR or inside Prowlarr. Mind Prowlarr's documentation (Settings > Download Clients):

> If you intend to do searches directly within Prowlarr, you need to add Download Clients. Otherwise, you do not need to add them here. For searches from your Apps, the download clients configured there are used instead.
> Download clients are for Prowlarr in-app searches only and do not sync to apps. There are no plans to add any such functionality.

**Implication to verify:** ABR performs *app searches* (`GET /api/v1/search` then `POST /api/v1/search {guid, indexerId}`), so a Prowlarr-side Decypharr client may **not** be used for ABR's grabs. The likely-correct path is a direct client:

- **Path A — direct `DecypharrClient` (preferred)**: add a qBittorrent-compatible `DownloadClient` impl under `app/internal/downloads/` that POSTs the `.torrent`/magnet to Decypharr (`/api/v2/auth/login`, `/torrents/add`), reusing the T0.1 findings (non-empty `category` resolving to an Arr, credentials, debrid provider, materializing `download_action`). Route `torrent` sources to it via a config-selectable torrent client (alongside the aria2 direct client).
- **Path B — Prowlarr-side**: add Decypharr as a Prowlarr download client and empirically confirm whether ABR's grab dispatches to it (likely insufficient per the note above).
- Deliverables: config fields, whether magnet vs `.torrent` is required/available, and a recommendation (default to Path A unless Path B is proven to work).

**Resolution (T5.1, done 2026-11-03):** the "may not be used" doubt is resolved — a Prowlarr-side download client **is** used for ABR's grabs, so **Path B works with only Prowlarr/Decypharr config changes** (zero ABR code). **Path A remains the preferred target** when ABR must guarantee files land in the ABS-watched folder and observe completion (explicit download action + state API). Magnet-first with `.torrent` fallback. Note the existing `DownloadClient` ABC is aria2-shaped; a future `DecypharrClient` must reinterpret `DownloadFile.urls` (one magnet URI or one `.torrent` URL) and ignore `dir`/`out` (Decypharr sets the target folder). Full details: `spikes/T5.1-decypharr-torrent.md`.

**Decision (2026-11-03):** go **Path B** now (config-only, zero ABR code); **Path A** (`DecypharrClient`, tracked as P7) is **deferred**.

---

## 7. Test strategy / acceptance

- Unit/parsing tests use **recorded fixtures** (`tests/fixtures/livelib/`, `tests/fixtures/knigavuhe/`) — never live scraping in CI.
- External services mocked with the existing `tests/mock_services.py` aiohttp double pattern (extend for aria2/direct client).
- Each phase is "done" only when its acceptance tests pass **and** are recorded here.

Test results table (fill as phases complete):

| Phase | Test area | Command / note | Result | Date |
|---|---|---|---|---|
| 0 | fixture capture scripts | — | `[x]` | 2026-11-03 |
| 1 | schema/migration + full suite | `uv run pytest` = 74 passed · ruff clean · migration OK | `[x]` | 2026-11-03 |
| 2 | livelib parsers + endpoints | `uv run pytest` = 85 passed · ruff clean · basedpyright clean | `[x]` | 2026-11-03 |
| 3 | direct client + aria2 mock | `uv run pytest` = 90 passed · ruff clean · basedpyright clean | `[x]` | 2026-11-03 |
| 4 | knigavuhe scraper + ranking | `uv run pytest` = 104 passed · ruff clean · basedpyright clean | `[x]` | 2026-11-03 |
| 5 | full regression | `uv run pytest` = 113 passed | `[x]` | 2026-11-03 |
| 6 | release/narrator selection | `uv run pytest` = 113 passed · ruff clean · basedpyright clean | `[x]` | 2026-11-03 |
| 7 | P8 release persistence + re-request + P10 title split (+ sample-only, unmark, ABS cross-ref fixes) | `uv run pytest` = 128 passed · ruff/basedpyright clean · migration `c4d5e6f7a8b9` | `[x]` | 2026-11-04 |
| 8 | P12 release-centric core + search simplification | steps 1–4 done: `uv run pytest` = 120 passed · ruff/basedpyright clean · migration `a1b2c3d4e5f6` | `[x]` | 2026-11-04 |
| 9 | P9 download lifecycle | — | `[ ]` | — |

---

## 8. Deployment / local test stack

- **No compose file in this repo.** AudioBookRequestRu runs as one service in the owner's external arr stack, next to
  Prowlarr, the torrent client (via Prowlarr), an aria2 JSON-RPC container for direct sources, and Audiobookshelf.
- The removed `docker-compose.yml` (init/web/psql/prowlarr/qbittorrent/decypharr/aria2) and its
  `scripts/setup_local_stack.py` helper were deleted in the P12 cleanup; `psql` in particular had no `profiles:` gate,
  so a plain `docker compose up` could start an unwanted Postgres.
- Local development: `just dev` (or `uv run alembic upgrade heads && uv run fastapi dev`) with `.env.local`; the
  knigavuhe direct-source path needs no other service running.
- Live knigavuhe/livelib integration checks are run manually from a configured instance; external sites are never
  mocked in CI.

---

## 9. Delegation & task tracker

| # | Task | Phase | Agent/session | Status | Result link |
|---|---|---|---|---|---|
| T0.1 | Decypharr / download-client investigation | 0 | delegated (requestId `fa4344e2-f624-4b29-adae-e9e1560dca78`) | `[x]` | `spikes/T0.1-decypharr-download-client.md` |
| T0.2 | Livelib scraping spike | 0 | delegated (requestId `49802024-9da0-4a79-8ddf-29e87fed3983`) | `[x]` | `spikes/T0.2-livelib.md` |
| T0.3 | Knigavuhe scraping spike | 0 | delegated (requestId `11d2e57a-eb46-4234-a197-4d9aa6687447`) | `[x]` | `spikes/T0.3-knigavuhe.md` |
| P1 | Phase 1 — schema decoupling, Audible/Chaptarr removal, migration | 1 | delegated (requestId `b98f5e8f-7ec0-43c0-ad63-f6632e34d5f1`) | `[x]` | verified: 74 tests / ruff / migration OK; migration `bb0ee00ba98e` |
| P2 | Phase 2 — livelib metadata provider | 2 | delegated (requestId `64ebde8d-d807-4e1f-86f6-4837488d0102`) | `[x]` | verified: 85 tests / ruff / basedpyright OK; new `app/internal/metadata/` |
| P3 | Phase 3 — direct source model + aria2 client | 3 | delegated (requestId `1a469043-5dc6-445b-9734-c5251ee5d6f7`) | `[x]` | verified: 90 tests / ruff / basedpyright / compose OK; new `app/internal/downloads/` |
| P4a | Phase 4a — knigavuhe direct-source provider | 4 | delegated (requestId `0e2df2d5-1d91-4eb9-b160-a5d3c58fff3e`) | `[x]` | verified: 104 tests / ruff / basedpyright OK; new `app/internal/sources/` |
| P4b | Phase 4b — Russian-aware fuzzy matching | 4 | delegated (requestId `e16d7109-0293-46fa-8c27-2f194e9b185d`) | `[x]` | verified: `app/util/normalize.py` + `test_russian_matching.py`, checks green |
| P6 | Phase 6 — release/narrator selection (follow-up) | 6 | delegated (requestId `03c46cd5-9cd7-4e69-8028-bb07fd7aad2e`) | `[x]` | verified: 113 tests / ruff / basedpyright / migration OK; `SelectedRelease` + chooser |
| P5 | Phase 5 — hardening & docs | 5 | delegated (requestId `3174177b-2444-49ec-94b8-d0ac56705ad2`) | `[x]` | verified: 113 tests / ruff / compose OK; README + `.env.example` + compose finalized |
| T5.1 | Decypharr torrent download-client investigation | 5 | delegated (requestId `47d1fe94-3814-48f6-a4e2-a4596623350c`) | `[x]` | `spikes/T5.1-decypharr-torrent.md` (Path B interim, Path A preferred) |
| P7 | Decypharr torrent download client (Path A) — deferred | 5 | — | `[ ]` | deferred: Path B chosen (2026-11-03) |
| P8 | Persist release search results; re-request another release | 7 | in-session | `[x]` | verified: 128 tests; `ReleaseSnapshot` + chooser refresh + re-request flow |
| P9 | Download lifecycle: completion/failure tracking, move-to-library, retry | 8 | — | `[ ]` | moved after P12; scope in §5 Phase 8 |
| P10 | Split multi-novel livelib titles into separate searches | follow-up | in-session | `[x]` | verified: `split_combined_title` + chooser/sources suggestion |
| P11 | Sync `downloaded` with ABS (unmark books ABS doesn't have) | follow-up | — | `[x]` | superseded: per-release in-library check via ABS title+author |
| P12 | Release-centric core + search simplification (livelib → metadata only) | 7 | in-session | `[x]` | steps 1–4: release search UI, `/requests` grouped page, book-centric surfaces removed — see §5 Phase 7 |
| —  | (future phases, as needed) | —   | — | — | — |

---

## 10. Open questions / risks

- [x] **Chaptarr removal** confirmed — remove completely in Phase 1.
- [x] **Knigavuhe audio URLs**: inline `<script>` `BookPlayer` JSON (not XHR). Caveat: `url_refresh` ~2.5-day window — fetch fresh at download time and don't persist URLs.
- [x] **Livelib scraping**: works. Search (PHP) over HTTP/1.1; detail pages (Next.js) require **HTTP/2** → use `httpx[http2]` (or `curl --http2`). JSON-LD is the robust metadata source.
- [x] **Decypharr applicability**: tracker/debrid only — not for direct HTTP; aria2 chosen.
- [~] **Residual: markup drift** on both sites — mitigated by fixtures + isolated parsers + polite rate limiting/caching.

---

## 11. Design notes — request workflow & Audiobookshelf (added 2026-11-03)

> **Note (2026-11-04):** §11.1–11.2 describe the *book-centric* interim design (single
> `downloaded` flag + `selected_release` on `AudiobookRequest`). They are superseded by the
> release-centric target model in §4 and the Phase 7/8 plan; kept here as history.

### 11.1 Release / narrator selection before download

One livelib **work** maps to many audio **releases** that differ by narrator (исполнитель) and quality (different knigavuhe book pages, different tracker uploads). The requester must be able to choose which release + narrator they want **before** anything is sent to a download client.

- **Work vs release**: `Audiobook` = the livelib work (the requested entity). A "release" = a concrete download source (`TorrentSource | UsenetSource | DirectSource`) enriched with `book_metadata.narrators` + quality. The existing `rank_sources` result **is** the candidate-release list.
- **Surfacing**: the current admin-only `/wishlist/sources/{book_id}` table (narrator / indexer / flags / size / protocol columns) becomes the model for a **release chooser shown at request time** (search → work → request → fetch candidate releases → pick one). An untrusted user's chosen release is recorded and shown to the admin; a trusted user's chosen release is downloaded automatically.
- **Persistence**: store the chosen release on the request (nullable selection referencing `(protocol, guid, indexer_id)` for tracker sources, or `(protocol, source_id, download_urls)` for direct sources). Either a child table (`DownloadSelection`) or nullable columns on `AudiobookRequest` — decide in Phase 6.
- **Dispatch**: auto-download downloads the **chosen** release, not `ranked[0]`. If the user skips selection, default to the top-ranked release (keep the current one-click UX) with the option to override.
- **Ranking unchanged**: `rank_sources` stays the suggestion/ranking engine; selection is a thin persistence + dispatch layer.

### 11.2 Audiobookshelf integration — keep existing semantics

The downloaded audiobook must land in Audiobookshelf exactly as it does today:

- **Duplicate detection**: during search, `abs_mark_downloaded_flags` marks books that already exist in ABS as `downloaded` (now keyed on title + author since ASIN is gone). Preserve this behavior.
- **After download**: on download start/success, mark the request `downloaded=True`, emit `on_successful_download`, and trigger a library scan via `abs_trigger_scan` / `background_abs_trigger_scan`.
- **Direct path parity**: a successful aria2 grab must run the same mark-downloaded + scan callbacks as the Prowlarr path. aria2's `dir` must point into the volume ABS watches (shared `/downloads` → ABS library) so the scan picks up the new files.
- **No new ABS features** — reuse the existing `audiobookshelf/client.py` (`abs_mark_downloaded_flags`, `abs_trigger_scan`) and notification tokens.
