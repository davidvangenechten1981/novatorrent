"""RSS/Torznab feeds with filter rules and season/episode de-duplication."""

from __future__ import annotations

import re
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from . import netguard
from .engine import Engine

MAX_FEED_BYTES = 5 * 1024 * 1024
EP_RE = re.compile(r"(?i)\bS(\d{1,2})[ ._-]?E(\d{1,3})\b|\b(\d{1,2})x(\d{2,3})\b")


def parse_feed(data: bytes) -> list[dict[str, str]]:
    upper = data.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError("DTD/entities are not allowed in feeds")
    root = ET.fromstring(data)
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        guid = (it.findtext("guid") or "").strip()
        link = (it.findtext("link") or "").strip()
        magnet = ""
        enclosure = ""
        for el in it:
            tag = el.tag.rsplit("}", 1)[-1]
            if tag == "enclosure":
                enclosure = el.get("url", "")
            elif tag == "attr" and el.get("name") == "magneturl":
                magnet = el.get("value", "")
        url = magnet or enclosure or link
        if title and url:
            items.append({"title": title, "guid": guid or url, "url": url})
    return items


def episode_key(rule_id: str, title: str) -> str | None:
    m = EP_RE.search(title)
    if not m:
        return None
    season, ep = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    show = re.sub(r"[^a-z0-9]+", " ", title[: m.start()].lower()).strip()
    return f"{rule_id}|{show}|S{int(season):02d}E{int(ep):02d}"


def rule_matches(rule: dict[str, Any], title: str) -> bool:
    inc, exc = rule.get("must_contain", ""), rule.get("must_not_contain", "")
    low = title.lower()
    if rule.get("use_regex"):
        if inc and not re.search(inc, title, re.I):
            return False
        return not (exc and re.search(exc, title, re.I))
    if inc and not all(t in low for t in inc.lower().split()):
        return False
    return not (exc and any(t in low for t in exc.lower().split()))


class RssManager:
    def __init__(self, engine: Engine):
        self.e = engine
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="rss", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def data(self) -> dict[str, Any]:
        return self.e.state.data.setdefault("rss", {"feeds": {}, "rules": {}, "seen": {}, "episodes": []})

    def add_feed(self, name: str, url: str, interval_min: int = 30) -> str:
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("feed URL must be http(s)")
        fid = uuid.uuid4().hex[:8]
        with self.e.state.lock:
            self.data()["feeds"][fid] = {
                "name": name,
                "url": url,
                "interval_min": max(int(interval_min), 5),
                "last_fetch": 0,
                "last_error": "",
            }
        self.e.state.save()
        return fid

    def add_rule(self, rule: dict[str, Any]) -> str:
        if rule.get("use_regex"):
            for k in ("must_contain", "must_not_contain"):
                re.compile(rule.get(k, "") or "")  # validate early
        rid = uuid.uuid4().hex[:8]
        keep = (
            "name",
            "feed_ids",
            "must_contain",
            "must_not_contain",
            "use_regex",
            "category",
            "save_path",
            "paused",
            "smart_filter",
            "enabled",
        )
        with self.e.state.lock:
            self.data()["rules"][rid] = {k: rule[k] for k in keep if k in rule}
            self.data()["rules"][rid].setdefault("enabled", True)
        self.e.state.save()
        return rid

    def _loop(self) -> None:
        while not self._stop.wait(30):
            try:
                self.run_due()
            except Exception as ex:
                self.e.add_log(f"rss error: {type(ex).__name__}", 4)

    def run_due(self) -> None:
        now = time.time()
        for fid, f in list(self.data()["feeds"].items()):
            if now - f.get("last_fetch", 0) >= f["interval_min"] * 60:
                self.run_feed(fid)

    def fetch(self, url: str) -> bytes:
        netguard.check_url(url)
        with httpx.Client(timeout=30, follow_redirects=False) as c:
            r = c.get(url, headers={"User-Agent": "NovaTorrent"})
            r.raise_for_status()
            if len(r.content) > MAX_FEED_BYTES:
                raise ValueError("feed too large")
            return r.content

    def run_feed(self, fid: str) -> int:
        from .qbapi import fetch_torrent

        feed = self.data()["feeds"].get(fid)
        if not feed:
            return 0
        feed["last_fetch"] = int(time.time())
        if not self.e.vpn_ok:
            feed["last_error"] = "paused: VPN binding unavailable"
            return 0
        added = 0
        try:
            items = parse_feed(self.fetch(feed["url"]))
            feed["last_error"] = ""
        except Exception as ex:
            feed["last_error"] = f"{type(ex).__name__}: {ex}"[:200]
            self.e.state.save()
            return 0
        seen, eps = self.data()["seen"], self.data()["episodes"]
        for item in items:
            if item["guid"] in seen:
                continue
            for rid, rule in self.data()["rules"].items():
                if not rule.get("enabled", True):
                    continue
                if rule.get("feed_ids") and fid not in rule["feed_ids"]:
                    continue
                if not rule_matches(rule, item["title"]):
                    continue
                key = episode_key(rid, item["title"]) if rule.get("smart_filter") else None
                if key and key in eps:
                    continue
                try:
                    common = dict(
                        category=rule.get("category", ""),
                        save_path=rule.get("save_path") or None,
                        paused=bool(rule.get("paused")),
                    )
                    if item["url"].lower().startswith("magnet:"):
                        self.e.add(magnet=item["url"], **common)
                    else:
                        kind, payload = fetch_torrent(item["url"])
                        arg = {"magnet": payload} if kind == "magnet" else {"data": payload}
                        self.e.add(**arg, **common)
                except Exception as ex:
                    self.e.add_log(f"RSS add failed for '{item['title'][:60]}': {type(ex).__name__}", 4)
                    continue
                seen[item["guid"]] = int(time.time())
                if key:
                    eps.append(key)
                added += 1
                break
        if len(seen) > 5000:  # keep the dedupe store bounded
            for g in sorted(seen, key=seen.get)[: len(seen) - 4000]:
                del seen[g]
        self.e.state.save()
        return added
