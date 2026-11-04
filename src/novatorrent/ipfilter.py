"""IP filter parsing: eMule ipfilter.dat and PeerGuardian text (.p2p) formats, optional gzip."""

from __future__ import annotations

import gzip
import ipaddress
import re
from pathlib import Path

import httpx
import libtorrent as lt

MAX_FILTER_BYTES = 50 * 1024 * 1024
EMULE_RE = re.compile(r"^\s*(?P<a>[0-9a-fA-F.:]+)\s*-\s*(?P<b>[0-9a-fA-F.:]+)\s*,\s*(?P<level>\d*)")
BLOCK_LEVEL = 127  # eMule: levels >= 128 mean "allowed"


def parse_lines(text: str) -> list[tuple[str, str]]:
    """Return (first, last) address ranges to block."""
    out: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";", "//")):
            continue
        try:
            m = EMULE_RE.match(line)
            if m:  # eMule: "001.002.003.004 - 001.002.003.255 , 000 , description"
                if int(m.group("level") or 0) > BLOCK_LEVEL:
                    continue
                first, last = m.group("a"), m.group("b")
            else:  # PeerGuardian: "description:1.2.3.4-1.2.3.255"
                first, _, last = line.rsplit(":", 1)[-1].partition("-") if "-" in line else (line, "", "")
            a = str(ipaddress.ip_address(_strip_zeros(first.strip())))
            b = str(ipaddress.ip_address(_strip_zeros(last.strip() or first.strip())))
            out.append((a, b))
        except ValueError:
            continue  # skip malformed lines instead of failing the whole list
    return out


def _strip_zeros(ip: str) -> str:
    if "." in ip:
        return ".".join(str(int(p)) for p in ip.split("."))
    return ip


def load_ip_filter(path: str | Path) -> tuple[lt.ip_filter, int]:
    data = Path(path).read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    flt, n = lt.ip_filter(), 0
    for a, b in parse_lines(data.decode("utf-8", errors="ignore")):
        flt.add_rule(a, b, 1)  # 1 = blocked
        n += 1
    return flt, n


def update_from_url(url: str, dest: Path) -> None:
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("only http(s) IP filter URLs are allowed")
    with httpx.Client(follow_redirects=True, timeout=60) as c:
        r = c.get(url)
        r.raise_for_status()
        if len(r.content) > MAX_FILTER_BYTES:
            raise ValueError("IP filter too large")
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.write_bytes(r.content)
        load_ip_filter(tmp)  # validate before replacing the working filter
        tmp.replace(dest)
