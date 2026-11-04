import datetime as dt
import json
import shlex
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conftest import info_hash, make_torrent
from novatorrent import netguard
from novatorrent.manager import Manager, limit_reached, schedule_active
from novatorrent.notify import Notifier, mask_notify, merge_notify, notify_config
from novatorrent.rss import RssManager, episode_key, parse_feed, rule_matches
from novatorrent.settings import DEFAULTS
from test_p2p import make_engine, wait


class Capture(BaseHTTPRequestHandler):
    posts: list = []
    routes: dict = {}

    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        Capture.posts.append((self.path, self.rfile.read(n)))
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        body = Capture.routes.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def http_server():
    Capture.posts, Capture.routes = [], {}
    srv = HTTPServer(("127.0.0.1", 0), Capture)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def settings(**kw):
    d = dict(DEFAULTS)
    d.update(kw)
    return d


def test_alt_speed_schedule():
    s = settings(scheduler_enabled=True, schedule_from_hour=8, schedule_to_hour=20, scheduler_days=0)
    mon = dt.datetime(2026, 11, 2, 12, 0)  # a Monday
    assert schedule_active(s, mon)
    assert not schedule_active(s, mon.replace(hour=21))
    night = settings(scheduler_enabled=True, schedule_from_hour=22, schedule_to_hour=6)
    assert schedule_active(night, mon.replace(hour=23)) and schedule_active(night, mon.replace(hour=3))
    assert not schedule_active(night, mon.replace(hour=12))
    weekend = settings(scheduler_enabled=True, scheduler_days=2, schedule_from_hour=0, schedule_to_hour=23)
    assert not schedule_active(weekend, mon) and schedule_active(weekend, mon + dt.timedelta(days=5))
    only_tue = settings(scheduler_enabled=True, scheduler_days=4, schedule_from_hour=0, schedule_to_hour=23)
    assert schedule_active(only_tue, mon + dt.timedelta(days=1)) and not schedule_active(only_tue, mon)


def test_limit_resolution():
    info = {
        "ratio": 2.5,
        "ratio_limit": -2,
        "seeding_time": 100,
        "seeding_time_limit": -2,
        "inactive_seeding_time_limit": -2,
        "last_activity": time.time(),
    }
    assert limit_reached(info, settings()) == ""
    assert limit_reached(info, settings(max_ratio_enabled=True, max_ratio=2.0)) == "ratio"
    assert limit_reached({**info, "ratio_limit": -1}, settings(max_ratio_enabled=True, max_ratio=1.0)) == ""
    assert limit_reached(info, settings(max_seeding_time_enabled=True, max_seeding_time=1)) == "seeding_time"
    old = {**info, "last_activity": time.time() - 3600}
    assert (
        limit_reached(old, settings(max_inactive_seeding_time_enabled=True, max_inactive_seeding_time=30)) == "inactive"
    )


def seeded_engine(tmp_path):
    data, src = make_torrent(tmp_path / "origin")
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    shutil.copy(src, seed_dir / src.name)
    e = make_engine(tmp_path, "a", 0)
    h = e.add(data=data, save_path=str(seed_dir))
    assert wait(lambda: e.list_info()[0]["progress"] == 1.0)
    return e, h, seed_dir / src.name


def test_seed_rule_pause_and_remove(tmp_path):
    e, h, f = seeded_engine(tmp_path)
    try:
        e.meta(h)["ratio_limit"] = 0  # reached immediately
        m = Manager(e)
        e.settings.update({"max_ratio_act": 0})
        m.tick()
        assert wait(lambda: e.list_info()[0]["state"] == "pausedUP", 5)
        e.resume(h)
        e.settings.update({"max_ratio_act": 2})
        wait(lambda: e.list_info()[0]["state"] != "pausedUP", 5)
        m.tick()
        assert e.list_info() == [] and not f.exists()  # removed together with its data
    finally:
        e.stop()


def test_disk_guard_pauses_and_resumes(tmp_path):
    e = make_engine(tmp_path, "a", 0)
    try:
        e.add(magnet="magnet:?xt=urn:btih:" + "d" * 40)
        free = {"v": 10 * 2**20}
        m = Manager(e, free_space=lambda p: free["v"])
        events = []
        e.on_event = lambda n, i: events.append(n)
        m.tick()
        assert e.list_info()[0]["state"] == "pausedDL" and "disk_full" in events
        free["v"] = 100 * 2**30
        m.tick()
        assert not e.list_info()[0]["state"].startswith("paused")
    finally:
        e.stop()


def test_extra_trackers(tmp_path):
    e = make_engine(tmp_path, "a", 0)
    try:
        e.settings.update(
            {"nova_extra_trackers_enabled": True, "nova_extra_trackers": "udp://tracker.example:1337/announce"}
        )
        h = e.add(magnet="magnet:?xt=urn:btih:" + "e" * 40)
        urls = [t["url"] for t in e.trackers(h)]
        assert "udp://tracker.example:1337/announce" in urls
        e.add_trackers(h, ["http://t2.example/announce"])
        e.edit_tracker(h, "http://t2.example/announce", "http://t3.example/announce")
        assert "http://t3.example/announce" in [t["url"] for t in e.trackers(h)]
        e.remove_trackers(h, ["http://t3.example/announce"])
        assert "http://t3.example/announce" not in [t["url"] for t in e.trackers(h)]
    finally:
        e.stop()


def test_ban_peer_persists(tmp_path):
    e = make_engine(tmp_path, "a", 0)
    try:
        e.ban_peer("203.0.113.9")
        assert e.ses.get_ip_filter().access("203.0.113.9") != 0
        assert "203.0.113.9" in e.state.get("banned")
        with pytest.raises(ValueError):
            e.ban_peer("not an ip")
    finally:
        e.stop()


FEED = """<?xml version="1.0"?><rss xmlns:torznab="http://torznab.com/schemas/2015/feed"><channel>
<item><title>My.Show.S01E02.1080p.WEB</title><guid>g1</guid><enclosure url="%(u1)s" type="application/x-bittorrent"/></item>
<item><title>My.Show.S01E02.720p.HDTV</title><guid>g2</guid><enclosure url="%(u2)s" type="application/x-bittorrent"/></item>
<item><title>Other.Thing.2024</title><guid>g3</guid><link>magnet:?xt=urn:btih:%(h3)s</link></item>
<item><title>My.Show.S01E03.1080p.WEB</title><guid>g4</guid><torznab:attr name="magneturl" value="magnet:?xt=urn:btih:%(h4)s"/><link>http://x/y</link></item>
</channel></rss>"""


def test_rss_parse_match_and_dedupe(tmp_path, http_server, monkeypatch):
    monkeypatch.setattr(netguard, "ALLOW_LOOPBACK", True)
    t1, _ = make_torrent(tmp_path / "t1", "one.bin", 70000)
    t2, _ = make_torrent(tmp_path / "t2", "two.bin", 70000)
    Capture.routes["/t1.torrent"], Capture.routes["/t2.torrent"] = t1, t2
    feed = FEED % {
        "u1": f"{http_server}/t1.torrent",
        "u2": f"{http_server}/t2.torrent",
        "h3": "3" * 40,
        "h4": "4" * 40,
    }
    Capture.routes["/feed.xml"] = feed.encode()
    items = parse_feed(Capture.routes["/feed.xml"])
    assert len(items) == 4 and items[3]["url"].startswith("magnet:")  # torznab magneturl preferred
    assert episode_key("r", "My.Show.S01E02.1080p") == episode_key("r", "my show s1e2 720p")
    assert rule_matches({"must_contain": "my show"}, "My.Show".replace(".", " ")) is True
    assert rule_matches(
        {"must_contain": r"S\d+E02", "use_regex": True, "must_not_contain": "720p"}, "My.Show.S01E02.1080p"
    )
    assert not rule_matches({"must_contain": "x", "must_not_contain": "y"}, "x y")
    with pytest.raises(ValueError):
        parse_feed(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><rss/>')

    e = make_engine(tmp_path, "rss", 0)
    try:
        rss = RssManager(e)
        fid = rss.add_feed("test", f"{http_server}/feed.xml")
        rss.add_rule(
            {
                "name": "show",
                "must_contain": "my show",
                "use_regex": False,
                "category": "tv",
                "smart_filter": True,
                "paused": True,
            }
        )
        # "my show" has a space but titles use dots -> use regex rule instead
        rss.data()["rules"].clear()
        rss.add_rule(
            {
                "name": "show",
                "must_contain": r"my[. ]show",
                "use_regex": True,
                "category": "tv",
                "smart_filter": True,
                "paused": True,
            }
        )
        assert rss.run_feed(fid) == 2  # S01E02 once (dedupe) + S01E03 magnet
        hashes = {i["hash"] for i in e.list_info()}
        assert info_hash(t1) in hashes and info_hash(t2) not in hashes and "4" * 40 in hashes
        assert rss.run_feed(fid) == 0  # nothing new on a second run
        assert all(i["category"] == "tv" for i in e.list_info())
    finally:
        e.stop()


def test_rss_blocks_link_local(tmp_path):
    e = make_engine(tmp_path, "rss", 0)
    try:
        rss = RssManager(e)
        fid = rss.add_feed("evil", "http://169.254.169.254/latest/meta-data")
        assert rss.run_feed(fid) == 0
        assert "blocked" in rss.data()["feeds"][fid]["last_error"]
    finally:
        e.stop()


def test_notifications_and_secret_masking(tmp_path, http_server):
    e = make_engine(tmp_path, "n", 0)
    try:
        cfg = {
            "discord_url": f"{http_server}/discord",
            "ntfy_url": f"{http_server}/ntfy",
            "webhook_url": f"{http_server}/hook",
            "events": {"completed": True, "added": False},
        }
        e.settings.update({"nova_notify": cfg})
        n = Notifier(e)
        n.start()
        n.dispatch("added", {"name": "x"})  # disabled event: nothing sent
        n.dispatch("completed", {"name": "Ubuntu ISO"})
        assert wait(lambda: len(Capture.posts) >= 3, 10)
        paths = {p for p, _ in Capture.posts}
        assert paths == {"/discord", "/ntfy", "/hook"}
        body = {p: b for p, b in Capture.posts}
        assert b"Finished: Ubuntu ISO" in body["/discord"]
        assert json.loads(body["/hook"])["event"] == "completed"
        n.stop()
        masked = mask_notify(notify_config(e.settings.snapshot()))
        assert masked["discord_url"] == "***" and "127.0.0.1" not in json.dumps(masked)
        merged = merge_notify(cfg, {"discord_url": "***", "ntfy_url": "http://new/"})
        assert merged["discord_url"] == cfg["discord_url"] and merged["ntfy_url"] == "http://new/"
    finally:
        e.stop()


def test_hook_runs_without_shell_and_gets_env(tmp_path):
    e = make_engine(tmp_path, "h", 0)
    out = tmp_path / "hook.out"
    try:
        h = e.add(magnet="magnet:?xt=urn:btih:" + "f" * 40 + "&dn=x", category="movies")
        code = f"import os; open({str(out)!r},'w').write(os.environ['NOVA_HASH']+'|'+os.environ['NOVA_CATEGORY'])"
        cmd = shlex.join([sys.executable.replace("\\", "/"), "-I", "-c", code])
        e.settings.update({"nova_hook_enabled": True, "nova_hook_command": cmd})
        n = Notifier(e)
        n.dispatch("completed", {"hash": h, "name": "x"})
        assert wait(out.exists, 15)
        assert out.read_text() == f"{h}|movies"
        # disabled by default
        out.unlink()
        e.settings.update({"nova_hook_enabled": False})
        n.dispatch("completed", {"hash": h, "name": "x"})
        time.sleep(1)
        assert not out.exists()
    finally:
        e.stop()


def test_prefs_mask_secrets_via_api(client):
    client.post(
        "/api/v2/app/setPreferences",
        data={"json": json.dumps({"nova_notify": {"discord_url": "https://discord.example/hook/secret"}})},
    )
    p = client.get("/api/v2/app/preferences").json()
    assert p["nova_notify"]["discord_url"] == "***"
    client.post("/api/v2/app/setPreferences", data={"json": json.dumps({"nova_notify": p["nova_notify"]})})
    assert client.app.state.settings.get("nova_notify")["discord_url"].endswith("secret")
