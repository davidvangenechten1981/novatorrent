"""Replays the calls Radarr/Sonarr/Lidarr make against a qBittorrent-compatible API.

Usage: NOVA_URL=http://host:8080 NOVA_USER=admin NOVA_PASS=... uv run python scripts/arr_client_check.py
The password is read from the environment only (never printed).
"""

from __future__ import annotations

import os
import sys

import httpx


def run(c: httpx.Client, base: str = "") -> list[str]:
    """Return a list of failure messages (empty = all good)."""
    errors: list[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            errors.append(msg)

    v = c.get(f"{base}/api/v2/app/webapiVersion")
    check(v.status_code == 200 and v.text.count(".") == 2, "webapiVersion")
    check(c.get(f"{base}/api/v2/app/version").text.startswith("v"), "app/version")
    prefs = c.get(f"{base}/api/v2/app/preferences").json()
    check("save_path" in prefs and "queueing_enabled" in prefs, "app/preferences fields")
    c.post(f"{base}/api/v2/torrents/createCategory", data={"category": "arrcheck"})
    check("arrcheck" in c.get(f"{base}/api/v2/torrents/categories").json(), "categories")
    magnet = "magnet:?xt=urn:btih:" + "c" * 40 + "&dn=arr-check"
    r = c.post(f"{base}/api/v2/torrents/add", data={"urls": magnet, "category": "arrcheck", "paused": "true"})
    check(r.text == "Ok.", "torrents/add")
    items = c.get(f"{base}/api/v2/torrents/info", params={"category": "arrcheck"}).json()
    check(len(items) == 1 and items[0]["hash"] == "c" * 40, "torrents/info by category")
    c.post(f"{base}/api/v2/torrents/addTags", data={"hashes": "c" * 40, "tags": "imported"})
    c.post(f"{base}/api/v2/torrents/setCategory", data={"hashes": "c" * 40, "category": "arrcheck"})
    check(c.get(f"{base}/api/v2/torrents/properties", params={"hash": "c" * 40}).status_code == 200, "properties")
    check(c.get(f"{base}/api/v2/torrents/files", params={"hash": "c" * 40}).status_code == 200, "files")
    c.post(f"{base}/api/v2/torrents/delete", data={"hashes": "c" * 40, "deleteFiles": "true"})
    check(c.get(f"{base}/api/v2/torrents/info", params={"hashes": "c" * 40}).json() == [], "delete")
    c.post(f"{base}/api/v2/torrents/removeCategories", data={"categories": "arrcheck"})
    return errors


def main() -> int:
    base = os.environ.get("NOVA_URL", "http://localhost:8080")
    with httpx.Client(base_url=base, timeout=15) as c:
        r = c.post(
            "/api/v2/auth/login",
            data={"username": os.environ.get("NOVA_USER", "admin"), "password": os.environ.get("NOVA_PASS", "")},
        )
        if r.text != "Ok.":
            print("login failed")
            return 2
        errs = run(c)
    print("OK" if not errs else "FAILED: " + ", ".join(errs))
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
