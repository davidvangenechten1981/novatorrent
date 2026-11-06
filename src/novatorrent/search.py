"""Torznab search across user-configured indexers (e.g. Prowlarr) with one-click add.

Indexer API keys and result download URLs (which often embed a key) stay on the server: the browser only
gets an opaque result id (`rid`) that is resolved here when the user clicks "add".
"""

from __future__ import annotations

import threading
import time
import uuid
import xml.etree.ElementTree as ET
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qs, urlparse, urlunparse

import httpx

from . import netguard
from .engine import Engine
from .rss import MAX_FEED_BYTES, safe_xml_root

CACHE_MAX = 500
CACHE_TTL = 1800  # seconds a result id stays valid
MAX_QUERY = 200


def normalize_indexer(url: str, apikey: str) -> tuple[str, str]:
    """Return (clean url without query/fragment, api key). A key pasted inside the URL is moved out."""
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("indexer URL must be http(s)")
    key = apikey.strip() or parse_qs(u.query).get("apikey", [""])[0]
    return urlunparse((u.scheme, u.netloc, u.path.rstrip("/"), "", "", "")), key


def _int(v: Any) -> int:
    try:
        return max(int(v), 0)
    except (TypeError, ValueError):
        return 0


def parse_results(data: bytes) -> list[dict[str, Any]]:
    root = safe_xml_root(data)
    if root.tag.rsplit("}", 1)[-1] == "error":
        raise ValueError(f"indexer error {root.get('code', '?')}")
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        attrs: dict[str, str] = {}
        enclosure, enc_len = "", 0
        for el in it:
            tag = el.tag.rsplit("}", 1)[-1]
            if tag == "enclosure":
                enclosure, enc_len = el.get("url", ""), _int(el.get("length"))
            elif tag == "attr":
                attrs[el.get("name", "")] = el.get("value", "")
        url = attrs.get("magneturl") or enclosure or (it.findtext("link") or "").strip()
        if not title or not url:
            continue
        seeders, peers = _int(attrs.get("seeders")), _int(attrs.get("peers"))
        try:
            published = int(parsedate_to_datetime((it.findtext("pubDate") or "").strip()).timestamp())
        except (TypeError, ValueError, IndexError):
            published = 0
        out.append(
            {
                "title": title,
                "url": url,
                "size": _int(attrs.get("size")) or _int(it.findtext("size")) or enc_len,
                "seeders": seeders,
                "leechers": max(peers - seeders, 0),
                "published": published,
                "infohash": attrs.get("infohash", "").lower(),
            }
        )
    return out


def describe_error(ex: Exception) -> str:
    """Short error text that never contains the request URL (it carries the API key)."""
    if isinstance(ex, httpx.HTTPStatusError):
        return f"HTTP {ex.response.status_code}"
    if isinstance(ex, httpx.HTTPError):
        return type(ex).__name__
    if isinstance(ex, ET.ParseError):
        return "invalid XML"
    if isinstance(ex, ValueError):
        return str(ex)[:80]
    return type(ex).__name__


class SearchManager:
    def __init__(self, engine: Engine):
        self.e = engine
        self._cache: OrderedDict[str, tuple[float, str, str]] = OrderedDict()  # rid -> (time, url, title)
        self._lock = threading.Lock()

    # ---- indexers ---------------------------------------------------------------------------
    def data(self) -> dict[str, Any]:
        return self.e.state.data.setdefault("search", {"indexers": {}})

    def add_indexer(self, name: str, url: str, apikey: str) -> str:
        name = name.strip()[:60]
        if not name:
            raise ValueError("name required")
        clean, key = normalize_indexer(url, apikey)
        iid = uuid.uuid4().hex[:8]
        with self.e.state.lock:
            self.data()["indexers"][iid] = {"name": name, "url": clean, "apikey": key}
        self.e.state.save()
        return iid

    def remove_indexer(self, iid: str) -> bool:
        with self.e.state.lock:
            found = self.data()["indexers"].pop(iid, None) is not None
        self.e.state.save()
        return found

    def list_indexers(self) -> list[dict[str, Any]]:
        """Never includes the API key."""
        with self.e.state.lock:
            return [
                {"id": i, "name": x["name"], "url": x["url"], "has_key": bool(x["apikey"])}
                for i, x in self.data()["indexers"].items()
            ]

    def _request(self, ix: dict[str, Any], params: dict[str, str]) -> bytes:
        netguard.check_url(ix["url"])
        q = dict(params)
        if ix["apikey"]:
            q["apikey"] = ix["apikey"]
        with httpx.Client(timeout=20, follow_redirects=False) as c:
            r = c.get(ix["url"], params=q, headers={"User-Agent": "NovaTorrent"})
            r.raise_for_status()
            if len(r.content) > MAX_FEED_BYTES:
                raise ValueError("response too large")
            return r.content

    def test(self, iid: str) -> dict[str, Any]:
        ix = self.data()["indexers"].get(iid)
        if not ix:
            raise KeyError(iid)
        try:
            root = safe_xml_root(self._request(ix, {"t": "caps"}))
            tag = root.tag.rsplit("}", 1)[-1]
            if tag == "error":
                raise ValueError(f"indexer error {root.get('code', '?')}")
            if tag != "caps":
                raise ValueError("not a Torznab endpoint")
        except Exception as ex:
            return {"ok": False, "error": describe_error(ex)}
        return {"ok": True, "error": ""}

    # ---- search -----------------------------------------------------------------------------
    def search(self, query: str, indexer_ids: list[str] | None = None, limit: int = 100) -> dict[str, Any]:
        query = query.strip()[:MAX_QUERY]
        if not query:
            raise ValueError("query required")
        limit = min(max(limit, 1), 200)
        with self.e.state.lock:
            chosen = [(i, dict(x)) for i, x in self.data()["indexers"].items() if not indexer_ids or i in indexer_ids]

        def one(ix: dict[str, Any]) -> list[dict[str, Any]]:
            res = parse_results(self._request(ix, {"t": "search", "q": query, "limit": str(limit)}))
            for r in res:
                r["indexer"] = ix["name"]
            return res

        results: list[dict[str, Any]] = []
        errors: dict[str, str] = {}
        if chosen:
            with ThreadPoolExecutor(max_workers=min(6, len(chosen))) as pool:
                futures = [(ix["name"], pool.submit(one, ix)) for _, ix in chosen]
                for name, fut in futures:
                    try:
                        results += fut.result()
                    except Exception as ex:
                        errors[name] = describe_error(ex)
        best: dict[str, dict[str, Any]] = {}  # same infohash from several indexers: keep the best seeded
        rest = []
        for r in results:
            if r["infohash"]:
                if r["infohash"] not in best or r["seeders"] > best[r["infohash"]]["seeders"]:
                    best[r["infohash"]] = r
            else:
                rest.append(r)
        merged = sorted([*best.values(), *rest], key=lambda r: (-r["seeders"], -r["published"]))[:limit]
        for r in merged:
            r["rid"] = self._remember(r.pop("url"), r["title"])
        return {"results": merged, "errors": errors}

    def _remember(self, url: str, title: str) -> str:
        rid = uuid.uuid4().hex[:12]
        now = time.time()
        with self._lock:
            for k in [k for k, v in self._cache.items() if now - v[0] > CACHE_TTL]:
                del self._cache[k]
            self._cache[rid] = (now, url, title)
            while len(self._cache) > CACHE_MAX:
                self._cache.popitem(last=False)
        return rid

    # ---- add --------------------------------------------------------------------------------
    def add_result(self, rid: str, category: str = "", paused: bool = False) -> str:
        from .qbapi import fetch_torrent

        with self._lock:
            entry = self._cache.get(rid)
        if not entry or time.time() - entry[0] > CACHE_TTL:
            raise KeyError(rid)
        url = entry[1]
        common = {"category": category, "paused": paused}
        if url.lower().startswith("magnet:"):
            return self.e.add(magnet=url, **common)
        kind, payload = fetch_torrent(url)
        return self.e.add(**({"magnet": payload} if kind == "magnet" else {"data": payload}), **common)
