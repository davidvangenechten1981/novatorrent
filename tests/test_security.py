import json

import pytest
from fastapi.testclient import TestClient

from conftest import PASSWORD, info_hash, make_torrent
from novatorrent.app import create_app
from novatorrent.auth import AuthStore
from novatorrent.backup import BackupError, export_config, import_config
from novatorrent.security import TokenBucket, is_lan


def test_password_is_argon2_and_not_plaintext(cfg):
    AuthStore(cfg)
    raw = (cfg / "auth.json").read_text()
    assert "$argon2" in raw and PASSWORD not in raw
    assert "api_keys" in raw


def test_initial_password_generated_when_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("NOVA_ADMIN_PASSWORD", raising=False)
    a = AuthStore(tmp_path)
    txt = (tmp_path / "initial_password.txt").read_text()
    pw = txt.split("password: ")[1].strip()
    assert a.verify("admin", pw) == "admin" and a.verify("admin", "nope") is None


def test_api_key_stored_hashed(cfg):
    a = AuthStore(cfg)
    token = a.create_api_key("k", "readonly")
    assert token not in (cfg / "auth.json").read_text()
    assert a.check_api_key(token) == "readonly" and a.check_api_key(token + "x") is None


def test_last_admin_cannot_be_deleted(cfg):
    a = AuthStore(cfg)
    assert a.delete_user("admin") is False


def test_is_lan():
    for ok in ("192.168.1.5", "10.0.0.2", "172.20.0.3", "127.0.0.1", "::1", "100.64.1.1", "::ffff:192.168.0.9"):
        assert is_lan(ok), ok
    for bad in ("8.8.8.8", "1.1.1.1", "2606:4700::1111", "garbage"):
        assert not is_lan(bad), bad


def test_rate_limit_bucket():
    b = TokenBucket(rate=0.0001, burst=3)
    assert [b.allow("x") for _ in range(5)] == [True, True, True, False, False]
    assert b.allow("other")


def test_wan_client_blocked_by_default(cfg):
    with TestClient(create_app(cfg), client=("8.8.8.8", 5000)) as c:
        assert c.get("/api/health").status_code == 403


def test_wan_allowed_with_opt_in(cfg, monkeypatch):
    monkeypatch.setenv("NOVA_ALLOW_WAN", "1")
    with TestClient(create_app(cfg), client=("8.8.8.8", 5000)) as c:
        assert c.get("/api/health").status_code == 200


def test_host_header_allowlist(cfg, monkeypatch):
    monkeypatch.setenv("NOVA_ALLOWED_HOSTS", "nas.local,192.168.1.10")
    with TestClient(create_app(cfg)) as c:
        assert c.get("/api/health", headers={"Host": "evil.example"}).status_code == 400
        assert c.get("/api/health", headers={"Host": "nas.local:8080"}).status_code == 200


def test_qb_csrf_origin_check(client):
    # cookie-authenticated POST from a foreign origin must be rejected
    r = client.post("/api/v2/torrents/pause", data={"hashes": "all"}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 401
    # same origin and clients without Origin/Referer (Radarr) are fine
    ok = client.post("/api/v2/torrents/pause", data={"hashes": "all"}, headers={"Origin": "http://testserver"})
    assert ok.status_code == 200
    assert client.post("/api/v2/torrents/pause", data={"hashes": "all"}).status_code == 200


def test_nova_api_requires_csrf_token(client):
    sess = client.get("/api/nova/session").json()
    body = {"ip": "203.0.113.5"}
    assert client.post("/api/nova/ban", json=body).status_code == 403  # cookie but no token
    bad = client.post("/api/nova/ban", json=body, headers={"X-CSRF-Token": "wrong"})
    assert bad.status_code == 403
    ok = client.post("/api/nova/ban", json=body, headers={"X-CSRF-Token": sess["csrf"]})
    assert ok.status_code == 200
    assert client.post("/api/nova/ban", json={"ip": "x"}, headers={"X-CSRF-Token": sess["csrf"]}).status_code == 400


def test_readonly_cannot_use_nova_writes(client):
    auth = client.app.state.auth
    key = auth.create_api_key("ro", "readonly")
    c = TestClient(client.app)
    h = {"Authorization": f"Bearer {key}"}
    assert c.get("/api/nova/stats", headers=h).status_code == 200
    assert c.post("/api/nova/ban", json={"ip": "1.2.3.4"}, headers=h).status_code == 403
    assert c.get("/api/nova/users", headers=h).status_code == 403


def test_no_secrets_in_logs(client, caplog):
    client.post("/api/v2/auth/login", data={"username": "admin", "password": "wrongpass-xyz"})
    assert "wrongpass-xyz" not in caplog.text
    assert PASSWORD not in "\n".join(x["message"] for x in client.app.state.engine.logs)


def test_metrics_opt_in(client, monkeypatch):
    assert client.get("/metrics").status_code == 404
    monkeypatch.setenv("NOVA_METRICS", "1")
    r = client.get("/metrics")
    assert r.status_code == 200 and "novatorrent_vpn_up 1" in r.text
    anon = TestClient(client.app)
    assert anon.get("/metrics").status_code == 403


def test_homepage_widget_and_uptime_kuma_calls(client):
    """Homepage's qBittorrent widget logs in, then reads torrents/info + transfer/info."""
    assert client.get("/api/v2/torrents/info").status_code == 200
    ti = client.get("/api/v2/transfer/info").json()
    assert {"dl_info_speed", "up_info_speed", "dl_info_data", "up_info_data"} <= set(ti)
    assert TestClient(client.app).get("/api/health").json() == {"status": "ok"}


def test_backup_roundtrip_encrypted(tmp_path, cfg):
    (cfg / "state.json").write_text(json.dumps({"categories": {"tv": {"savePath": ""}}}))
    (cfg / "resume").mkdir()
    (cfg / "resume" / ("a" * 40 + ".fastresume")).write_bytes(b"resume-bytes")
    blob = export_config(cfg, "correct horse")
    assert b"categories" not in blob and b"settings" not in blob  # encrypted
    target = tmp_path / "restore"
    target.mkdir()
    names = import_config(target, "correct horse", blob)
    assert "state.json" in names and ("resume/" + "a" * 40 + ".fastresume") in names
    assert json.loads((target / "state.json").read_text())["categories"]["tv"] is not None
    assert not (target / "auth.json").exists()
    with pytest.raises(BackupError):
        import_config(target, "wrong password", blob)
    with pytest.raises(BackupError):
        export_config(cfg, "short")
    with pytest.raises(BackupError):
        import_config(target, "correct horse", b"garbage" * 20)


def test_backup_via_api(client, tmp_path):
    data, _ = make_torrent(tmp_path / "s")
    client.post(
        "/api/v2/torrents/add", files={"torrents": ("a.torrent", data)}, data={"paused": "true", "category": "x"}
    )
    csrf = client.get("/api/nova/session").json()["csrf"]
    h = {"X-CSRF-Token": csrf}
    r = client.post("/api/nova/backup/export", json={"password": "pw-long-enough"}, headers=h)
    assert r.status_code == 200 and r.content.startswith(b"NTBAK1")
    bad = client.post(
        "/api/nova/backup/import",
        data={"password": "nope-nope-nope"},
        files={"file": ("b.ntbak", r.content)},
        headers=h,
    )
    assert bad.status_code == 400
    good = client.post(
        "/api/nova/backup/import",
        data={"password": "pw-long-enough"},
        files={"file": ("b.ntbak", r.content)},
        headers=h,
    )
    assert good.json()["restored"] >= 2
    assert (client.app.state.engine.resume_dir / f"{info_hash(data)}.fastresume").exists()


def test_env_overrides_enforce_vpn_binding(cfg, monkeypatch):
    monkeypatch.setenv("NOVA_BIND_INTERFACE", "tun-does-not-exist")
    monkeypatch.setenv("NOVA_REQUIRE_VPN", "1")
    with TestClient(create_app(cfg)) as c:
        # interface is missing -> fail closed: health is 503 and the session is paused
        assert c.get("/api/health").status_code == 503
        assert c.app.state.engine.ses.is_paused()


def test_healthcheck_works_with_allowed_hosts(cfg, monkeypatch):
    """The in-container healthcheck calls 127.0.0.1; it must not be blocked by NOVA_ALLOWED_HOSTS."""
    from fastapi.testclient import TestClient

    from novatorrent.app import create_app

    monkeypatch.setenv("NOVA_ALLOWED_HOSTS", "nas.example,192.168.0.10")
    with TestClient(create_app(cfg)) as c:
        for host in ("127.0.0.1:8080", "localhost:8080", "[::1]:8080"):
            assert c.get("/api/health", headers={"host": host}).status_code == 200, host
        assert c.get("/api/health", headers={"host": "nas.example"}).status_code == 200
        # unknown hosts stay blocked for everything else
        assert c.get("/api/v2/app/version", headers={"host": "evil.example"}).status_code == 400
