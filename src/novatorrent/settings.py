"""Persistent settings and state store (atomic JSON files under the config dir)."""

from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any

# Keys follow qBittorrent preference names where one exists; extras use the nova_ prefix.
DEFAULTS: dict[str, Any] = {
    # paths
    "save_path": "/data/downloads",
    "temp_path_enabled": False,
    "temp_path": "",
    "create_subfolder_enabled": True,
    "start_paused_enabled": False,
    "preallocate_all": False,
    "incomplete_files_ext": False,
    "auto_tmm_enabled": False,
    # limits
    "dl_limit": 0,
    "up_limit": 0,
    "alt_dl_limit": 10240,
    "alt_up_limit": 10240,
    "alt_speed_active": False,
    "scheduler_enabled": False,
    "schedule_from_hour": 8,
    "schedule_from_min": 0,
    "schedule_to_hour": 20,
    "schedule_to_min": 0,
    "scheduler_days": 0,  # 0 every day, 1-5 weekdays/weekend variants as in qB
    "max_connec": 500,
    "max_connec_per_torrent": 100,
    "max_uploads": 20,
    "max_uploads_per_torrent": 4,
    # queueing
    "queueing_enabled": True,
    "max_active_downloads": 5,
    "max_active_uploads": 50,
    "max_active_torrents": 200,
    "max_active_checking_torrents": 1,
    "dont_count_slow_torrents": True,
    "slow_torrent_dl_rate_threshold": 2,
    "slow_torrent_ul_rate_threshold": 2,
    "slow_torrent_inactive_timer": 60,
    # seeding rules
    "max_ratio_enabled": False,
    "max_ratio": 2.0,
    "max_seeding_time_enabled": False,
    "max_seeding_time": 1440,
    "max_inactive_seeding_time_enabled": False,
    "max_inactive_seeding_time": 1440,
    "max_ratio_act": 0,  # 0 pause, 1 remove, 2 remove with files
    # network (phase 2)
    "listen_port": 6881,
    "upnp": False,
    "random_port": False,
    "dht": True,
    "pex": True,
    "lsd": True,
    "utp_enabled": True,
    "tcp_enabled": True,
    "encryption": 0,  # 0 prefer, 1 require, 2 disable
    "anonymous_mode": False,
    "current_network_interface": "",
    "current_interface_address": "",
    "nova_ipv4": True,
    "nova_ipv6": True,
    "nova_require_vpn_binding": False,
    "nova_port_file": "",
    "proxy_type": "None",  # None, SOCKS5, HTTP
    "proxy_ip": "",
    "proxy_port": 8080,
    "proxy_username": "",
    "proxy_password": "",
    "proxy_peer_connections": True,
    "ip_filter_enabled": False,
    "ip_filter_path": "",
    "nova_ip_filter_url": "",
    "nova_ip_filter_interval_h": 24,
    # disk / misc
    "nova_min_free_space_mb": 1024,
    "nova_warn_free_space_mb": 5120,  # early warning only (0 = off); downloads pause at nova_min_free_space_mb
    "nova_max_peerlist_size": 3000,  # libtorrent default; lower (e.g. 500) saves memory with many torrents
    "nova_sequential_default": False,
    "nova_extra_trackers_enabled": False,
    "nova_extra_trackers": "",
    # notifications / hooks
    "nova_notify": {},
    "nova_hook_enabled": False,
    "nova_hook_command": "",
    # web
    "locale": "en",
    "web_ui_port": 8080,
}


def atomic_write(path: Path, data: bytes, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    if mode is not None:
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
    os.replace(tmp, path)


class JsonStore:
    """Small thread-safe JSON document persisted atomically."""

    def __init__(self, path: Path, defaults: dict[str, Any]):
        self.path = path
        self.lock = threading.RLock()
        self.data: dict[str, Any] = copy.deepcopy(defaults)
        if path.exists():
            try:
                self.data.update(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                # Keep the broken file for inspection instead of overwriting silently.
                try:
                    os.replace(path, path.with_suffix(".corrupt"))
                except OSError:
                    pass

    def save(self) -> None:
        with self.lock:
            atomic_write(self.path, json.dumps(self.data, indent=1).encode())

    def get(self, key: str, default: Any = None) -> Any:
        with self.lock:
            return self.data.get(key, default)

    def update(self, values: dict[str, Any]) -> None:
        with self.lock:
            self.data.update(values)
            self.save()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return copy.deepcopy(self.data)


class Settings(JsonStore):
    SECRET_KEYS = frozenset({"proxy_password"})

    def __init__(self, config_dir: Path):
        super().__init__(config_dir / "settings.json", DEFAULTS)

    def update(self, values: dict[str, Any]) -> None:
        # Ignore unknown keys so clients cannot grow the file arbitrarily.
        super().update({k: v for k, v in values.items() if k in DEFAULTS})


STATE_DEFAULTS: dict[str, Any] = {
    "categories": {},  # name -> {"savePath": str}
    "tags": [],
    "torrents": {},  # hash -> metadata
    "rss": {"feeds": {}, "rules": {}, "seen": {}, "episodes": []},
    "banned": [],  # banned peer IPs
}


class State(JsonStore):
    def __init__(self, config_dir: Path):
        super().__init__(config_dir / "state.json", STATE_DEFAULTS)
