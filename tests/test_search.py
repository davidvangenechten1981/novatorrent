"""Torznab search: fake indexer on localhost, key handling, add by result id."""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from conftest import make_torrent
from novatorrent import netguard
from novatorrent.search import normalize_indexer, parse_results

KEY = "sekrit-indexer-key-123"  # fake test credential
HASH_A = "a" * 40
HASH_B = "b" * 40


def torznab(items: list[str]) -> bytes:
    return (
        '<?xml version="1.0"?><rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">'
        "<channel><title>x</title>" + "".join(items) + "</channel></rss>"
    ).encode()


def item(title, url, seeders, peers, size, infohash="", magnet=""):
    attrs = f'<torznab:attr name="seeders" value="{seeders}"/><torznab:attr name="peers" value="{peers}"/>'
    if infohash:
        attrs += f'<torznab:attr name="infohash" value="{infohash}"/>'
    if magnet:
        attrs += f'<torznab:attr name="magneturl" value="{magnet}"/>'
    return (
        f"<item><title>{title}</title><link>{url}</link>"
        f'<enclosure url="{url}" length="{size}" type="application/x-bittorrent"/>'
        f"<pubDate>Wed, 04 Nov 2026 10:00:00 +0000</pubDate>{attrs}</item>"
    )


class Fake(BaseHTTPRequestHandler):
    torrent = b""
    seen: list[str] = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        Fake.seen.append(self.path)
        if u.path == "/dl/1.torrent":
            body, ctype = Fake.torrent, "application/x-bittorrent"
        elif q.get("apikey") != [KEY]:
            body, ctype = b'<error code="100" description="Incorrect user credentials"/>', "application/xml"
        elif q.get("t") == ["caps"]:
            body, ctype = b"<caps><server/></caps>", "application/xml"
        else:
            base = f"http://127.0.0.1:{self.server.server_port}"
            body = torznab(
                [
                    item("Linux ISO low", f"{base}/dl/1.torrent", 3, 5, 1000, HASH_B),
                    item("Linux ISO best", f"{base}/dl/1.torrent", 50, 60, 2000, HASH_A),
                    item("Linux ISO best dup", f"{base}/dl/1.torrent", 7, 9, 2000, HASH_A),
                    item("Magnet one", "", 10, 12, 500, "c" * 40, f"magnet:?xt=urn:btih:{'c' * 40}&amp;dn=m"),
                ]
            )
            ctype = "application/xml"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(autouse=True)
def csrf(client):
    """The web-UI sends the per-session CSRF token on every write; do the same here."""
    client.headers["x-csrf-token"] = client.get("/api/nova/session").json()["csrf"]


@pytest.fixture
def indexer_url(tmp_path, monkeypatch):
    monkeypatch.setattr(netguard, "ALLOW_LOOPBACK", True)
    Fake.torrent, _ = make_torrent(tmp_path / "t", "linux.bin", 70000)
    Fake.seen = []
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/1/api"
    srv.shutdown()


def add_indexer(client, url, key=KEY, name="Fake"):
    r = client.post("/api/nova/search/indexers", json={"name": name, "url": url, "apikey": key})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_normalize_moves_key_out_of_url():
    assert normalize_indexer("http://h:9696/1/api/?apikey=abc&x=1", "") == ("http://h:9696/1/api", "abc")
    assert normalize_indexer("http://h/api", " k ") == ("http://h/api", "k")
    with pytest.raises(ValueError):
        normalize_indexer("ftp://h/api", "k")


def test_parse_rejects_dtd_and_reports_error():
    with pytest.raises(ValueError, match="DTD"):
        parse_results(b'<!DOCTYPE x [<!ENTITY a "b">]><rss/>')
    with pytest.raises(ValueError, match="indexer error 100"):
        parse_results(b'<error code="100" description="x"/>')


def test_indexer_list_never_contains_key(client, indexer_url):
    add_indexer(client, indexer_url)
    r = client.get("/api/nova/search/indexers")
    assert KEY not in r.text
    assert r.json()[0]["has_key"] is True


def test_search_sorted_deduped_and_hides_urls_and_key(client, indexer_url):
    add_indexer(client, indexer_url)
    r = client.get("/api/nova/search", params={"q": "linux"})
    assert r.status_code == 200
    body = r.json()
    titles = [x["title"] for x in body["results"]]
    assert titles == ["Linux ISO best", "Magnet one", "Linux ISO low"]  # dedupe keeps best seeded, sorted
    assert body["results"][0]["leechers"] == 10 and body["results"][0]["size"] == 2000
    assert body["results"][0]["indexer"] == "Fake" and body["results"][0]["published"] > 0
    assert KEY not in r.text and "http" not in r.text and "url" not in body["results"][0]
    assert body["errors"] == {}


def test_wrong_key_is_reported_without_leaking_it(client, indexer_url):
    add_indexer(client, indexer_url, key="wrong-key-value")
    r = client.get("/api/nova/search", params={"q": "linux"}).json()
    assert r["results"] == [] and r["errors"] == {"Fake": "indexer error 100"}
    assert "wrong-key-value" not in str(r)


def test_indexer_test_endpoint(client, indexer_url):
    good, bad = add_indexer(client, indexer_url, name="good"), add_indexer(client, indexer_url, key="nope", name="bad")
    assert client.post(f"/api/nova/search/indexers/{good}/test", json={}).json() == {"ok": True, "error": ""}
    assert client.post(f"/api/nova/search/indexers/{bad}/test", json={}).json()["ok"] is False
    assert client.post("/api/nova/search/indexers/nope/test", json={}).status_code == 404


def test_add_torrent_and_magnet_by_result_id(client, indexer_url):
    client.post("/api/v2/torrents/createCategory", data={"category": "movies", "savePath": ""})
    add_indexer(client, indexer_url)
    results = client.get("/api/nova/search", params={"q": "linux"}).json()["results"]
    by_title = {x["title"]: x["rid"] for x in results}
    for title in ("Linux ISO best", "Magnet one"):
        r = client.post("/api/nova/search/add", json={"rid": by_title[title], "category": "movies", "paused": True})
        assert r.status_code == 200, r.text
    infos = {i["hash"]: i for i in client.get("/api/v2/torrents/info").json()}
    assert "c" * 40 in infos and infos["c" * 40]["category"] == "movies"
    assert len(infos) == 2  # the .torrent file plus the magnet
    assert client.post("/api/nova/search/add", json={"rid": "unknown"}).status_code == 404


def test_blocked_and_invalid_urls(client):
    assert (
        client.post("/api/nova/search/indexers", json={"name": "x", "url": "ftp://h/api", "apikey": ""}).status_code
        == 400
    )
    add_indexer(client, "http://169.254.169.254/latest/api", key="k", name="meta")
    r = client.get("/api/nova/search", params={"q": "x"}).json()
    assert r["errors"] == {"meta": "blocked address"}


def test_query_required_and_admin_only(client, cfg):
    assert client.get("/api/nova/search", params={"q": "  "}).status_code == 400
    client.app.state.auth.set_user("ro", "readonly-pass-1", "readonly")
    with TestClient(client.app) as ro:
        ro.post("/api/v2/auth/login", data={"username": "ro", "password": "readonly-pass-1"})
        assert ro.get("/api/nova/search", params={"q": "x"}).status_code == 403
        assert ro.get("/api/nova/search/indexers").status_code == 403
        assert ro.post("/api/nova/search/add", json={"rid": "x"}).status_code == 403
