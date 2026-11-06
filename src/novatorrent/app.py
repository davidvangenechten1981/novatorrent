"""FastAPI application: qBittorrent-compatible API, Nova API, health endpoint, static web-UI."""

from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import UploadFile

from .auth import AuthStore
from .engine import Engine
from .manager import Manager
from .netmon import NetMonitor
from .notify import Notifier
from .novaapi import build_router
from .qbapi import READONLY_OK, Params, QbApi
from .rss import RssManager
from .security import SecurityMiddleware
from .settings import Settings, State

WEB_DIR = Path(__file__).parent / "web"


def create_app(config_dir: Path | None = None) -> FastAPI:
    config_dir = Path(config_dir or os.environ.get("NOVA_CONFIG_DIR", "/config"))
    config_dir.mkdir(parents=True, exist_ok=True)
    settings, state = Settings(config_dir), State(config_dir)
    if os.environ.get("NOVA_DEFAULT_SAVE_PATH"):
        settings.data["save_path"] = os.environ["NOVA_DEFAULT_SAVE_PATH"]
    # Deployment overrides (compose): applied on every start so a VPN setup cannot be misconfigured away.
    env_over = {}
    if os.environ.get("NOVA_BIND_INTERFACE"):
        env_over["current_network_interface"] = os.environ["NOVA_BIND_INTERFACE"]
    if os.environ.get("NOVA_REQUIRE_VPN") == "1":
        env_over["nova_require_vpn_binding"] = True
    if os.environ.get("NOVA_PORT_FILE"):
        env_over["nova_port_file"] = os.environ["NOVA_PORT_FILE"]
    if env_over:
        settings.update(env_over)
    auth = AuthStore(config_dir)
    engine = Engine(config_dir, settings, state)
    qb = QbApi(engine)
    netmon = NetMonitor(engine)
    manager = Manager(engine)
    notifier = Notifier(engine)
    rss = RssManager(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await run_in_threadpool(engine.start)
        if no_auth:
            engine.add_log("AUTHENTICATION DISABLED (NOVA_AUTH_DISABLED=1): anyone on the LAN has admin access", 4)
        await run_in_threadpool(netmon.start)
        manager.start()
        notifier.start()
        rss.start()
        yield
        for svc in (rss, notifier, manager, netmon):
            svc.stop()
        await run_in_threadpool(engine.stop)

    app = FastAPI(title="NovaTorrent", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SecurityMiddleware)
    app.state.engine, app.state.auth, app.state.settings, app.state.qb = engine, auth, settings, qb
    app.state.manager, app.state.notifier, app.state.rss = manager, notifier, rss

    # Opt-in "no login" mode for a trusted LAN. Guard rails: never together with NOVA_ALLOW_WAN, never for
    # requests that went through a reverse proxy (X-Forwarded-*), and the completion hook is disabled.
    no_auth = os.environ.get("NOVA_AUTH_DISABLED") == "1"
    if no_auth and os.environ.get("NOVA_ALLOW_WAN") == "1":
        raise RuntimeError("NOVA_AUTH_DISABLED=1 cannot be combined with NOVA_ALLOW_WAN=1")
    proxy_headers = ("x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip", "forwarded")

    def anonymous(request: Request) -> bool:
        return no_auth and not any(h in request.headers for h in proxy_headers)

    def principal(request: Request) -> tuple[str, str] | None:
        hdr = request.headers.get("authorization", "")
        if hdr.lower().startswith("bearer "):
            role = auth.check_api_key(hdr[7:].strip())
            return ("apikey", role) if role else None
        sess = auth.get_session(request.cookies.get("SID"))
        if sess:
            return (sess[0], sess[1])
        return ("anonymous", "admin") if anonymous(request) else None

    def csrf_ok(request: Request) -> bool:
        """State-changing /api/nova calls need the per-session token; API keys are exempt
        because they are not sent automatically by a browser."""
        if request.headers.get("authorization", "").lower().startswith("bearer "):
            return True
        sess = auth.get_session(request.cookies.get("SID"))
        if not sess and anonymous(request):
            # no session token exists: fall back to the browser Origin/Referer check
            return not origin_mismatch(request)
        token = request.headers.get("x-csrf-token", "")
        return bool(sess) and hmac.compare_digest(sess[2], token)

    ctx = SimpleNamespace(engine=engine, auth=auth, rss=rss, notifier=notifier, principal=principal, csrf_ok=csrf_ok)
    app.include_router(build_router(ctx))

    @app.get("/api/health")
    def health() -> Response:
        # Intentionally unauthenticated and free of details: used by Docker/Uptime Kuma.
        if engine.ses is None:
            return JSONResponse({"status": "starting"}, status_code=503)
        if not engine.vpn_ok:
            return JSONResponse({"status": "vpn_down"}, status_code=503)
        return JSONResponse({"status": "ok"})

    @app.get("/metrics")
    def metrics(request: Request) -> Response:
        """Prometheus metrics; opt-in via NOVA_METRICS=1 and needs a valid session/API key."""
        if os.environ.get("NOVA_METRICS") != "1":
            return PlainTextResponse("Not Found", 404)
        if not principal(request):
            return PlainTextResponse("Forbidden", 403)
        st = engine.server_state()
        infos = engine.list_info()
        lines = [
            "# TYPE novatorrent_download_bytes_per_second gauge",
            f"novatorrent_download_bytes_per_second {st['dl_info_speed']}",
            "# TYPE novatorrent_upload_bytes_per_second gauge",
            f"novatorrent_upload_bytes_per_second {st['up_info_speed']}",
            "# TYPE novatorrent_torrents gauge",
            f"novatorrent_torrents {len(infos)}",
            "# TYPE novatorrent_peers gauge",
            f"novatorrent_peers {st['total_peer_connections']}",
            "# TYPE novatorrent_free_space_bytes gauge",
            f"novatorrent_free_space_bytes {st['free_space_on_disk']}",
            "# TYPE novatorrent_alltime_downloaded_bytes counter",
            f"novatorrent_alltime_downloaded_bytes {st['alltime_dl']}",
            "# TYPE novatorrent_alltime_uploaded_bytes counter",
            f"novatorrent_alltime_uploaded_bytes {st['alltime_ul']}",
            "# TYPE novatorrent_vpn_up gauge",
            f"novatorrent_vpn_up {1 if engine.vpn_ok else 0}",
        ]
        return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    async def read_params(request: Request) -> Params:
        data: dict[str, str] = dict(request.query_params)
        files: list[tuple[str, bytes]] = []
        if request.method == "POST":
            form = await request.form()
            for k, v in form.multi_items():
                if isinstance(v, UploadFile):
                    files.append((v.filename or k, await v.read()))
                else:
                    data[k] = v
        return Params(data, files)

    def origin_mismatch(request: Request) -> bool:
        """qBittorrent-style CSRF check for cookie-authenticated POSTs: if the browser sends
        Origin/Referer, its host must equal the Host header. Non-browser clients send neither."""
        if request.method != "POST" or request.headers.get("authorization"):
            return False
        src = request.headers.get("origin") or request.headers.get("referer")
        return bool(src) and urlparse(src).netloc != request.headers.get("host", "")

    @app.api_route("/api/v2/{section}/{name}", methods=["GET", "POST"])
    async def qb_dispatch(section: str, name: str, request: Request) -> Response:
        route = f"{section}/{name}"
        ip = request.client.host if request.client else "?"
        if origin_mismatch(request):
            return PlainTextResponse("Unauthorized", 401)
        params = await read_params(request)
        if route == "auth/login" and anonymous(request):
            return PlainTextResponse("Ok.")  # no login needed; clients that still send credentials keep working
        if route == "auth/login":
            wait = auth.locked_for(ip)
            if wait:
                return PlainTextResponse(
                    f"Your IP address has been banned for {wait}s after too many failed authentication attempts.", 403
                )
            role = await run_in_threadpool(auth.verify, params.s("username"), params.s("password"))
            if not role:
                auth.record_fail(ip)
                return PlainTextResponse("Fails.")
            auth.record_ok(ip)
            sid, _ = auth.new_session(params.s("username"), role)
            resp = PlainTextResponse("Ok.")
            resp.set_cookie("SID", sid, httponly=True, samesite="strict", path="/")
            return resp
        who = principal(request)
        if not who:
            return PlainTextResponse("Forbidden", 403)
        if route == "auth/logout":
            auth.end_session(request.cookies.get("SID"))
            return PlainTextResponse("Ok.")
        if who[1] != "admin" and route not in READONLY_OK:
            return PlainTextResponse("Forbidden", 403)
        if engine.ses is None:
            return PlainTextResponse("Service Unavailable", 503)
        r = await run_in_threadpool(qb.call, route, params)
        if r.media == "json":
            return JSONResponse(r.body, status_code=r.status)
        return PlainTextResponse(str(r.body), status_code=r.status)

    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

    return app
