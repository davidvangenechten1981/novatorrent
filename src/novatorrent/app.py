"""FastAPI application: qBittorrent-compatible API, health endpoint, static web-UI."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from starlette.datastructures import UploadFile

from .auth import AuthStore
from .engine import Engine
from .netmon import NetMonitor
from .qbapi import READONLY_OK, Params, QbApi
from .settings import Settings, State


def create_app(config_dir: Path | None = None) -> FastAPI:
    config_dir = Path(config_dir or os.environ.get("NOVA_CONFIG_DIR", "/config"))
    config_dir.mkdir(parents=True, exist_ok=True)
    settings, state = Settings(config_dir), State(config_dir)
    if os.environ.get("NOVA_DEFAULT_SAVE_PATH"):
        settings.data["save_path"] = os.environ["NOVA_DEFAULT_SAVE_PATH"]
    auth = AuthStore(config_dir)
    engine = Engine(config_dir, settings, state)
    qb = QbApi(engine)
    netmon = NetMonitor(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await run_in_threadpool(engine.start)
        await run_in_threadpool(netmon.start)
        yield
        netmon.stop()
        await run_in_threadpool(engine.stop)

    app = FastAPI(title="NovaTorrent", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.engine, app.state.auth, app.state.settings, app.state.qb = engine, auth, settings, qb

    @app.get("/api/health")
    def health() -> Response:
        # Intentionally unauthenticated and free of details: used by Docker/Uptime Kuma.
        if engine.ses is None:
            return JSONResponse({"status": "starting"}, status_code=503)
        if not engine.vpn_ok:
            return JSONResponse({"status": "vpn_down"}, status_code=503)
        return JSONResponse({"status": "ok"})

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

    def principal(request: Request) -> tuple[str, str] | None:
        hdr = request.headers.get("authorization", "")
        if hdr.lower().startswith("bearer "):
            role = auth.check_api_key(hdr[7:].strip())
            return ("apikey", role) if role else None
        sess = auth.get_session(request.cookies.get("SID"))
        return (sess[0], sess[1]) if sess else None

    @app.api_route("/api/v2/{section}/{name}", methods=["GET", "POST"])
    async def qb_dispatch(section: str, name: str, request: Request) -> Response:
        route = f"{section}/{name}"
        ip = request.client.host if request.client else "?"
        params = await read_params(request)
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

    return app
