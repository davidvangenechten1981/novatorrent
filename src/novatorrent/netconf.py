"""Translate NovaTorrent settings into a libtorrent settings pack (network, limits, privacy)."""

from __future__ import annotations

import ipaddress
from typing import Any

import libtorrent as lt

# libtorrent enum values (stable ABI): proxy_type_t, enc_policy, enc_level
PROXY_NONE, PROXY_SOCKS5, PROXY_SOCKS5_PW, PROXY_HTTP, PROXY_HTTP_PW = 0, 2, 3, 4, 5
USER_AGENT = "NovaTorrent/0.1 libtorrent/" + lt.__version__


def listen_interfaces(s: dict[str, Any]) -> str:
    """Build the listen_interfaces string. Binding to an interface/IP is what keeps VPN traffic safe.

    If a binding is requested but ``nova_require_vpn_binding`` is set and the interface is not
    present, the caller (VPN monitor) keeps the session paused; here we only build the string.
    """
    port = 0 if s.get("random_port") else int(s.get("listen_port", 6881))
    iface = (s.get("current_network_interface") or "").strip()
    addr = (s.get("current_interface_address") or "").strip()
    v4, v6 = bool(s.get("nova_ipv4", True)), bool(s.get("nova_ipv6", True))
    if addr:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            ip = None
        if ip is not None:
            if (ip.version == 4 and not v4) or (ip.version == 6 and not v6):
                return f"127.0.0.1:{0}"  # requested family disabled: listen nowhere useful
            return f"[{ip}]:{port}" if ip.version == 6 else f"{ip}:{port}"
    if iface:
        return f"{iface}:{port}"
    parts = []
    if v4:
        parts.append(f"0.0.0.0:{port}")
    if v6:
        parts.append(f"[::]:{port}")
    return ",".join(parts) or f"127.0.0.1:{0}"


def build_settings_pack(s: dict[str, Any], partial: bool = False) -> dict[str, Any]:
    kib = 1024
    alt = bool(s.get("alt_speed_active"))
    dl = int(s["alt_dl_limit" if alt else "dl_limit"]) * kib
    ul = int(s["alt_up_limit" if alt else "up_limit"]) * kib
    enc = int(s.get("encryption", 0))
    pol = {0: 1, 1: 0, 2: 2}.get(enc, 1)  # prefer -> enabled, require -> forced, disable -> disabled
    ptype = {"None": PROXY_NONE, "SOCKS5": PROXY_SOCKS5, "HTTP": PROXY_HTTP}.get(
        str(s.get("proxy_type", "None")), PROXY_NONE
    )
    if ptype in (PROXY_SOCKS5, PROXY_HTTP) and s.get("proxy_username"):
        ptype += 1
    iface = (s.get("current_network_interface") or "").strip()
    addr = (s.get("current_interface_address") or "").strip()
    pack: dict[str, Any] = {
        "user_agent": USER_AGENT,
        "listen_interfaces": listen_interfaces(s),
        "outgoing_interfaces": addr or iface,
        "alert_mask": (
            lt.alert.category_t.error_notification
            | lt.alert.category_t.status_notification
            | lt.alert.category_t.storage_notification
            | lt.alert.category_t.port_mapping_notification
            | lt.alert.category_t.ip_block_notification
        ),
        "enable_dht": bool(s.get("dht")),
        "enable_lsd": bool(s.get("lsd")),
        "enable_upnp": bool(s.get("upnp")),
        "enable_natpmp": bool(s.get("upnp")),
        "enable_incoming_utp": bool(s.get("utp_enabled")),
        "enable_outgoing_utp": bool(s.get("utp_enabled")),
        "enable_incoming_tcp": bool(s.get("tcp_enabled")),
        "enable_outgoing_tcp": bool(s.get("tcp_enabled")),
        "out_enc_policy": pol,
        "in_enc_policy": pol,
        "allowed_enc_level": 3,
        "anonymous_mode": bool(s.get("anonymous_mode")),
        "proxy_type": ptype,
        "proxy_hostname": s.get("proxy_ip", ""),
        "proxy_port": int(s.get("proxy_port", 8080)),
        "proxy_username": s.get("proxy_username", ""),
        "proxy_password": s.get("proxy_password", ""),
        "proxy_peer_connections": bool(s.get("proxy_peer_connections", True)),
        "proxy_hostnames": ptype != PROXY_NONE,
        "proxy_tracker_connections": ptype != PROXY_NONE,
        "download_rate_limit": dl,
        "upload_rate_limit": ul,
        "connections_limit": int(s.get("max_connec", 500)),
        "unchoke_slots_limit": int(s.get("max_uploads", 20)),
        "active_downloads": _q(s, "max_active_downloads"),
        "active_seeds": _q(s, "max_active_uploads"),
        "active_limit": _q(s, "max_active_torrents"),
        "active_checking": int(s.get("max_active_checking_torrents", 1)),
        "dont_count_slow_torrents": bool(s.get("dont_count_slow_torrents")),
        "inactive_down_rate": int(s.get("slow_torrent_dl_rate_threshold", 2)) * kib,
        "inactive_up_rate": int(s.get("slow_torrent_ul_rate_threshold", 2)) * kib,
        "auto_manage_startup": 60,
        "announce_to_all_trackers": False,
        "announce_to_all_tiers": True,
        "strict_end_game_mode": True,
        "mixed_mode_algorithm": 0,
    }
    if ptype != PROXY_NONE:
        # With a proxy there must be no direct connections: disable what cannot be proxied.
        pack["enable_upnp"] = False
        pack["enable_natpmp"] = False
        pack["enable_lsd"] = False
    if s.get("anonymous_mode"):
        pack["enable_upnp"] = False
        pack["enable_natpmp"] = False
        pack["enable_lsd"] = False
    base = {} if partial else lt.default_settings()
    base.update(pack)
    return base


def _q(s: dict[str, Any], key: str) -> int:
    """Queueing off means no limit (-1)."""
    if not s.get("queueing_enabled", True):
        return -1
    v = int(s.get(key, -1))
    return v if v > 0 else -1


def session_extensions(ses: Any, s: dict[str, Any]) -> None:
    ses.add_extension(lt.create_ut_metadata_plugin)
    ses.add_extension(lt.create_smart_ban_plugin)
    if s.get("pex") and not s.get("anonymous_mode"):
        ses.add_extension(lt.create_ut_pex_plugin)
