"""qBittorrent WebUI API v2 handlers (dispatcher table)."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .engine import Engine, thash
from .settings import DEFAULTS

WEBAPI_VERSION = "2.9.3"
APP_VERSION = "v4.6.7"

READONLY_OK = {
    "app/version", "app/webapiVersion", "app/preferences", "app/buildInfo",
    "torrents/info", "torrents/properties", "torrents/files", "torrents/trackers",
    "torrents/categories", "torrents/tags", "torrents/count", "torrents/countFiles",
    "transfer/info", "sync/maindata", "sync/torrentPeers", "transfer/speedLimitsMode",
    "transfer/downloadLimit", "transfer/uploadLimit", "log/main", "auth/logout",
    "torrents/pieceStates", "torrents/webseeds",
}


class Params:
    def __init__(self, data: dict[str, str], files: list[tuple[str, bytes]]):
        self.d, self.files = data, files

    def s(self, k: str, default: str = "") -> str:
        return self.d.get(k, default)

    def b(self, k: str, default: bool = False) -> bool:
        v = self.d.get(k)
        return default if v is None else v.lower() in ("true", "1", "yes")

    def i(self, k: str, default: int = 0) -> int:
        try:
            return int(float(self.d.get(k, default)))
        except (TypeError, ValueError):
            return default

    def f(self, k: str, default: float = 0.0) -> float:
        try:
            return float(self.d.get(k, default))
        except (TypeError, ValueError):
            return default


class Response:
    def __init__(self, body: Any = "", status: int = 200, media: str | None = None):
        self.body, self.status, self.media = body, status, media


OK = Response("Ok.")
FAILS = Response("Fails.")
NOT_FOUND = Response("Not Found", 404)


class QbApi:
    def __init__(self, engine: Engine):
        self.e = engine
        self._rid = 0
        self._snaps: dict[int, dict[str, Any]] = {}

    # ---- dispatcher ----------------------------------------------------------------------
    def call(self, route: str, p: Params) -> Response:
        fn = getattr(self, "h_" + route.replace("/", "_"), None)
        if not fn:
            return NOT_FOUND
        return fn(p)

    # ---- app ------------------------------------------------------------------------------
    def h_app_version(self, p: Params) -> Response:
        return Response(APP_VERSION)

    def h_app_webapiVersion(self, p: Params) -> Response:
        return Response(WEBAPI_VERSION)

    def h_app_buildInfo(self, p: Params) -> Response:
        import libtorrent as lt
        return Response({"qt": "6", "libtorrent": lt.__version__, "boost": "1", "openssl": "3",
                         "bitness": 64}, media="json")

    def h_app_shutdown(self, p: Params) -> Response:
        return OK

    def h_app_defaultSavePath(self, p: Params) -> Response:
        return Response(self.e.settings.get("save_path"))

    def h_app_preferences(self, p: Params) -> Response:
        d = self.e.settings.snapshot()
        d.pop("proxy_password", None)
        d["max_ratio"] = d["max_ratio"] if d["max_ratio_enabled"] else -1
        d["max_seeding_time"] = d["max_seeding_time"] if d["max_seeding_time_enabled"] else -1
        d["create_subfolder_enabled"] = True
        d["add_trackers_enabled"] = bool(d["nova_extra_trackers_enabled"])
        d["add_trackers"] = d["nova_extra_trackers"]
        return Response(d, media="json")

    def h_app_setPreferences(self, p: Params) -> Response:
        try:
            values = json.loads(p.s("json", "{}"))
        except ValueError:
            return Response("Bad Request", 400)
        if not isinstance(values, dict):
            return Response("Bad Request", 400)
        values = {k: v for k, v in values.items() if k in DEFAULTS and k != "web_ui_port"}
        for k, v in list(values.items()):
            if type(v) is not type(DEFAULTS[k]) and not (isinstance(DEFAULTS[k], float)
                                                         and isinstance(v, int)):
                if isinstance(DEFAULTS[k], bool) and isinstance(v, int):
                    values[k] = bool(v)
                elif isinstance(DEFAULTS[k], int) and isinstance(v, float):
                    values[k] = int(v)
                else:
                    del values[k]
        self.e.settings.update(values)
        self.e.apply_settings()
        return OK

    # ---- transfer -------------------------------------------------------------------------
    def h_transfer_info(self, p: Params) -> Response:
        s = self.e.server_state()
        return Response({k: s[k] for k in ("connection_status", "dht_nodes", "dl_info_data",
                         "dl_info_speed", "dl_rate_limit", "up_info_data", "up_info_speed",
                         "up_rate_limit")}, media="json")

    def h_transfer_speedLimitsMode(self, p: Params) -> Response:
        return Response("1" if self.e.settings.get("alt_speed_active") else "0")

    def h_transfer_toggleSpeedLimitsMode(self, p: Params) -> Response:
        self.e.settings.update({"alt_speed_active": not self.e.settings.get("alt_speed_active")})
        self.e.apply_settings()
        return OK

    def h_transfer_downloadLimit(self, p: Params) -> Response:
        return Response(str(self.e.settings.get("dl_limit") * 1024))

    def h_transfer_uploadLimit(self, p: Params) -> Response:
        return Response(str(self.e.settings.get("up_limit") * 1024))

    def h_transfer_setDownloadLimit(self, p: Params) -> Response:
        self.e.settings.update({"dl_limit": max(p.i("limit") // 1024, 0)})
        self.e.apply_settings()
        return OK

    def h_transfer_setUploadLimit(self, p: Params) -> Response:
        self.e.settings.update({"up_limit": max(p.i("limit") // 1024, 0)})
        self.e.apply_settings()
        return OK

    # ---- torrents: queries ------------------------------------------------------------------
    def _hashes(self, p: Params) -> list[str]:
        return self.e.hashes(p.s("hashes"))

    def h_torrents_info(self, p: Params) -> Response:
        hashes = self.e.hashes(p.s("hashes")) if p.s("hashes") else None
        cat = p.d.get("category")
        tag = p.d.get("tag")
        items = self.e.list_info(hashes=hashes, category=cat, tag=tag, filter_=p.s("filter", "all"))
        key = p.s("sort")
        if key and items and key in items[0]:
            items.sort(key=lambda x: x[key], reverse=p.b("reverse"))
        off, lim = p.i("offset"), p.i("limit", -1)
        if off:
            items = items[off:]
        if lim > 0:
            items = items[:lim]
        return Response(items, media="json")

    def h_torrents_count(self, p: Params) -> Response:
        return Response(str(len(self.e.statuses())))

    def h_torrents_properties(self, p: Params) -> Response:
        h = p.s("hash").lower()
        s = self.e.statuses().get(h)
        if not s:
            return NOT_FOUND
        i = self.e.info(s)
        ti = s.handle.torrent_file() if s.has_metadata else None
        return Response({
            "save_path": i["save_path"], "creation_date": ti.creation_date() if ti else -1,
            "piece_size": ti.piece_length() if ti else -1, "comment": ti.comment() if ti else "",
            "total_wasted": s.total_failed_bytes + s.total_redundant_bytes,
            "total_uploaded": i["uploaded"], "total_uploaded_session": i["uploaded_session"],
            "total_downloaded": i["downloaded"], "total_downloaded_session": i["downloaded_session"],
            "up_limit": i["up_limit"], "dl_limit": i["dl_limit"], "time_elapsed": i["time_active"],
            "seeding_time": i["seeding_time"], "nb_connections": s.num_connections,
            "nb_connections_limit": 100, "share_ratio": i["ratio"], "addition_date": i["added_on"],
            "completion_date": i["completion_on"], "created_by": ti.creator() if ti else "",
            "dl_speed_avg": 0, "dl_speed": i["dlspeed"], "eta": i["eta"], "last_seen": i["last_activity"],
            "peers": s.num_peers - s.num_seeds, "peers_total": s.list_peers - s.list_seeds,
            "pieces_have": s.num_pieces, "pieces_num": ti.num_pieces() if ti else 0,
            "reannounce": 0, "seeds": s.num_seeds, "seeds_total": s.list_seeds,
            "total_size": i["total_size"], "up_speed": i["upspeed"], "up_speed_avg": 0,
            "isPrivate": bool(ti.priv()) if ti else False, "hash": h, "name": i["name"],
        }, media="json")

    def h_torrents_files(self, p: Params) -> Response:
        r = self.e.files(p.s("hash").lower())
        return NOT_FOUND if r is None else Response(r, media="json")

    def h_torrents_trackers(self, p: Params) -> Response:
        r = self.e.trackers(p.s("hash").lower())
        return NOT_FOUND if r is None else Response(r, media="json")

    def h_torrents_categories(self, p: Params) -> Response:
        cats = self.e.state.get("categories", {})
        return Response({n: {"name": n, "savePath": c.get("savePath", "")} for n, c in cats.items()},
                        media="json")

    def h_torrents_tags(self, p: Params) -> Response:
        return Response(sorted(self.e.state.get("tags", [])), media="json")

    # ---- torrents: add / delete / control ---------------------------------------------------
    def h_torrents_add(self, p: Params) -> Response:
        tags = [t.strip() for t in p.s("tags").split(",") if t.strip()]
        common = dict(
            save_path=p.s("savepath") or None, category=p.s("category"), tags=tags,
            paused=p.b("paused") or p.b("stopped"), sequential=p.b("sequentialDownload"),
            first_last=p.b("firstLastPiecePrio"), up_limit=p.i("upLimit", -1),
            dl_limit=p.i("dlLimit", -1), ratio_limit=p.f("ratioLimit", -2),
            seeding_time_limit=p.i("seedingTimeLimit", -2))
        if common["category"] and common["category"] not in self.e.state.get("categories", {}):
            with self.e.state.lock:
                self.e.state.data["categories"][common["category"]] = {"savePath": ""}
            self.e.state.save()
        added = 0
        for url in [u.strip() for u in p.s("urls").splitlines() if u.strip()]:
            try:
                if url.lower().startswith("magnet:"):
                    self.e.add(magnet=url, **common)
                elif re.match(r"^https?://", url, re.I):
                    kind, payload = fetch_torrent(url)
                    self.e.add(**({"magnet": payload} if kind == "magnet" else {"data": payload}),
                               **common)
                else:
                    continue
                added += 1
            except Exception as ex:
                self.e.add_log(f"Add from URL failed: {type(ex).__name__}", 4)
        for _, data in p.files:
            try:
                self.e.add(data=data, **common)
                added += 1
            except Exception as ex:
                self.e.add_log(f"Add .torrent failed: {type(ex).__name__}: {ex}", 4)
        return OK if added else FAILS

    def h_torrents_delete(self, p: Params) -> Response:
        for h in self._hashes(p):
            self.e.remove(h, p.b("deleteFiles"))
        return OK

    def _each(self, p: Params, fn) -> Response:
        for h in self._hashes(p):
            fn(h)
        return OK

    def h_torrents_pause(self, p: Params) -> Response:
        return self._each(p, self.e.pause)

    h_torrents_stop = h_torrents_pause

    def h_torrents_resume(self, p: Params) -> Response:
        return self._each(p, self.e.resume)

    h_torrents_start = h_torrents_resume

    def h_torrents_setForceStart(self, p: Params) -> Response:
        on = p.b("value")
        return self._each(p, lambda h: self.e.resume(h, force=True) if on else self.e.resume(h))

    def h_torrents_recheck(self, p: Params) -> Response:
        return self._each(p, self.e.recheck)

    def h_torrents_reannounce(self, p: Params) -> Response:
        return self._each(p, self.e.reannounce)

    def h_torrents_topPrio(self, p: Params) -> Response:
        return self._each(p, lambda h: self.e.queue_move(h, "top"))

    def h_torrents_bottomPrio(self, p: Params) -> Response:
        return self._each(p, lambda h: self.e.queue_move(h, "bottom"))

    def h_torrents_increasePrio(self, p: Params) -> Response:
        return self._each(p, lambda h: self.e.queue_move(h, "up"))

    def h_torrents_decreasePrio(self, p: Params) -> Response:
        return self._each(p, lambda h: self.e.queue_move(h, "down"))

    def h_torrents_setLocation(self, p: Params) -> Response:
        loc = p.s("location")
        if not loc:
            return Response("Bad Request", 400)
        return self._each(p, lambda h: self.e.set_location(h, loc))

    def h_torrents_filePrio(self, p: Params) -> Response:
        ids = [int(x) for x in p.s("id").split("|") if x.isdigit()]
        h = p.s("hash").lower()
        if not self.e.handle(h):
            return NOT_FOUND
        self.e.set_file_priority(h, ids, p.i("priority"))
        return OK

    def h_torrents_setSequentialDownload(self, p: Params) -> Response:
        return self._each(p, lambda h: self._toggle_flag(h, "sequential_download"))

    def _toggle_flag(self, h: str, name: str) -> None:
        import libtorrent as lt
        handle = self.e.handle(h)
        if handle:
            flag = getattr(lt.torrent_flags, name)
            if handle.flags() & flag:
                handle.unset_flags(flag)
            else:
                handle.set_flags(flag)

    def h_torrents_toggleSequentialDownload(self, p: Params) -> Response:
        return self.h_torrents_setSequentialDownload(p)

    def h_torrents_setDownloadLimit(self, p: Params) -> Response:
        return self._each(p, lambda h: self._limit(h, "download", p.i("limit")))

    def h_torrents_setUploadLimit(self, p: Params) -> Response:
        return self._each(p, lambda h: self._limit(h, "upload", p.i("limit")))

    def _limit(self, h: str, kind: str, v: int) -> None:
        handle = self.e.handle(h)
        if handle:
            getattr(handle, f"set_{kind}_limit")(v if v > 0 else 0)

    def h_torrents_setShareLimits(self, p: Params) -> Response:
        def apply(h: str) -> None:
            m = self.e.meta(h)
            m["ratio_limit"] = p.f("ratioLimit", -2)
            m["seeding_time_limit"] = p.i("seedingTimeLimit", -2)
            m["inactive_seeding_time_limit"] = p.i("inactiveSeedingTimeLimit", -2)
        r = self._each(p, apply)
        self.e.state.save()
        return r

    # ---- categories & tags ------------------------------------------------------------------
    def h_torrents_createCategory(self, p: Params) -> Response:
        name = p.s("category")
        if not name or not re.match(r"^[^\\/:*?\"<>|]+$", name):
            return Response("Bad Request", 400)
        with self.e.state.lock:
            cats = self.e.state.data["categories"]
            if name in cats:
                return Response("Conflict", 409)
            cats[name] = {"savePath": p.s("savePath")}
        self.e.state.save()
        return OK

    def h_torrents_editCategory(self, p: Params) -> Response:
        name = p.s("category")
        with self.e.state.lock:
            if name not in self.e.state.data["categories"]:
                return Response("Conflict", 409)
            self.e.state.data["categories"][name]["savePath"] = p.s("savePath")
        self.e.state.save()
        return OK

    def h_torrents_removeCategories(self, p: Params) -> Response:
        with self.e.state.lock:
            for n in p.s("categories").split("\n"):
                self.e.state.data["categories"].pop(n.strip(), None)
                for m in self.e.state.data["torrents"].values():
                    if m.get("category") == n.strip():
                        m["category"] = ""
        self.e.state.save()
        return OK

    def h_torrents_setCategory(self, p: Params) -> Response:
        cat = p.s("category")
        if cat and cat not in self.e.state.get("categories", {}):
            return Response("Conflict", 409)
        for h in self._hashes(p):
            self.e.meta(h)["category"] = cat
        self.e.state.save()
        return OK

    def h_torrents_createTags(self, p: Params) -> Response:
        with self.e.state.lock:
            tags = set(self.e.state.data["tags"])
            tags.update(t.strip() for t in p.s("tags").split(",") if t.strip())
            self.e.state.data["tags"] = sorted(tags)
        self.e.state.save()
        return OK

    def h_torrents_deleteTags(self, p: Params) -> Response:
        gone = {t.strip() for t in p.s("tags").split(",")}
        with self.e.state.lock:
            self.e.state.data["tags"] = [t for t in self.e.state.data["tags"] if t not in gone]
            for m in self.e.state.data["torrents"].values():
                m["tags"] = [t for t in m.get("tags", []) if t not in gone]
        self.e.state.save()
        return OK

    def h_torrents_addTags(self, p: Params) -> Response:
        new = [t.strip() for t in p.s("tags").split(",") if t.strip()]
        for h in self._hashes(p):
            m = self.e.meta(h)
            m["tags"] = sorted(set(m.get("tags", [])) | set(new))
        with self.e.state.lock:
            self.e.state.data["tags"] = sorted(set(self.e.state.data["tags"]) | set(new))
        self.e.state.save()
        return OK

    def h_torrents_removeTags(self, p: Params) -> Response:
        gone = {t.strip() for t in p.s("tags").split(",") if t.strip()}
        for h in self._hashes(p):
            m = self.e.meta(h)
            m["tags"] = [t for t in m.get("tags", []) if gone and t not in gone] if gone else []
        self.e.state.save()
        return OK

    # ---- trackers ---------------------------------------------------------------------------
    def h_torrents_addTrackers(self, p: Params) -> Response:
        h = p.s("hash").lower()
        if not self.e.handle(h):
            return NOT_FOUND
        self.e.add_trackers(h, [u.strip() for u in p.s("urls").split("\n") if u.strip()])
        return OK

    def h_torrents_removeTrackers(self, p: Params) -> Response:
        h = p.s("hash").lower()
        if not self.e.handle(h):
            return NOT_FOUND
        self.e.remove_trackers(h, [u.strip() for u in p.s("urls").split("|") if u.strip()])
        return OK

    def h_torrents_editTracker(self, p: Params) -> Response:
        h = p.s("hash").lower()
        if not self.e.handle(h):
            return NOT_FOUND
        self.e.edit_tracker(h, p.s("origUrl"), p.s("newUrl"))
        return OK

    # ---- sync -------------------------------------------------------------------------------
    def _snapshot(self) -> dict[str, Any]:
        return {
            "torrents": {i["hash"]: i for i in self.e.list_info()},
            "categories": {n: {"name": n, "savePath": c.get("savePath", "")}
                           for n, c in self.e.state.get("categories", {}).items()},
            "tags": sorted(self.e.state.get("tags", [])),
            "server_state": self.e.server_state(),
        }

    def h_sync_maindata(self, p: Params) -> Response:
        rid = p.i("rid")
        cur = self._snapshot()
        self._rid += 1
        new_rid = self._rid
        prev = self._snaps.get(rid) if rid else None
        self._snaps[new_rid] = cur
        for old in [k for k in self._snaps if k < new_rid - 20]:
            del self._snaps[old]
        if prev is None:
            return Response({"rid": new_rid, "full_update": True, **cur}, media="json")
        out: dict[str, Any] = {"rid": new_rid}
        t = {}
        for h, info in cur["torrents"].items():
            old = prev["torrents"].get(h)
            diff = info if old is None else {k: v for k, v in info.items() if old.get(k) != v}
            if diff:
                t[h] = diff
        if t:
            out["torrents"] = t
        gone = [h for h in prev["torrents"] if h not in cur["torrents"]]
        if gone:
            out["torrents_removed"] = gone
        cats = {n: c for n, c in cur["categories"].items() if prev["categories"].get(n) != c}
        if cats:
            out["categories"] = cats
        cgone = [n for n in prev["categories"] if n not in cur["categories"]]
        if cgone:
            out["categories_removed"] = cgone
        tags = [x for x in cur["tags"] if x not in prev["tags"]]
        if tags:
            out["tags"] = tags
        tgone = [x for x in prev["tags"] if x not in cur["tags"]]
        if tgone:
            out["tags_removed"] = tgone
        ss = {k: v for k, v in cur["server_state"].items() if prev["server_state"].get(k) != v}
        if ss:
            out["server_state"] = ss
        return Response(out, media="json")

    def h_sync_torrentPeers(self, p: Params) -> Response:
        return Response({"rid": p.i("rid") + 1, "full_update": True, "peers": self.e.peers(
            p.s("hash").lower())}, media="json")

    # ---- log --------------------------------------------------------------------------------
    def h_log_main(self, p: Params) -> Response:
        last = p.i("last_known_id", -1)
        return Response([x for x in self.e.logs if x["id"] > last], media="json")


def fetch_torrent(url: str) -> tuple[str, Any]:
    """Download a .torrent from http(s). Prowlarr may redirect to a magnet link."""
    with httpx.Client(follow_redirects=False, timeout=30) as c:
        for _ in range(5):
            r = c.get(url, headers={"User-Agent": "NovaTorrent"})
            if r.is_redirect:
                loc = r.headers.get("location", "")
                if loc.lower().startswith("magnet:"):
                    return "magnet", loc
                url = str(r.url.join(loc))
                continue
            r.raise_for_status()
            if len(r.content) > 20 * 1024 * 1024:
                raise ValueError("torrent too large")
            return "data", r.content
    raise ValueError("too many redirects")


_ = thash  # re-exported for tests
