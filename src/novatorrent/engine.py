"""libtorrent session wrapper: add/remove, resume data, alert loop, qBittorrent-style data."""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

import libtorrent as lt

from .settings import Settings, State, atomic_write

log = logging.getLogger("novatorrent.engine")

HASH_RE = re.compile(r"^[0-9a-fA-F]{40}$")


def secs(v: Any) -> int:
    """libtorrent durations are ints or timedeltas depending on the binding version."""
    if hasattr(v, "total_seconds"):
        return int(v.total_seconds())
    return int(v or 0)


def thash(h: Any) -> str:
    ih = h.info_hashes()
    return str(ih.v1 if ih.has_v1() else ih.v2)[:40]


def map_priority_to_lt(p: int) -> int:
    return {0: 0, 1: 4, 6: 6, 7: 7}.get(int(p), 4)


def map_priority_from_lt(p: int) -> int:
    return 0 if p == 0 else 7 if p >= 7 else 6 if p >= 5 else 1


class Engine:
    def __init__(self, config_dir: Path, settings: Settings, state: State):
        self.config_dir = config_dir
        self.settings = settings
        self.state = state
        self.resume_dir = config_dir / "resume"
        self.resume_dir.mkdir(parents=True, exist_ok=True)
        self.ses: lt.session | None = None
        self.logs: deque[dict[str, Any]] = deque(maxlen=1000)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pending_saves = 0
        self._lock = threading.RLock()
        self._status_cache: tuple[float, dict[str, Any]] = (0.0, {})
        self.on_event = lambda name, info: None  # set by app: notifications/hooks
        self.session_started = time.time()

    # ---- lifecycle -------------------------------------------------------------------------
    def start(self) -> None:
        from .netconf import build_settings_pack, session_extensions

        cfg = self.settings.snapshot()
        self.ses = lt.session(build_settings_pack(cfg), 0)  # 0: no default plugins (PEX is opt-in)
        session_extensions(self.ses, cfg)
        self._load_resume()
        self._thread = threading.Thread(target=self._alert_loop, name="alerts", daemon=True)
        self._thread.start()
        self.add_log(f"NovaTorrent engine started (libtorrent {lt.__version__})")

    def stop(self, timeout: float = 30.0) -> None:
        if not self.ses:
            return
        self.save_all_resume(wait=timeout)
        self._stop.set()
        if self._thread:
            self._thread.join(5)
        self.ses.pause()
        self.ses = None

    def apply_settings(self) -> None:
        from .netconf import build_settings_pack

        if self.ses:
            self.ses.apply_settings(build_settings_pack(self.settings.snapshot(), partial=True))

    # ---- logging ---------------------------------------------------------------------------
    def add_log(self, message: str, level: int = 1) -> None:
        # level follows qBittorrent: 1 normal, 2 info, 4 warning, 8 critical
        self.logs.append(
            {"id": len(self.logs), "message": message, "timestamp": int(time.time() * 1000), "type": level}
        )

    # ---- resume data -----------------------------------------------------------------------
    def _load_resume(self) -> None:
        for f in sorted(self.resume_dir.glob("*.fastresume")):
            try:
                params = lt.read_resume_data(f.read_bytes())
                self.ses.add_torrent(params)
            except Exception as e:  # corrupt resume file: keep it, report, continue
                self.add_log(f"Could not load resume data {f.name}: {e}", 4)
                try:
                    os.replace(f, f.with_suffix(".corrupt"))
                except OSError:
                    pass

    def save_all_resume(self, wait: float = 0.0) -> None:
        if not self.ses:
            return
        n = 0
        for h in self.ses.get_torrents():
            if h.is_valid() and h.status().has_metadata:
                try:
                    h.save_resume_data(lt.torrent_handle.save_info_dict)
                    n += 1
                except Exception as e:
                    self.add_log(f"save_resume_data failed: {e}", 4)
        with self._lock:
            self._pending_saves += n
        deadline = time.time() + wait
        while wait and time.time() < deadline:
            with self._lock:
                if self._pending_saves <= 0:
                    break
            # when the alert thread already stopped we drain alerts ourselves
            if self._thread is None or not self._thread.is_alive():
                self._handle_alerts(self.ses.pop_alerts())
            time.sleep(0.05)

    def _alert_loop(self) -> None:
        last_save = time.time()
        while not self._stop.is_set():
            try:
                if self.ses.wait_for_alert(500):
                    self._handle_alerts(self.ses.pop_alerts())
                if time.time() - last_save > 60:
                    last_save = time.time()
                    self._save_dirty()
            except Exception as e:  # keep the loop alive
                self.add_log(f"alert loop error: {e}", 8)

    def _save_dirty(self) -> None:
        n = 0
        for h in self.ses.get_torrents():
            if h.is_valid() and h.need_save_resume_data():
                h.save_resume_data(lt.torrent_handle.save_info_dict)
                n += 1
        with self._lock:
            self._pending_saves += n

    def _handle_alerts(self, alerts: list[Any]) -> None:
        for a in alerts:
            try:
                self._handle_alert(a)
            except Exception as e:
                self.add_log(f"alert handler error: {e}", 8)

    def _handle_alert(self, a: Any) -> None:
        if isinstance(a, lt.save_resume_data_alert):
            h = thash(a.handle)
            buf = lt.write_resume_data_buf(a.params)
            atomic_write(self.resume_dir / f"{h}.fastresume", buf)
            with self._lock:
                self._pending_saves -= 1
        elif isinstance(a, lt.save_resume_data_failed_alert):
            with self._lock:
                self._pending_saves -= 1
        elif isinstance(a, lt.torrent_finished_alert):
            h = thash(a.handle)
            meta = self.meta(h)
            if not meta.get("completion_on"):
                meta["completion_on"] = int(time.time())
                self._save_meta()
                self._on_complete(a.handle, h)
            a.handle.save_resume_data(lt.torrent_handle.save_info_dict)
            with self._lock:
                self._pending_saves += 1
        elif isinstance(a, lt.metadata_received_alert):
            a.handle.save_resume_data(lt.torrent_handle.save_info_dict)
            with self._lock:
                self._pending_saves += 1
        elif isinstance(a, (lt.torrent_error_alert, lt.file_error_alert)):
            self.add_log(a.message(), 8)
            self.on_event("error", {"hash": thash(a.handle), "message": a.message()})
        elif isinstance(a, lt.add_torrent_alert) and a.error.value():
            self.add_log(f"Add failed: {a.error.message()}", 8)
        elif isinstance(a, (lt.listen_succeeded_alert, lt.listen_failed_alert)):
            self.add_log(a.message(), 2 if isinstance(a, lt.listen_succeeded_alert) else 4)

    def _on_complete(self, handle: Any, h: str) -> None:
        final = self.meta(h).get("final_path")
        if final:  # "move on complete": same-volume moves are atomic renames
            try:
                Path(final).mkdir(parents=True, exist_ok=True)
                handle.move_storage(final)
            except Exception as e:
                self.add_log(f"move on complete failed: {e}", 8)
        name = handle.status().name
        self.add_log(f"Torrent finished: {name}", 2)
        self.on_event("completed", {"hash": h, "name": name})

    # ---- metadata (category, tags, limits ...) ---------------------------------------------
    def meta(self, h: str) -> dict[str, Any]:
        with self.state.lock:
            return self.state.data["torrents"].setdefault(h, {})

    def _save_meta(self) -> None:
        self.state.save()

    # ---- adding / removing ------------------------------------------------------------------
    def resolve_save_path(self, category: str, explicit: str | None) -> str:
        if explicit:
            return explicit
        cat = self.state.get("categories", {}).get(category or "", {})
        return cat.get("savePath") or self.settings.get("save_path")

    def add(
        self,
        *,
        magnet: str | None = None,
        data: bytes | None = None,
        save_path: str | None = None,
        category: str = "",
        tags: list[str] | None = None,
        paused: bool = False,
        sequential: bool = False,
        first_last: bool = False,
        up_limit: int = -1,
        dl_limit: int = -1,
        ratio_limit: float = -2,
        seeding_time_limit: int = -2,
    ) -> str:
        if magnet:
            params = lt.parse_magnet_uri(magnet)
        elif data:
            params = lt.add_torrent_params()
            params.ti = lt.torrent_info(lt.bdecode(data))
        else:
            raise ValueError("nothing to add")
        ih = params.ti.info_hashes() if data and not magnet else params.info_hashes
        h = str(ih.v1 if ih.has_v1() else ih.v2)[:40]
        if self._find(h):
            return h  # duplicates are accepted silently, like qBittorrent
        final_path = self.resolve_save_path(category, save_path)
        params.save_path = final_path
        use_temp = bool(self.settings.get("temp_path_enabled") and self.settings.get("temp_path"))
        if use_temp:
            params.save_path = self.settings.get("temp_path")
        Path(params.save_path).mkdir(parents=True, exist_ok=True)
        flags = params.flags
        flags &= ~lt.torrent_flags.duplicate_is_error
        flags |= lt.torrent_flags.auto_managed
        if paused or self.settings.get("start_paused_enabled"):
            flags |= lt.torrent_flags.paused
            flags &= ~lt.torrent_flags.auto_managed
        if sequential or self.settings.get("nova_sequential_default"):
            flags |= lt.torrent_flags.sequential_download
        params.flags = flags
        if self.settings.get("preallocate_all"):
            params.storage_mode = lt.storage_mode_t.storage_mode_allocate
        else:
            params.storage_mode = lt.storage_mode_t.storage_mode_sparse
        if up_limit and up_limit > 0:
            params.upload_limit = up_limit
        if dl_limit and dl_limit > 0:
            params.download_limit = dl_limit
        if self.settings.get("nova_extra_trackers_enabled"):
            extra = [t.strip() for t in self.settings.get("nova_extra_trackers").split() if t.strip()]
            params.trackers = list(params.trackers) + [t for t in extra if t not in params.trackers]
        self.ses.add_torrent(params)
        self.invalidate()
        m = self.meta(h)
        m.update(
            {
                "category": category or "",
                "tags": sorted(set(tags or [])),
                "added_on": int(time.time()),
                "completion_on": 0,
                "ratio_limit": ratio_limit,
                "seeding_time_limit": seeding_time_limit,
                "inactive_seeding_time_limit": -2,
                "first_last": bool(first_last),
                "seen_complete": 0,
                "final_path": final_path if use_temp else "",
            }
        )
        self._save_meta()
        self.add_log(f"Torrent added: {h}", 2)
        self.on_event("added", {"hash": h, "name": h})
        return h

    def invalidate(self) -> None:
        self._status_cache = (0.0, {})

    def _find(self, h: str) -> Any | None:
        if not self.ses or not HASH_RE.match(h):
            return None
        try:
            ih = lt.sha1_hash(bytes.fromhex(h))
            handle = self.ses.find_torrent(ih)
        except Exception:
            return None
        return handle if handle.is_valid() else None

    def handle(self, h: str) -> Any | None:
        return self._find(h.lower())

    def hashes(self, spec: str) -> list[str]:
        if spec == "all":
            return [thash(x) for x in self.ses.get_torrents()]
        return [x.lower() for x in spec.split("|") if HASH_RE.match(x)]

    def remove(self, h: str, delete_files: bool = False) -> bool:
        handle = self.handle(h)
        if not handle:
            return False
        self.ses.remove_torrent(handle, lt.session.delete_files if delete_files else 0)
        self.invalidate()
        for ext in ("fastresume", "corrupt"):
            p = self.resume_dir / f"{h}.{ext}"
            if p.exists():
                p.unlink()  # our own resume bookkeeping file, not user data
        with self.state.lock:
            self.state.data["torrents"].pop(h, None)
        self._save_meta()
        self.on_event("removed", {"hash": h})
        return True

    # ---- control ----------------------------------------------------------------------------
    def pause(self, h: str) -> None:
        handle = self.handle(h)
        self.invalidate()
        if handle:
            handle.unset_flags(lt.torrent_flags.auto_managed)
            handle.pause()
            self.meta(h)["force_start"] = False

    def resume(self, h: str, force: bool = False) -> None:
        handle = self.handle(h)
        self.invalidate()
        if not handle:
            return
        if force:
            handle.unset_flags(lt.torrent_flags.auto_managed)
            handle.resume()
            self.meta(h)["force_start"] = True
        else:
            handle.set_flags(lt.torrent_flags.auto_managed)
            handle.resume()
            self.meta(h)["force_start"] = False

    def recheck(self, h: str) -> None:
        handle = self.handle(h)
        self.invalidate()
        if handle:
            handle.force_recheck()

    def reannounce(self, h: str) -> None:
        handle = self.handle(h)
        if handle:
            handle.force_reannounce(0, -1)

    def queue_move(self, h: str, how: str) -> None:
        handle = self.handle(h)
        if not handle:
            return
        {
            "top": handle.queue_position_top,
            "bottom": handle.queue_position_bottom,
            "up": handle.queue_position_up,
            "down": handle.queue_position_down,
        }[how]()

    def set_location(self, h: str, path: str) -> None:
        handle = self.handle(h)
        if handle:
            Path(path).mkdir(parents=True, exist_ok=True)
            handle.move_storage(path)

    def set_file_priority(self, h: str, ids: list[int], prio: int) -> None:
        handle = self.handle(h)
        if handle:
            for i in ids:
                handle.file_priority(i, map_priority_to_lt(prio))

    # ---- queries ----------------------------------------------------------------------------
    def statuses(self, max_age: float = 0.8) -> dict[str, Any]:
        ts, cache = self._status_cache
        if time.time() - ts < max_age:
            return cache
        # NOTE: ses.get_torrent_status(pred) with a Python predicate crashes (access violation)
        # when called concurrently with the alert thread, so poll handles one by one.
        sts = [h.status() for h in self.ses.get_torrents() if h.is_valid()]
        cache = {thash(s.handle): s for s in sts}
        self._status_cache = (time.time(), cache)
        return cache

    def qb_state(self, s: Any, m: dict[str, Any]) -> str:
        flags = s.flags
        paused = bool(flags & lt.torrent_flags.paused)
        auto = bool(flags & lt.torrent_flags.auto_managed)
        complete = s.progress >= 1.0 or s.state in (lt.torrent_status.seeding, lt.torrent_status.finished)
        forced = bool(m.get("force_start"))
        if s.errc.value():
            return "error"
        if paused and not auto:
            return "pausedUP" if complete else "pausedDL"
        if paused and auto:
            return "queuedUP" if complete else "queuedDL"
        st = s.state
        if st == lt.torrent_status.checking_files:
            return "checkingUP" if complete else "checkingDL"
        if st == lt.torrent_status.checking_resume_data:
            return "checkingResumeData"
        if st == lt.torrent_status.downloading_metadata:
            return "forcedMetaDL" if forced else "metaDL"
        if st == lt.torrent_status.allocating:
            return "allocating"
        if complete:
            if forced:
                return "forcedUP"
            return "uploading" if s.upload_payload_rate > 0 else "stalledUP"
        if forced:
            return "forcedDL"
        return "downloading" if s.download_payload_rate > 0 else "stalledDL"

    def content_path(self, s: Any) -> str:
        try:
            ti = s.handle.torrent_file()
        except Exception:
            ti = None
        base = s.save_path
        if ti is None:
            return base
        files = ti.files()
        if files.num_files() == 1:
            return os.path.join(base, files.file_path(0))
        return os.path.join(base, files.name() if hasattr(files, "name") else s.name)

    def info(self, s: Any) -> dict[str, Any]:
        h = thash(s.handle)
        m = self.meta(h)
        size = s.total_wanted
        total = s.total
        dl_all = s.all_time_download
        ul_all = s.all_time_upload
        ratio = ul_all / dl_all if dl_all else (ul_all / s.total_done if s.total_done else 0.0)
        ds, us = s.download_payload_rate, s.upload_payload_rate
        left = max(size - s.total_wanted_done, 0)
        eta = int(left / ds) if ds > 0 and left else 8640000
        trackers = s.current_tracker or ""
        try:
            first = s.handle.trackers()
            trackers = trackers or (first[0]["url"] if first else "")
        except Exception:
            pass
        state = self.qb_state(s, m)
        flags = s.flags
        return {
            "hash": h,
            "infohash_v1": h,
            "infohash_v2": "",
            "name": s.name or h,
            "magnet_uri": lt.make_magnet_uri(s.handle) if s.handle.is_valid() else "",
            "size": size,
            "total_size": total,
            "progress": round(s.progress, 6),
            "dlspeed": ds,
            "upspeed": us,
            "eta": eta,
            "state": state,
            "category": m.get("category", ""),
            "tags": ", ".join(m.get("tags", [])),
            "save_path": s.save_path,
            "download_path": "",
            "content_path": self.content_path(s),
            "ratio": round(ratio, 4),
            "ratio_limit": m.get("ratio_limit", -2),
            "seeding_time": secs(s.seeding_duration),
            "seeding_time_limit": m.get("seeding_time_limit", -2),
            "inactive_seeding_time_limit": m.get("inactive_seeding_time_limit", -2),
            "time_active": secs(s.active_duration),
            "added_on": m.get("added_on", 0),
            "completion_on": m.get("completion_on", 0) or -1,
            "last_activity": int(time.time()) - max(secs(s.time_since_upload), 0),
            "seen_complete": m.get("seen_complete", 0),
            "amount_left": left,
            "completed": s.total_wanted_done,
            "downloaded": dl_all,
            "uploaded": ul_all,
            "downloaded_session": s.total_payload_download,
            "uploaded_session": s.total_payload_upload,
            "num_seeds": s.num_seeds,
            "num_leechs": s.num_peers - s.num_seeds,
            "num_complete": s.list_seeds,
            "num_incomplete": s.list_peers - s.list_seeds,
            "priority": s.queue_position + 1 if s.queue_position >= 0 else 0,
            "tracker": trackers,
            "trackers_count": 0,
            "dl_limit": s.handle.download_limit() or -1,
            "up_limit": s.handle.upload_limit() or -1,
            "seq_dl": bool(flags & lt.torrent_flags.sequential_download),
            "f_l_piece_prio": bool(m.get("first_last")),
            "force_start": bool(m.get("force_start")),
            "auto_tmm": False,
            "super_seeding": bool(flags & lt.torrent_flags.super_seeding),
            "availability": round(s.distributed_copies, 3),
            "max_ratio": -1,
            "max_seeding_time": -1,
            "reannounce": 0,
        }

    def list_info(
        self,
        *,
        hashes: list[str] | None = None,
        category: str | None = None,
        tag: str | None = None,
        filter_: str = "all",
    ) -> list[dict[str, Any]]:
        out = []
        sts = self.statuses()
        for h, s in sts.items():
            if hashes is not None and h not in hashes:
                continue
            if not s.handle.is_valid():
                continue
            info = self.info(s)
            if category is not None and info["category"] != category:
                continue
            if tag is not None:
                tags = [t.strip() for t in info["tags"].split(",") if t.strip()]
                if (tag == "" and tags) or (tag != "" and tag not in tags):
                    continue
            if not _matches_filter(info, filter_):
                continue
            out.append(info)
        return out

    def files(self, h: str) -> list[dict[str, Any]] | None:
        handle = self.handle(h)
        if not handle:
            return None
        st = handle.status()
        if not st.has_metadata:
            return []
        ti = handle.torrent_file()
        fs = ti.files()
        prog = handle.file_progress()
        prios = handle.get_file_priorities()
        out = []
        for i in range(fs.num_files()):
            size = fs.file_size(i)
            out.append(
                {
                    "index": i,
                    "name": fs.file_path(i).replace("\\", "/"),
                    "size": size,
                    "progress": (prog[i] / size) if size else 1.0,
                    "priority": map_priority_from_lt(prios[i]),
                    "is_seed": st.is_seeding,
                    "piece_range": [0, 0],
                    "availability": 1.0,
                }
            )
        return out

    def trackers(self, h: str) -> list[dict[str, Any]] | None:
        handle = self.handle(h)
        if not handle:
            return None
        out = [
            {
                "url": f"** [{n}] **",
                "status": 2 if on else 0,
                "tier": -1,
                "num_peers": 0,
                "num_seeds": 0,
                "num_leeches": 0,
                "num_downloaded": 0,
                "msg": "",
            }
            for n, on in (
                ("DHT", self.settings.get("dht")),
                ("PeX", self.settings.get("pex")),
                ("LSD", self.settings.get("lsd")),
            )
        ]
        for t in handle.trackers():
            status, msg, seeds, leeches = 1, "", 0, 0
            for ep in t.get("endpoints", []):
                for ih in ep.get("info_hashes", {}).values():
                    if ih.get("updating"):
                        status = max(status, 3)
                    elif ih.get("fails", 0) > 0:
                        status = 4
                        msg = (
                            ep.get("message") or ih.get("last_error", {}).get("message", "")
                            if isinstance(ih.get("last_error"), dict)
                            else ep.get("message", "")
                        )
                    elif ih.get("start_sent") or ih.get("complete_sent") or ep.get("message") is not None:
                        status = max(status, 2) if status != 4 else 4
                    seeds = max(seeds, ih.get("scrape_complete", 0) or 0)
                    leeches = max(leeches, ih.get("scrape_incomplete", 0) or 0)
            out.append(
                {
                    "url": t["url"],
                    "status": status,
                    "tier": t.get("tier", 0),
                    "num_peers": 0,
                    "num_seeds": seeds,
                    "num_leeches": leeches,
                    "num_downloaded": 0,
                    "msg": msg,
                }
            )
        return out

    def peers(self, h: str) -> dict[str, Any]:
        handle = self.handle(h)
        out: dict[str, Any] = {}
        if not handle:
            return out
        for pi in handle.get_peer_info():
            ip, port = pi.ip
            out[f"{ip}:{port}"] = {
                "ip": ip,
                "port": port,
                "client": pi.client if isinstance(pi.client, str) else pi.client.decode(errors="replace"),
                "progress": pi.progress,
                "dl_speed": pi.payload_down_speed,
                "up_speed": pi.payload_up_speed,
                "downloaded": pi.total_download,
                "uploaded": pi.total_upload,
                "flags": "",
                "connection": "uTP" if pi.flags & pi.utp_socket else "BT",
                "country": "",
                "country_code": "",
                "relevance": pi.progress,
                "files": "",
            }
        return out

    def add_trackers(self, h: str, urls: list[str]) -> None:
        handle = self.handle(h)
        if handle:
            for u in urls:
                handle.add_tracker({"url": u, "tier": 0})

    def remove_trackers(self, h: str, urls: list[str]) -> None:
        handle = self.handle(h)
        if handle:
            handle.replace_trackers([t for t in handle.trackers() if t["url"] not in urls])

    def edit_tracker(self, h: str, old: str, new: str) -> None:
        handle = self.handle(h)
        if handle:
            ts = [{"url": new if t["url"] == old else t["url"], "tier": t["tier"]} for t in handle.trackers()]
            handle.replace_trackers(ts)

    def server_state(self) -> dict[str, Any]:
        st = self.ses.status()
        sts = self.statuses()
        dl_all = sum(s.all_time_download for s in sts.values())
        ul_all = sum(s.all_time_upload for s in sts.values())
        try:
            free = (
                os.statvfs(self.settings.get("save_path")).f_bavail
                * os.statvfs(self.settings.get("save_path")).f_frsize
            )
        except (OSError, AttributeError):
            import shutil

            try:
                free = shutil.disk_usage(self.settings.get("save_path")).free
            except OSError:
                free = 0
        return {
            "connection_status": "connected" if st.has_incoming_connections or st.num_peers else "firewalled",
            "dht_nodes": st.dht_nodes,
            "dl_info_data": st.total_payload_download,
            "dl_info_speed": st.payload_download_rate,
            "dl_rate_limit": self.settings.get("dl_limit"),
            "up_info_data": st.total_payload_upload,
            "up_info_speed": st.payload_upload_rate,
            "up_rate_limit": self.settings.get("up_limit"),
            "free_space_on_disk": free,
            "global_ratio": f"{(ul_all / dl_all) if dl_all else 0:.2f}",
            "alltime_dl": dl_all,
            "alltime_ul": ul_all,
            "queueing": self.settings.get("queueing_enabled"),
            "use_alt_speed_limits": bool(self.settings.get("alt_speed_active")),
            "refresh_interval": 1500,
            "total_peer_connections": st.num_peers,
            "use_subcategories": False,
            "write_cache_overload": "0",
            "read_cache_overload": "0",
        }


def _matches_filter(info: dict[str, Any], f: str) -> bool:
    s = info["state"]
    if f in ("all", ""):
        return True
    if f == "downloading":
        return s in (
            "downloading",
            "metaDL",
            "forcedMetaDL",
            "stalledDL",
            "forcedDL",
            "queuedDL",
            "checkingDL",
            "pausedDL",
            "allocating",
        )
    if f == "seeding":
        return s in ("uploading", "stalledUP", "forcedUP", "queuedUP", "checkingUP")
    if f == "completed":
        return s in ("uploading", "stalledUP", "forcedUP", "queuedUP", "checkingUP", "pausedUP")
    if f in ("paused", "stopped"):
        return s.startswith("paused")
    if f == "resumed":
        return not s.startswith("paused")
    if f == "active":
        return info["dlspeed"] > 0 or info["upspeed"] > 0
    if f == "inactive":
        return info["dlspeed"] == 0 and info["upspeed"] == 0
    if f == "stalled":
        return s in ("stalledUP", "stalledDL")
    if f == "stalled_uploading":
        return s == "stalledUP"
    if f == "stalled_downloading":
        return s == "stalledDL"
    if f == "errored":
        return s in ("error", "missingFiles")
    return True
