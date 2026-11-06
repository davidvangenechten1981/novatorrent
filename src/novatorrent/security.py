"""HTTP hardening: LAN-only by default, Host allow-list, per-IP rate limit, security headers."""

from __future__ import annotations

import ipaddress
import os
import threading
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def is_lan(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    # CGNAT 100.64/10 (Tailscale etc.) is treated as private too.
    return a.is_private or a.is_loopback or a.is_link_local or a in ipaddress.ip_network("100.64.0.0/10")


class TokenBucket:
    def __init__(self, rate: float, burst: int):
        self.rate, self.burst = rate, burst
        self.lock = threading.Lock()
        self.state: dict[str, tuple[float, float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            if len(self.state) > 5000:
                self.state.clear()
            tokens, last = self.state.get(key, (float(self.burst), now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens < 1:
                self.state[key] = (tokens, now)
                return False
            self.state[key] = (tokens - 1, now)
            return True


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, rate: float | None = None, burst: int | None = None):
        super().__init__(app)
        if rate is None:
            try:
                rate = float(os.environ.get("NOVA_RATE_LIMIT", "30"))
            except ValueError:
                rate = 30.0
            rate = max(rate, 1.0)
        if burst is None:
            burst = max(200, int(rate * 6))
        self.bucket = TokenBucket(rate, burst)

    async def dispatch(self, request: Request, call_next) -> Response:
        ip = request.client.host if request.client else ""
        allow_wan = os.environ.get("NOVA_ALLOW_WAN") == "1"
        if ip and ip != "testclient" and not allow_wan and not is_lan(ip):
            return PlainTextResponse("Forbidden", 403)
        hosts = [h.strip().lower() for h in os.environ.get("NOVA_ALLOWED_HOSTS", "").split(",") if h.strip()]
        if hosts:
            host = request.headers.get("host", "").lower()
            host = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
            # Loopback is always allowed: the container healthcheck calls 127.0.0.1.
            if host not in hosts and host not in LOOPBACK_HOSTS:
                return PlainTextResponse("Invalid host header", 400)
        if request.url.path != "/api/health" and not self.bucket.allow(ip):
            return PlainTextResponse("Too Many Requests", 429, headers={"Retry-After": "1"})
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Content-Security-Policy", CSP)
        if not request.url.path.startswith("/api/"):
            # Static UI files: always revalidate (ETag), so a browser never keeps running JS from an older version.
            resp.headers.setdefault("Cache-Control", "no-cache")
        return resp
