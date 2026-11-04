"""Notifications (Discord, ntfy, Telegram, generic webhook) and safe completion hooks."""

from __future__ import annotations

import os
import queue
import shlex
import subprocess
import threading
import time
from typing import Any

import httpx

from .engine import Engine

EVENTS = ("added", "completed", "error", "disk_full", "vpn_down", "vpn_up", "seed_limit")
DEFAULT_EVENTS = {
    "added": False,
    "completed": True,
    "error": True,
    "disk_full": True,
    "vpn_down": True,
    "vpn_up": False,
    "seed_limit": False,
}
SECRET_KEYS = ("discord_url", "ntfy_url", "telegram_token", "webhook_url")
MASK = "***"


def notify_config(settings: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(settings.get("nova_notify") or {})
    cfg["events"] = {**DEFAULT_EVENTS, **(cfg.get("events") or {})}
    return cfg


def mask_notify(cfg: dict[str, Any]) -> dict[str, Any]:
    """Never send stored webhook URLs/tokens back to the browser."""
    out = dict(cfg)
    for k in SECRET_KEYS:
        if out.get(k):
            out[k] = MASK
    return out


def merge_notify(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    out = {**old, **new}
    for k in SECRET_KEYS:
        if new.get(k) == MASK:
            out[k] = old.get(k, "")
    return out


def render(event: str, info: dict[str, Any]) -> str:
    name = info.get("name") or info.get("hash", "")[:8]
    text = {
        "added": f"Added: {name}",
        "completed": f"Finished: {name}",
        "error": f"Error: {info.get('message', name)}",
        "disk_full": f"Low disk space ({info.get('free_mb', '?')} MiB free), downloads paused",
        "vpn_down": f"VPN/binding lost: {info.get('reason', '')}",
        "vpn_up": "VPN/binding restored",
        "seed_limit": f"Seed limit reached: {name}",
    }.get(event, event)
    return f"NovaTorrent - {text}"


class Notifier:
    def __init__(self, engine: Engine):
        self.e = engine
        self.q: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue(maxsize=200)
        self.sent: list[tuple[str, str]] = []  # (channel, message) for tests/diagnostics
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        engine.on_event = self.dispatch

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker, name="notify", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def dispatch(self, event: str, info: dict[str, Any]) -> None:
        s = self.e.settings.snapshot()
        if event == "completed" and s["nova_hook_enabled"] and s["nova_hook_command"]:
            threading.Thread(target=self._hook, args=(s, info), daemon=True).start()
        if notify_config(s)["events"].get(event):
            try:
                self.q.put_nowait((event, info))
            except queue.Full:
                pass

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                event, info = self.q.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self.send_all(event, info)
            except Exception as ex:
                self.e.add_log(f"notification failed: {type(ex).__name__}", 4)

    def send_all(self, event: str, info: dict[str, Any]) -> list[str]:
        cfg = notify_config(self.e.settings.snapshot())
        msg = render(event, info)
        done: list[str] = []
        with httpx.Client(timeout=10, follow_redirects=False) as c:
            for ch, fn in (
                ("discord", self._discord),
                ("ntfy", self._ntfy),
                ("telegram", self._telegram),
                ("webhook", self._webhook),
            ):
                try:
                    if fn(c, cfg, msg, event, info):
                        done.append(ch)
                        self.sent.append((ch, msg))
                except Exception as ex:  # one broken channel must not block others
                    self.e.add_log(f"notification via {ch} failed: {type(ex).__name__}", 4)
        return done

    @staticmethod
    def _ok_url(u: str) -> bool:
        return u.lower().startswith(("http://", "https://"))

    def _discord(self, c: httpx.Client, cfg: dict, msg: str, *_: Any) -> bool:
        if not self._ok_url(cfg.get("discord_url", "")):
            return False
        c.post(cfg["discord_url"], json={"content": msg}).raise_for_status()
        return True

    def _ntfy(self, c: httpx.Client, cfg: dict, msg: str, *_: Any) -> bool:
        if not self._ok_url(cfg.get("ntfy_url", "")):
            return False
        c.post(cfg["ntfy_url"], content=msg.encode(), headers={"Title": "NovaTorrent"}).raise_for_status()
        return True

    def _telegram(self, c: httpx.Client, cfg: dict, msg: str, *_: Any) -> bool:
        if not (cfg.get("telegram_token") and cfg.get("telegram_chat")):
            return False
        url = f"https://api.telegram.org/bot{cfg['telegram_token']}/sendMessage"
        c.post(url, json={"chat_id": cfg["telegram_chat"], "text": msg}).raise_for_status()
        return True

    def _webhook(self, c: httpx.Client, cfg: dict, msg: str, event: str, info: dict) -> bool:
        if not self._ok_url(cfg.get("webhook_url", "")):
            return False
        body = {"event": event, "message": msg, "data": info, "time": int(time.time())}
        c.post(cfg["webhook_url"], json=body).raise_for_status()
        return True

    # ---- completion hook ---------------------------------------------------------------------
    def _hook(self, s: dict[str, Any], info: dict[str, Any]) -> None:
        """Run the configured command WITHOUT a shell; torrent data goes only via environment
        variables, never through the command line, so names cannot inject arguments."""
        try:
            argv = shlex.split(s["nova_hook_command"], posix=True)
            if not argv:
                return
            h = info.get("hash", "")
            ti = next(iter(self.e.list_info(hashes=[h])), {})
            env = {
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),  # needed by Python on Windows
                "NOVA_HASH": h,
                "NOVA_NAME": str(ti.get("name", info.get("name", ""))),
                "NOVA_PATH": str(ti.get("content_path", "")),
                "NOVA_CATEGORY": str(ti.get("category", "")),
            }
            r = subprocess.run(argv, env=env, shell=False, timeout=60, capture_output=True)
            self.e.add_log(f"completion hook exited with {r.returncode}", 2)
        except Exception as ex:
            self.e.add_log(f"completion hook failed: {type(ex).__name__}", 4)
