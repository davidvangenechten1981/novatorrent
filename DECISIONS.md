# DECISIONS

## D1 — Torrent engine: libtorrent-rasterbar (Python bindings)
Compared for a long-running headless server with hundreds of torrents and heavy seeding:
- **libtorrent-rasterbar**: the engine behind qBittorrent/Deluge. Mature piece/disk cache, resume
  data, queueing, IP filter, proxy, interface binding, encryption, anonymous mode, uTP, DHT/PEX/LSD,
  v1+v2, sequential download, per-file priorities — nearly every requirement is a native setting.
  Official manylinux wheels for amd64 + arm64 exist on PyPI (`libtorrent` 2.1.x).
- **anacrolix/torrent (Go)**: good library, but no queueing, no IP filter file, weak seeding/disk
  tuning at scale, no anonymous mode; much would have to be built by hand. Go is not installed here.
- **WebTorrent**: aimed at browsers (WebRTC), poor TCP/uTP swarm performance and memory use for
  hundreds of torrents. Rejected.
**Choice: libtorrent.**

## D2 — Stack: Python 3.12 + FastAPI/uvicorn, vanilla JS web-UI (no build step)
- One language for engine glue and API; libtorrent has first-class Python bindings.
- FastAPI + uvicorn: small, async; blocking libtorrent calls run in a threadpool.
- Web-UI: plain HTML/CSS/ES modules served statically. No Node build chain -> smaller image,
  fewer supply-chain risks, easier maintenance.
- qBittorrent API is one dispatcher route (`/api/v2/{section}/{name}`) with a handler table.
- Single multi-arch image (amd64 + arm64) from `python:3.12-slim`, libtorrent from the PyPI wheel.

## D3 — Packages
Runtime: libtorrent, fastapi, uvicorn, python-multipart, httpx, argon2-cffi, cryptography.
Dev: pytest, ruff. All needed by the project; installed via `uv sync` (Python 3.12 venv).

## D4 — Settings use qBittorrent preference names where one exists
`/app/preferences` and `/app/setPreferences` map 1:1 to our store; extras are prefixed `nova_`.

## D5 — State lives in /config
`settings.json`, `state.json` (categories, tags, per-torrent metadata), `resume/<hash>.fastresume`,
`auth.json`. All writes are atomic (tmp + replace).

## D6 — Initial admin password
If `NOVA_ADMIN_PASSWORD` is not set, a random password is generated on first start and written to
`/config/initial_password.txt` (mode 600). It is never logged.

## D7 — webapiVersion 2.9.3 (qBittorrent 4.6.x) -> states `pausedDL/pausedUP`
Radarr/Sonarr/Lidarr/Cleanuparr understand both old and new names; 4.6 is the widest baseline.
