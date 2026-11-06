"""Opt-in NOVA_AUTH_DISABLED mode and its guard rails."""

import pytest
from fastapi.testclient import TestClient

from novatorrent.app import create_app


@pytest.fixture
def open_client(cfg, monkeypatch):
    monkeypatch.setenv("NOVA_AUTH_DISABLED", "1")
    with TestClient(create_app(cfg)) as c:
        yield c


def test_login_still_required_by_default(client):
    c = TestClient(client.app)  # fresh client without the login cookie
    assert c.get("/api/v2/app/version").status_code == 403
    assert c.get("/api/nova/session").status_code == 401


def test_no_login_needed_when_disabled(open_client):
    c = open_client
    assert c.get("/api/v2/app/version").status_code == 200
    s = c.get("/api/nova/session").json()
    assert s["user"] == "anonymous" and s["role"] == "admin"
    assert c.post("/api/v2/auth/login", data={"username": "x", "password": "y"}).text == "Ok."
    assert c.post("/api/v2/torrents/createCategory", data={"category": "radarr", "savePath": ""}).status_code == 200
    assert c.post("/api/nova/ban", json={"ip": "203.0.113.9"}).status_code == 200


def test_reverse_proxy_requests_still_need_login(open_client):
    for h in ("X-Forwarded-For", "X-Real-IP", "Forwarded", "X-Forwarded-Host"):
        assert open_client.get("/api/v2/app/version", headers={h: "198.51.100.7"}).status_code == 403, h
        assert open_client.get("/api/nova/session", headers={h: "198.51.100.7"}).status_code == 401, h


def test_browser_cross_site_post_is_rejected(open_client):
    r = open_client.post("/api/nova/ban", json={"ip": "203.0.113.9"}, headers={"origin": "http://evil.example"})
    assert r.status_code == 403
    r = open_client.post(
        "/api/v2/torrents/createCategory",
        data={"category": "x", "savePath": ""},
        headers={"origin": "http://evil.example"},
    )
    assert r.status_code == 401


def test_refuses_to_combine_with_allow_wan(cfg, monkeypatch):
    monkeypatch.setenv("NOVA_AUTH_DISABLED", "1")
    monkeypatch.setenv("NOVA_ALLOW_WAN", "1")
    with pytest.raises(RuntimeError):
        create_app(cfg)


@pytest.mark.parametrize(("disabled", "expected"), [("1", 0), ("0", 1)])
def test_completion_hook_is_off_without_login(cfg, monkeypatch, disabled, expected):
    monkeypatch.setenv("NOVA_AUTH_DISABLED", disabled)
    with TestClient(create_app(cfg)) as c:
        n = c.app.state.notifier
        calls = []
        monkeypatch.setattr(n, "_hook", lambda s, info: calls.append(1))
        c.app.state.settings.update({"nova_hook_enabled": True, "nova_hook_command": "echo hi"})
        n.dispatch("completed", {"hash": "a" * 40, "name": "x"})
        import time

        time.sleep(0.3)
        assert len(calls) == expected
