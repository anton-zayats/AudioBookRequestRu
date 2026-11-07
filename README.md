![Header](/media/AudioBookRequestIcon.png)

# AudioBookRequestRu

A fork of [AudioBookRequest](https://github.com/markbeep/AudioBookRequest) adapted for **Russian-language
audiobook requests**. Audible metadata is replaced by livelib.ru and the Chaptarr backend is removed.

Search Russian books on **livelib.ru**, pick the release and narrator you want, and download straight from
**knigavuhe.org** (via an **aria2** download client) or from your tracker indexers (via **Prowlarr**).

If you've heard of Overseerr, Ombi, or Jellyseerr; this is in a similar vein, <ins>but for audiobooks</ins> — in Russian.

## Table of Contents

- [What it does](#what-it-does)
  - [Features](#features)
  - [Out of Scope](#out-of-scope)
- [Getting Started](#getting-started)
  - [Quick Start](#quick-start)
  - [Basic Usage](#basic-usage)
    - [Metadata (livelib.ru)](#metadata-livelibru)
    - [Tracker downloads (Prowlarr)](#tracker-downloads-prowlarr)
    - [Direct downloads (knigavuhe.org + aria2)](#direct-downloads-knigavuheorg--aria2)
    - [Release & narrator selection](#release--narrator-selection)
    - [Audiobookshelf Integration](#audiobookshelf-integration)
    - [OpenID Connect](#openid-connect)
    - [Environment Variables](#environment-variables)
- [Contributing](#contributing)
  - [Conventional Commits](#conventional-commits)
  - [Local Development](#local-development)
    - [Local test stack](#local-test-stack)
- [Tools](#tools)

# What it does

AudioBookRequestRu is a simple, lightweight tool for managing Russian audiobook requests for your media server.
It is the frontend you and your friends use to search for books, pick a narrator, and create requests in an
organized fashion. It is not a full replacement for Readarr — it is meant to be used alongside it.

## Features

- **Russian metadata from livelib.ru** — search, suggestions, similar books, and categories all come from livelib.
- **Direct downloads from knigavuhe.org** — non-tracker audio is handed off to an **aria2 JSON-RPC** download client.
- **Tracker sources via Prowlarr** — reuse your existing Prowlarr indexers and download clients for torrent/usenet results.
- **Release & narrator selection** — one book can have many audio releases; the requester picks the narrator and
  release before anything is downloaded.
- Add manual audiobook requests for any books not found on livelib.
- Easy user management. Only three assignable groups, made to get out of your way.
- Send notifications to your favorite notification service (apprise, gotify, discord, ntfy, etc.).
- Single image deployment. You can deploy and create your first requests in under 5 minutes.
- SQLite and Postgres support.
- Lightweight website. No bulky JavaScript files, allowing you to use the website even on low bandwidth.
- Mobile friendly. Search for books or accept requests on the go.

## Out of Scope

- AudioBookRequestRu does **not** handle moving, renaming, nor editing metadata after downloads. Instead it supports
  multiple REST API endpoints that allow for easy interoperability with scripts and other apps.

---

# Getting Started

AudioBookRequestRu is intended to be deployed using Docker or Kubernetes. For bare-metal deployments, follow the
[local development](#local-development) instructions below.

## Quick Start

Build the image and run it directly:

```bash
docker build -t audiobookrequest-ru .
docker run -p 8000:8000 -v "$(pwd)/config:/config" audiobookrequest-ru
```

Then head to http://localhost:8000.

**NOTE:** AudioBookRequestRu uses the `/config` directory inside the container for storing configs and data.
Mount that directory locally somewhere to ensure persistent data across restarts.

## Basic Usage

1. On first login the login-type and root admin user have to be configured.
2. Head to `Settings > Users` to create accounts for your friends.
3. Search for a book: results are **releases** (from Prowlarr trackers and direct sources such as knigavuhe), each
   with narrator, source, size and a status badge (`In library` when Audiobookshelf already has it).
4. Per release, either **Download** immediately or **Add to requests** to download later.
5. The `Requests` page groups bookmarked releases by title and author; each release can be downloaded, refreshed
   (re-fetched from its source) or removed there. Admins see everyone's requests.
6. Books that cannot be found can be added as a **manual request** (free text); an admin marks those downloaded.

### Metadata (livelib.ru)

**livelib.ru** is used for **suggestions and metadata enrichment** only; it is no longer the search entry point.
Releases are found by searching Prowlarr and the enabled direct sources directly. There is no metadata source to
configure — livelib is the default (and currently only) provider.

### Tracker downloads (Prowlarr)

`Download` on a tracker release is an in-app Prowlarr grab, using your existing Prowlarr indexers and configured
download clients for torrent/usenet results.

1. Ensure your Prowlarr instance is correctly set up with any indexers and download clients you want.
   [More info](https://prowlarr.com/).
2. On Prowlarr, head to `Settings > General` and copy the `API Key`.
3. On AudioBookRequestRu, head to `Settings > Prowlarr` and enter the API key and the base URL of your Prowlarr
   instance, e.g. `https://prowlarr.example.com`.
4. Head to `Settings > Download` to configure the download heuristics:
   1. The ranking sliders determine the order of releases retrieved from Prowlarr.
   2. Indexer flags allow you to add priorities to certain sources like freeleeches.

#### Torrent downloads via Decypharr (optional)

To route tracker (torrent) results through a debrid provider, point Prowlarr at
[Decypharr](https://github.com/sirrobot01/decypharr) (a qBittorrent-compatible debrid gateway). No ABR code changes
are needed: ABR's grab is an in-app Prowlarr grab, so Prowlarr uses its configured download client.

1. **Decypharr**: configure a debrid provider; set the default download action to **`download`** (not `symlink`/`none`)
   so files are written to disk; set the download folder to a path shared with Audiobookshelf; create an Arr/category
   (e.g. `audiobooks`); note the API token or a user/password.
2. **Prowlarr → Settings → Download Clients → Add → qBittorrent**:
   - Host `http://<decypharr>:8282`, port `8282`.
   - Username/password matching Decypharr's auth (or the Decypharr API token).
   - **Category = `audiobooks`** (non-empty and matching the Decypharr Arr name — a blank category fails with `arr is required`).
3. Point **Audiobookshelf's** library folder at Decypharr's download folder (files land under `<download_folder>/<category>`).

Note: ABR marks a request downloaded when Prowlarr accepts the grab ("submitted to debrid"), not when the files finish;
ABS scans are idempotent so this is safe, though the scan may run slightly early.

### Direct downloads (knigavuhe.org + aria2)

Books that knigavuhe.org hosts directly are downloaded by handing the audio URLs to an **aria2 JSON-RPC** server —
no tracker or torrent client is involved.

1. Run an aria2 instance with RPC enabled and an RPC secret. This repository ships **no** compose file: aria2 (like
   Prowlarr and Audiobookshelf) belongs to your own deployment stack, e.g. `p3terx/aria2-pro` with `RPC_SECRET` set
   and a download directory volume shared with Audiobookshelf.
2. In AudioBookRequestRu, head to `Settings > Download` and fill in the download client settings:
   - **Host** — the aria2 host (e.g. `aria2` in the compose stack, or `localhost`).
   - **Port** — the aria2 RPC port (default `6800`).
   - **RPC secret** — the same secret aria2 was started with.
   - **Base download directory** — the directory aria2 writes into; point this at the volume Audiobookshelf also watches.
3. Head to `Settings > Direct sources` and make sure **knigavuhe** is enabled (base URL defaults to
   `https://knigavuhe.org`, and `Max candidates` bounds how many candidate releases are scraped per book).

### Release & narrator selection

One livelib work often maps to several audio releases that differ by narrator (исполнитель) and quality. When
requesting a book, the candidate releases are fetched and shown so the requester can pick the narrator they want
**before** anything is sent to a download client.

- A `Trusted`/`Admin` request with a chosen release starts downloading automatically once the request is made.
- If the requester skips the selection, the top-ranked release is used, and the admin can still override it later.

### Audiobookshelf Integration

Audiobookshelf (ABS) integration lets ABR:

- Check if a book already exists in your ABS library and mark it as downloaded in search results to avoid duplicate requests.
- Trigger a library scan in ABS when a request is marked as downloaded in ABR (manual or automatic), so the new item appears quickly.

Setup steps:

1. In ABS, create an API token for an account with access to your audiobook library (Admin recommended).
2. In ABR, go to `Settings > Audiobookshelf` and enter:
   - Base URL of your ABS server (e.g. `https://abs.example.com` or `http://localhost:13378`).
   - API Token from step 1.
   - Select the target Library.
   - Enable "Use ABS to mark existing books as downloaded" if you want ABR to flag existing titles during search.

Notes:

- ABR detects existing books by matching on "title + first author"; this is a best-effort match and may not catch
  every case depending on your metadata.
- ABS is automatically asked to scan after successful downloads are marked in ABR. ABS typically auto-detects updates,
  but this helps pick up changes sooner.
- Direct (knigavuhe/aria2) downloads must land inside the volume ABS watches so the scan picks up the new files.

### OpenID Connect

OpenID Connect authentication is supported. Configure the login type under `Settings > Security` (`Login Type >
OpenID Connect`) and fill in your provider's endpoint, client id/secret, scopes, and claims. Head to the
[upstream OpenID Connect wiki page](https://github.com/markbeep/AudioBookRequest/wiki/OpenID-Connect) for a walkthrough.
If you get locked out, log in at `/login?backup=1`.

### Environment Variables

Application configuration is read from environment variables with the `ABR_` prefix (nested settings use `__`, e.g.
`ABR_APP__CONFIG_DIR`). See [`.env.example`](.env.example) for the full list.

Note that **Prowlarr, the download client (aria2), direct sources (knigavuhe), and Audiobookshelf are _not_ configured
through environment variables** — they are edited in the Settings UI and stored in ABR's config database.

---

# Contributing

Please read the [contribution guidelines](?tab=contributing-ov-file) before contributing.

## Conventional Commits

This project uses [Conventional Commits](https://www.conventionalcommits.org) to allow for a more organized commit
history and support automated changelog generation. Pull requests will be squashed in most cases (with some exceptions).

## Local Development

```bash
uv sync --dev          # install dependencies
uv run pytest tests     # run the test suite
uv run ruff check app tests
uv run basedpyright app
```

Type checks run with `basedpyright` (`just types`), tests with `pytest` (`just test`), and the Jinja templates are
validated with `djlint` (`just test_format`).

### Running alongside your own stack

The repository deliberately contains **no** `docker-compose.yml`: AudioBookRequestRu is one service inside the
deployment stack that also runs Prowlarr, the torrent client, aria2 and Audiobookshelf. Run the app image with your
`config` volume mounted (`/config`) and point the Settings UI at your existing services:

- `Settings > Prowlarr` — base URL and API key (internal URL, e.g. `http://prowlarr:9696`).
- `Settings > Download` — aria2 host/port/RPC secret and the base download directory (shared with Audiobookshelf).
- `Settings > Audiobookshelf` — base URL, API token, library.
- `Settings > Direct sources` — knigavuhe enabled/disabled.

For local development without any of that, run the app directly (`just dev` / `uv run fastapi dev`) and configure the
services you have; the direct-source search works with no other service running.

# Tools

AudioBookRequestRu builds on top of some other great open-source tools and services. A big thanks goes out to these
developers.

- [livelib.ru](https://www.livelib.ru/) — Russian book catalog used for search, suggestions, similar books, and categories.
- [knigavuhe.org](https://knigavuhe.org/) — audiobook site scraped for direct audio download sources.
- [aria2](https://aria2.github.io/) — high-speed download utility used as the JSON-RPC download client for direct sources.
- [Prowlarr](https://github.com/Prowlarr/Prowlarr) — does a lot of the heavy lifting concerning searching through
  indexers and forwarding download requests to download clients.
- [Audiobookshelf](https://www.audiobookshelf.org/) — self-hosted audiobook server ABR integrates with for duplicate
  detection and library scans.