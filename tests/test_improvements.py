"""Small robustness/observability improvements."""


def test_unsupported_api_call_is_logged_once(client):
    for _ in range(3):
        assert client.get("/api/v2/torrents/noSuchThing").status_code == 404
    assert client.get("/api/v2/foo/bar-baz").status_code == 404  # unsafe characters: not logged
    logs = client.get("/api/v2/log/main", params={"normal": "true", "info": "true", "warning": "true"}).json()
    msgs = [x["message"] for x in logs if x["message"].startswith("Unsupported")]
    assert len(msgs) == 1 and msgs[0].endswith("noSuchThing") and "bar-baz" not in msgs[0]
