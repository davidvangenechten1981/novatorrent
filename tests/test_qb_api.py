import time

from fastapi.testclient import TestClient

from conftest import PASSWORD, info_hash, make_torrent
from novatorrent.app import create_app


def test_health_without_login(cfg):
    with TestClient(create_app(cfg)) as c:
        assert c.get("/api/health").status_code == 200


def test_auth(cfg):
    with TestClient(create_app(cfg)) as c:
        assert c.get("/api/v2/app/version").status_code == 403
        bad = c.post("/api/v2/auth/login", data={"username": "admin", "password": "wrong"})
        assert bad.text == "Fails."
        r = c.post("/api/v2/auth/login", data={"username": "admin", "password": PASSWORD})
        assert r.text == "Ok." and "SID" in r.cookies
        assert c.get("/api/v2/app/version").text.startswith("v")
        assert c.get("/api/v2/app/webapiVersion").text == "2.9.3"


def test_brute_force_lockout(cfg):
    with TestClient(create_app(cfg)) as c:
        for _ in range(5):
            c.post("/api/v2/auth/login", data={"username": "admin", "password": "x"})
        r = c.post("/api/v2/auth/login", data={"username": "admin", "password": PASSWORD})
        assert r.status_code == 403


def test_add_info_delete(client, tmp_path):
    data, _ = make_torrent(tmp_path / "src")
    h = info_hash(data)
    client.post("/api/v2/torrents/createCategory", data={"category": "radarr", "savePath": ""})
    r = client.post(
        "/api/v2/torrents/add",
        files={"torrents": ("a.torrent", data)},
        data={"category": "radarr", "paused": "true", "tags": "x,y"},
    )
    assert r.text == "Ok."
    infos = client.get("/api/v2/torrents/info", params={"category": "radarr"}).json()
    assert len(infos) == 1
    t = infos[0]
    assert t["hash"] == h and t["state"] == "pausedDL" and t["category"] == "radarr"
    assert set(t["tags"].split(", ")) == {"x", "y"}
    for k in (
        "name",
        "size",
        "progress",
        "dlspeed",
        "upspeed",
        "eta",
        "save_path",
        "content_path",
        "ratio",
        "seeding_time",
        "added_on",
        "completion_on",
        "amount_left",
        "num_seeds",
        "num_leechs",
    ):
        assert k in t, k
    assert t["content_path"].endswith("payload.bin")
    assert client.get("/api/v2/torrents/info", params={"hashes": h}).json()[0]["hash"] == h
    files = client.get("/api/v2/torrents/files", params={"hash": h}).json()
    assert files[0]["name"] == "payload.bin"
    props = client.get("/api/v2/torrents/properties", params={"hash": h}).json()
    assert props["piece_size"] == 65536
    assert client.get("/api/v2/torrents/trackers", params={"hash": h}).status_code == 200
    client.post("/api/v2/torrents/resume", data={"hashes": h})
    client.post("/api/v2/torrents/delete", data={"hashes": h, "deleteFiles": "true"})
    assert client.get("/api/v2/torrents/info").json() == []


def test_add_magnet_and_dedupe(client):
    h = "a" * 40
    for _ in range(2):
        r = client.post("/api/v2/torrents/add", data={"urls": f"magnet:?xt=urn:btih:{h}&dn=test", "paused": "true"})
        assert r.text == "Ok."
    items = client.get("/api/v2/torrents/info").json()
    assert len(items) == 1 and items[0]["hash"] == h


def test_categories_tags(client):
    post = client.post
    assert post("/api/v2/torrents/createCategory", data={"category": "sonarr", "savePath": "/x"}).text == "Ok."
    assert post("/api/v2/torrents/createCategory", data={"category": "sonarr"}).status_code == 409
    assert post("/api/v2/torrents/editCategory", data={"category": "sonarr", "savePath": "/y"}).text == "Ok."
    assert client.get("/api/v2/torrents/categories").json()["sonarr"]["savePath"] == "/y"
    post("/api/v2/torrents/createTags", data={"tags": "a,b"})
    assert client.get("/api/v2/torrents/tags").json() == ["a", "b"]
    post("/api/v2/torrents/removeCategories", data={"categories": "sonarr"})
    assert client.get("/api/v2/torrents/categories").json() == {}


def test_sync_maindata(client):
    r = client.get("/api/v2/sync/maindata", params={"rid": 0}).json()
    assert r["full_update"] is True and "server_state" in r
    rid = r["rid"]
    client.post("/api/v2/torrents/add", data={"urls": "magnet:?xt=urn:btih:" + "b" * 40, "paused": "true"})
    d = client.get("/api/v2/sync/maindata", params={"rid": rid}).json()
    assert "b" * 40 in d["torrents"]
    rid = d["rid"]
    client.post("/api/v2/torrents/delete", data={"hashes": "b" * 40, "deleteFiles": "false"})
    d = client.get("/api/v2/sync/maindata", params={"rid": rid}).json()
    assert "b" * 40 in d["torrents_removed"]


def test_preferences_roundtrip(client):
    p = client.get("/api/v2/app/preferences").json()
    assert "save_path" in p and "proxy_password" not in p
    client.post("/api/v2/app/setPreferences", data={"json": '{"max_active_downloads": 7, "bogus": 1}'})
    assert client.get("/api/v2/app/preferences").json()["max_active_downloads"] == 7
    ti = client.get("/api/v2/transfer/info").json()
    assert "dl_info_speed" in ti and "connection_status" in ti


def test_state_mapping(client, tmp_path):
    data, _ = make_torrent(tmp_path / "s2")
    h = info_hash(data)
    client.post("/api/v2/torrents/add", files={"torrents": ("a.torrent", data)})
    client.post("/api/v2/torrents/pause", data={"hashes": h})
    time.sleep(0.5)
    assert client.get("/api/v2/torrents/info").json()[0]["state"] == "pausedDL"


def test_readonly_user_and_apikey(client):
    auth = client.app.state.auth
    auth.set_user("viewer", "viewer-pass-1", "readonly")
    key = auth.create_api_key("homepage", "readonly")
    with TestClient(client.app) as c2:
        login = c2.post("/api/v2/auth/login", data={"username": "viewer", "password": "viewer-pass-1"})
        assert login.text == "Ok."
        assert c2.get("/api/v2/torrents/info").status_code == 200
        assert c2.post("/api/v2/torrents/pause", data={"hashes": "all"}).status_code == 403
    with TestClient(client.app) as c3:
        ok = c3.get("/api/v2/torrents/info", headers={"Authorization": f"Bearer {key}"})
        assert ok.status_code == 200
        bad = c3.get("/api/v2/torrents/info", headers={"Authorization": "Bearer nt_bad"})
        assert bad.status_code == 403
