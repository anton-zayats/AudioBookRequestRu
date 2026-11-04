# AudioBookRequestRu — Implementation Plan

Living plan for forking **AudioBookRequest** (upstream `markbeep/AudioBookRequest`, currently v1.11.0) into a
Russian-language audiobook request manager.

> Status legend: `[ ]` todo · `[~]` in progress · `[x]` done

---

## 1. Goal

Adapt AudioBookRequest (ABR) so it can serve **Russian-language audiobook requests**:

1. Use **livelib.ru** as the metadata source for Russian books (replacing Audible).
2. When a book is identified on livelib, search its audio versions in existing Prowlarr indexers **and** new (additional) indexers.
3. Add a specific indexer + **download client** for **knigavuhe.org** (not a tracker — requires scraping the page to find audio files).
4. Add the ability to add **new specific, non-tracker download sources** over time.

---

## 2. Locked decisions

Decided with the owner (2026-11-03):

1. **Identifier model**: generalize `Audiobook` to `(source, external_id)` with a surrogate primary key, and **remove Audible entirely**.
   - Only `source = "livelib"` ships initially; the `source` column is kept for future providers.
2. **Direct downloads**: delegate direct (non-tracker) grabs to a **configurable external download client**.
   - **Confirmed (T0.1): aria2 JSON-RPC** is the direct-HTTP client. Decypharr is orthogonal (see §6).
3. **Canonical livelib identifier**: the **work**, not editions. Search cards' `data-book_id` is the work id (confirmed 20/20 cards). Canonical pair = `(work_id, edition data-link)`; full metadata/similar-books come from the edition page's JSON-LD.
4. **Direct-source framework**: **code-level plugin interface** (Python classes registered in a list), knigavuhe as the first implementation.

### Secondary decisions / notes

5. **Decypharr**: the current ABR→Prowlarr→download-client flow needs a separate investigation for Decypharr compatibility. Tracked in §6 / Task T0.1.
6. **Local test stack**: a docker compose profile for local testing is required (§8).
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

## 4. Target architecture

```
app/internal/
  metadata/                 # NEW — replaces app/internal/audible/
    base.py                 # MetadataProvider ABC
    livelib.py              # provider (search / single / suggestions / similar / categories)
    registry.py             # get_provider() from config `metadata_source`
    # (knigavuhe.py can become a fallback metadata provider if livelib is too fragile)
  sources/                  # NEW — direct (non-tracker) download-source providers
    base.py                 # DirectSourceProvider ABC: search(book) -> list[DirectSource]
    knigavuhe.py            # first provider
    registry.py
  downloads/
    client.py               # DownloadClient ABC
    aria2.py                # aria2 JSON-RPC (or chosen client)
    config.py               # host/port/secret/download dir config
  download_backend.py       # reduced after Chaptarr removal
  models.py                 # Audiobook: id (PK) + (source, external_id) unique; DirectSource
  query.py                  # Prowlarr + direct providers, dispatch by protocol
```

**Data model:**

- `Audiobook`: `id` surrogate PK; `source` = `"livelib"`; `external_id` = livelib **work id**; unique `(source, external_id)`; `url` = livelib work link; metadata fields unchanged.
- `AudiobookRequest`: `book_id` FK replaces `asin`.
- Source union: `TorrentSource | UsenetSource | DirectSource(protocol="direct", download_urls, filetype, ...)`.

**Download dispatch:**

- `torrent` / `usenet` → Prowlarr (existing `start_download`).
- `direct` → external download client (aria2) with `download_urls` and target directory.

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

---

## 8. Local docker test stack

- `docker compose --profile local up` starts ABR + PostgreSQL + Prowlarr + qBittorrent + aria2 (and an optional `decypharr` service).
- aria2 (direct/knigavuhe) added in Phase 3: RPC 6800, `./tmp/downloads`. Decypharr is optional and only relevant as Prowlarr's download client for tracker/debrid sources (**Path B**, see §6.1); add/configure the `decypharr` service when testing it.
- Live knigavuhe/livelib integration tests run from the docker stack only when explicitly requested (external sites are not mocked).

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
