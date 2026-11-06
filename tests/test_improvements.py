"""Small robustness/observability improvements."""


def test_unsupported_api_call_is_logged_once(client):
    for _ in range(3):
        assert client.get("/api/v2/torrents/noSuchThing").status_code == 404
    assert client.get("/api/v2/foo/bar-baz").status_code == 404  # unsafe characters: not logged
    logs = client.get("/api/v2/log/main", params={"normal": "true", "info": "true", "warning": "true"}).json()
    msgs = [x["message"] for x in logs if x["message"].startswith("Unsupported")]
    assert len(msgs) == 1 and msgs[0].endswith("noSuchThing") and "bar-baz" not in msgs[0]


def test_peerlist_size_is_configurable(client):
    eng = client.app.state.engine
    assert eng.ses.get_settings()["max_peerlist_size"] == 3000
    client.post("/api/v2/app/setPreferences", data={"json": '{"nova_max_peerlist_size": 500}'})
    assert eng.ses.get_settings()["max_peerlist_size"] == 500
    client.post("/api/v2/app/setPreferences", data={"json": '{"nova_max_peerlist_size": 1}'})
    assert eng.ses.get_settings()["max_peerlist_size"] == 100  # clamped, 0/tiny values would break peer discovery


def test_early_disk_warning_without_pausing(tmp_path):
    from novatorrent.manager import Manager
    from test_automation import make_engine

    e = make_engine(tmp_path, "a", 0)
    try:
        e.add(magnet="magnet:?xt=urn:btih:" + "e" * 40)
        e.settings.update({"nova_min_free_space_mb": 1024, "nova_warn_free_space_mb": 5120})
        free = {"v": 3 * 2**30}  # between pause level (1 GiB) and warning level (5 GiB)
        events = []
        e.on_event = lambda n, i: events.append(n)
        m = Manager(e, free_space=lambda p: free["v"])
        m.tick()
        m.tick()
        assert events == ["disk_low"]  # warned once, not repeatedly
        assert not e.list_info()[0]["state"].startswith("paused")  # downloads keep running
        free["v"] = 20 * 2**30
        m.tick()
        free["v"] = 3 * 2**30
        m.tick()
        assert events == ["disk_low", "disk_low"]  # re-armed after recovering
        free["v"] = 100 * 2**20  # below pause level: the existing guard takes over
        m.tick()
        assert "disk_full" in events and e.list_info()[0]["state"] == "pausedDL"
    finally:
        e.stop()


def test_static_ui_files_are_revalidated_but_api_is_untouched(client):
    r = client.get("/app.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache" and r.headers.get("etag")
    assert client.get("/manifest.webmanifest").status_code == 200
    assert client.get("/icon.svg").headers["content-type"].startswith("image/svg")
    assert "cache-control" not in client.get("/api/v2/app/version").headers
