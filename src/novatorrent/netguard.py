"""Outbound URL guard: stops RSS/URL-add features from reaching loopback or link-local hosts
(e.g. cloud metadata endpoints). Private LAN ranges stay allowed because Prowlarr lives there."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

ALLOW_LOOPBACK = False  # tests only


def check_url(url: str) -> None:
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("only http(s) URLs are allowed")
    for info in socket.getaddrinfo(u.hostname, u.port or 80, proto=socket.IPPROTO_TCP):
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.is_link_local or ip.is_unspecified or ip.is_multicast:
            raise ValueError("blocked address")
        if ip.is_loopback and not ALLOW_LOOPBACK:
            raise ValueError("blocked address")
