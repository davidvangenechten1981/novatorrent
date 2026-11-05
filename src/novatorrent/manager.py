"""Periodic automation: alt-speed schedule, seeding rules, disk-space guard, first/last piece."""

from __future__ import annotations

import datetime as dt
import shutil
import threading
import time
from collections.abc import Callable
from typing import Any

from .engine import Engine


def schedule_active(s: dict[str, Any], now: dt.datetime) -> bool:
    """qBittorrent-style scheduler: from/to time and a day selector (0 every day, 1 weekdays,
    2 weekends, 3..9 Monday..Sunday). A window that crosses midnight is supported."""
    if not s.get("scheduler_enabled"):
        return bool(s.get("alt_speed_active"))
    days = int(s.get("scheduler_days", 0))
    wd = now.weekday()  # Monday = 0
    if days == 1 and wd > 4 or days == 2 and wd < 5 or days >= 3 and wd != days - 3:
        return False
    cur = now.hour * 60 + now.minute
    a = int(s["schedule_from_hour"]) * 60 + int(s["schedule_from_min"])
    b = int(s["schedule_to_hour"]) * 60 + int(s["schedule_to_min"])
    return a <= cur < b if a <= b else (cur >= a or cur < b)


def effective_limits(info: dict[str, Any], s: dict[str, Any]) -> tuple[float, int, int]:
    """Resolve per-torrent share limits against the global rules. -1 = unlimited."""
    ratio = info["ratio_limit"]
    if ratio == -2:
        ratio = s["max_ratio"] if s["max_ratio_enabled"] else -1
    seed = info["seeding_time_limit"]
    if seed == -2:
        seed = s["max_seeding_time"] if s["max_seeding_time_enabled"] else -1
    inactive = info["inactive_seeding_time_limit"]
    if inactive == -2:
        inactive = s["max_inactive_seeding_time"] if s["max_inactive_seeding_time_enabled"] else -1
    return ratio, int(seed), int(inactive)


def limit_reached(info: dict[str, Any], s: dict[str, Any]) -> str:
    ratio, seed, inactive = effective_limits(info, s)
    if ratio >= 0 and info["ratio"] >= ratio:
        return "ratio"
    if seed >= 0 and info["seeding_time"] >= seed * 60:
        return "seeding_time"
    if inactive >= 0 and time.time() - info["last_activity"] >= inactive * 60:
        return "inactive"
    return ""


class Manager:
    def __init__(
        self,
        engine: Engine,
        interval: float = 5.0,
        free_space: Callable[[str], int] | None = None,
        clock: Callable[[], dt.datetime] = dt.datetime.now,
    ):
        self.e = engine
        self.interval = interval
        self.free_space = free_space or (lambda p: shutil.disk_usage(p).free)
        self.clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.paused_for_space: set[str] = set()
        self.disk_alerted = False

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="manager", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(3)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.tick()
            except Exception as ex:
                self.e.add_log(f"manager error: {type(ex).__name__}: {ex}", 8)

    def tick(self) -> None:
        if not self.e.ses:
            return
        s = self.e.settings.snapshot()
        self._schedule(s)
        infos = self.e.list_info()
        self._seed_rules(s, infos)
        self._disk_guard(s, infos)
        self._first_last(infos)

    # ---- alternative speed schedule ------------------------------------------------------
    def _schedule(self, s: dict[str, Any]) -> None:
        if not s["scheduler_enabled"]:
            return
        want = schedule_active(s, self.clock())
        if want != bool(s["alt_speed_active"]):
            self.e.settings.update({"alt_speed_active": want})
            self.e.apply_settings()
            self.e.add_log(f"Alternative speed limits {'on' if want else 'off'} (schedule)", 2)

    # ---- seeding rules --------------------------------------------------------------------
    def _seed_rules(self, s: dict[str, Any], infos: list[dict[str, Any]]) -> None:
        for i in infos:
            if i["progress"] < 1.0 or i["state"].startswith("paused") or i["state"] == "error":
                continue
            if i["state"].startswith(("checking", "metaDL", "forced")):
                continue  # forced torrents ignore share limits, like in qBittorrent
            why = limit_reached(i, s)
            if not why:
                continue
            action = int(s["max_ratio_act"])
            self.e.add_log(f"Share limit ({why}) reached for {i['name']}: action {action}", 2)
            self.e.on_event("seed_limit", {"hash": i["hash"], "name": i["name"], "reason": why})
            if action == 0:
                self.e.pause(i["hash"])
            else:
                self.e.remove(i["hash"], delete_files=action == 2)

    # ---- disk space -----------------------------------------------------------------------
    def _disk_guard(self, s: dict[str, Any], infos: list[dict[str, Any]]) -> None:
        need = int(s["nova_min_free_space_mb"]) * 1024 * 1024
        if need <= 0:
            return
        try:
            free = self.free_space(s["save_path"])
        except OSError:
            return
        if free < need:
            for i in infos:
                if (
                    i["progress"] < 1.0
                    and not i["state"].startswith("paused")
                    and not i["force_start"]
                    and i["hash"] not in self.paused_for_space
                ):
                    self.e.pause(i["hash"])
                    self.paused_for_space.add(i["hash"])
            if not self.disk_alerted:
                self.disk_alerted = True
                self.e.add_log(f"Low disk space ({free // 2**20} MiB free): downloads paused", 8)
                self.e.on_event("disk_full", {"free_mb": free // 2**20})
        elif free > need * 1.2:  # hysteresis to avoid flapping
            for h in list(self.paused_for_space):
                self.e.resume(h)
                self.paused_for_space.discard(h)
            self.disk_alerted = False

    # ---- first/last piece priority ----------------------------------------------------------
    def _first_last(self, infos: list[dict[str, Any]]) -> None:
        for i in infos:
            m = self.e.meta(i["hash"])
            if not m.get("first_last") or m.get("fl_applied"):
                continue
            handle = self.e.handle(i["hash"])
            if not handle or not handle.status().has_metadata:
                continue
            ti = handle.torrent_file()
            fs = ti.layout()
            for f in range(fs.num_files()):
                size = fs.file_size(f)
                if size == 0:
                    continue
                for off in (0, size - 1):
                    handle.piece_priority(ti.map_file(f, off, 1).piece, 7)
            m["fl_applied"] = True
