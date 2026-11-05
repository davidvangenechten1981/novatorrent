"""Scale smoke test: many torrents, polling endpoints must stay responsive (no network used)."""

import os
import time

import pytest

N = int(os.environ.get("NOVA_PERF_N", "500"))
# Generous bound for slow CI machines; the real numbers are printed with `pytest -s`.
MAX_SECONDS = float(os.environ.get("NOVA_PERF_MAX", "3.0"))


@pytest.fixture
def many(cfg, monkeypatch):
    from fastapi.testclient import TestClient

    from conftest import PASSWORD
    from novatorrent.app import create_app

    monkeypatch.setenv("NOVA_RATE_LIMIT", "100000")
    with TestClient(create_app(cfg)) as client:
        client.post("/api/v2/auth/login", data={"username": "admin", "password": PASSWORD})
        yield from _fill(client)


def _fill(client):
    client.post("/api/v2/torrents/createCategory", data={"category": "radarr", "savePath": ""})
    for i in range(1, N + 1):
        h = f"{i:040x}"
        r = client.post(
            "/api/v2/torrents/add",
            data={"urls": f"magnet:?xt=urn:btih:{h}&dn=t{i}", "paused": "true", "category": "radarr"},
        )
        assert r.text == "Ok."
    yield client


def _timed(fn):
    t0 = time.perf_counter()
    out = fn()
    return time.perf_counter() - t0, out


def test_info_and_maindata_scale(many):
    # cold call (status cache empty), then warm call
    cold, r = _timed(lambda: many.get("/api/v2/torrents/info"))
    assert len(r.json()) == N
    warm, _ = _timed(lambda: many.get("/api/v2/torrents/info", params={"category": "radarr"}))
    full, m = _timed(lambda: many.get("/api/v2/sync/maindata", params={"rid": 0}))
    assert len(m.json()["torrents"]) == N
    diff, _ = _timed(lambda: many.get("/api/v2/sync/maindata", params={"rid": m.json()["rid"]}))
    print(f"\nN={N} info cold={cold:.3f}s warm={warm:.3f}s maindata full={full:.3f}s diff={diff:.3f}s")
    for name, value in (("cold", cold), ("warm", warm), ("full", full), ("diff", diff)):
        assert value < MAX_SECONDS, f"{name} took {value:.2f}s for {N} torrents"
