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
