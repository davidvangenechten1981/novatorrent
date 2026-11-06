"""NovaTorrent's own JSON API (/api/nova/*) used by the web-UI: RSS, peers, users, logs, stats."""

from __future__ import annotations

import ipaddress
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from . import __version__
from .backup import BackupError, export_config, import_config


def build_router(ctx: Any) -> APIRouter:
    r = APIRouter(prefix="/api/nova")
    e, auth = ctx.engine, ctx.auth

    def who(request: Request, admin: bool = False, write: bool = False) -> tuple[str, str]:
        p = ctx.principal(request)
        if not p:
            raise HTTPException(401, "Unauthorized")
        if admin and p[1] != "admin":
            raise HTTPException(403, "Forbidden")
        if write and request.method != "GET":
            if p[1] != "admin":
                raise HTTPException(403, "Forbidden")
            if not ctx.csrf_ok(request):
                raise HTTPException(403, "CSRF check failed")
        return p

    @r.get("/session")
    def session(request: Request):
        p = who(request)
        sess = auth.get_session(request.cookies.get("SID"))
        return {"user": p[0], "role": p[1], "csrf": sess[2] if sess else "", "version": __version__}

    @r.get("/status")
    def status(request: Request):
        who(request)
        return e.server_state()

    @r.get("/stats")
    def stats(request: Request):
        who(request)
        infos = e.list_info()
        by_state: dict[str, int] = {}
        for i in infos:
            by_state[i["state"]] = by_state.get(i["state"], 0) + 1
        st = e.server_state()
        return {
            "torrents": len(infos),
            "by_state": by_state,
            "alltime_dl": st["alltime_dl"],
            "alltime_ul": st["alltime_ul"],
            "global_ratio": st["global_ratio"],
            "free_space": st["free_space_on_disk"],
            "peers": st["total_peer_connections"],
        }

    @r.get("/logs")
    def logs(request: Request, last: int = -1):
        who(request)
        return [x for x in e.logs if x["id"] > last]

    # ---- peers ----------------------------------------------------------------------------
    @r.get("/peers")
    def peers(request: Request, hash: str):
        who(request)
        return list(e.peers(hash.lower()).values())

    @r.post("/ban")
    async def ban(request: Request):
        who(request, write=True)
        ip = (await request.json()).get("ip", "")
        try:
            await run_in_threadpool(e.ban_peer, ip)
        except ValueError:
            raise HTTPException(400, "invalid IP") from None
        return {"ok": True}

    @r.get("/banned")
    def banned(request: Request):
        who(request)
        return e.state.get("banned", [])

    @r.post("/unban")
    async def unban(request: Request):
        who(request, write=True)
        ip = str((await request.json()).get("ip", ""))
        with e.state.lock:
            lst = e.state.data.get("banned", [])
            if ip in lst:
                lst.remove(ip)
        e.state.save()
        e.apply_ip_filter()
        for b in e.state.get("banned", []):
            e.ban_peer(b, persist=False)
        return {"ok": True}

    # ---- RSS ------------------------------------------------------------------------------
    @r.get("/rss")
    def rss_get(request: Request):
        who(request)
        d = ctx.rss.data()
        return {"feeds": d["feeds"], "rules": d["rules"]}

    @r.post("/rss/feeds")
    async def rss_add_feed(request: Request):
        who(request, write=True)
        b = await request.json()
        try:
            fid = ctx.rss.add_feed(str(b.get("name", "")), str(b.get("url", "")), int(b.get("interval_min", 30)))
        except ValueError as ex:
            raise HTTPException(400, str(ex)) from None
        return {"id": fid}

    @r.delete("/rss/feeds/{fid}")
    def rss_del_feed(request: Request, fid: str):
        who(request, write=True)
        with e.state.lock:
            ctx.rss.data()["feeds"].pop(fid, None)
        e.state.save()
        return {"ok": True}

    @r.post("/rss/rules")
    async def rss_add_rule(request: Request):
        who(request, write=True)
        try:
            rid = ctx.rss.add_rule(await request.json())
        except Exception as ex:
            raise HTTPException(400, f"invalid rule: {type(ex).__name__}") from None
        return {"id": rid}

    @r.delete("/rss/rules/{rid}")
    def rss_del_rule(request: Request, rid: str):
        who(request, write=True)
        with e.state.lock:
            ctx.rss.data()["rules"].pop(rid, None)
        e.state.save()
        return {"ok": True}

    @r.post("/rss/run/{fid}")
    async def rss_run(request: Request, fid: str):
        who(request, write=True)
        return {"added": await run_in_threadpool(ctx.rss.run_feed, fid)}

    # ---- search (Torznab indexers) ----------------------------------------------------------
    @r.get("/search/indexers")
    def search_indexers(request: Request):
        who(request, admin=True)
        return ctx.search.list_indexers()  # never contains API keys

    @r.post("/search/indexers")
    async def search_add_indexer(request: Request):
        who(request, write=True)
        b = await request.json()
        try:
            iid = ctx.search.add_indexer(str(b.get("name", "")), str(b.get("url", "")), str(b.get("apikey", "")))
        except ValueError as ex:
            raise HTTPException(400, str(ex)) from None
        return {"id": iid}

    @r.delete("/search/indexers/{iid}")
    def search_del_indexer(request: Request, iid: str):
        who(request, write=True)
        if not ctx.search.remove_indexer(iid):
            raise HTTPException(404, "unknown indexer")
        return {"ok": True}

    @r.post("/search/indexers/{iid}/test")
    async def search_test_indexer(request: Request, iid: str):
        who(request, write=True)
        try:
            return await run_in_threadpool(ctx.search.test, iid)
        except KeyError:
            raise HTTPException(404, "unknown indexer") from None

    @r.get("/search")
    async def search_run(request: Request, q: str = "", indexers: str = "", limit: int = 100):
        who(request, admin=True)
        ids = [i for i in indexers.split(",") if i]
        try:
            return await run_in_threadpool(ctx.search.search, q, ids, limit)
        except ValueError as ex:
            raise HTTPException(400, str(ex)) from None

    @r.post("/search/add")
    async def search_add(request: Request):
        who(request, write=True)
        b = await request.json()
        try:
            h = await run_in_threadpool(
                ctx.search.add_result, str(b.get("rid", "")), str(b.get("category", "")), bool(b.get("paused"))
            )
        except KeyError:
            raise HTTPException(404, "result expired, search again") from None
        except Exception as ex:
            raise HTTPException(400, f"add failed: {type(ex).__name__}") from None
        return {"hash": h}

    # ---- notifications ----------------------------------------------------------------------
    @r.post("/notify/test")
    async def notify_test(request: Request):
        who(request, write=True)
        done = await run_in_threadpool(ctx.notifier.send_all, "completed", {"name": "test notification"})
        return {"sent_via": done}

    # ---- users / api keys (admin) -----------------------------------------------------------
    @r.get("/users")
    def users(request: Request):
        who(request, admin=True)
        return auth.list_users()

    @r.post("/users")
    async def users_set(request: Request):
        who(request, write=True)
        b = await request.json()
        try:
            await run_in_threadpool(
                auth.set_user, str(b.get("user", "")), str(b.get("password", "")), str(b.get("role", "readonly"))
            )
        except ValueError as ex:
            raise HTTPException(400, str(ex)) from None
        return {"ok": True}

    @r.delete("/users/{name}")
    def users_del(request: Request, name: str):
        who(request, write=True)
        if not auth.delete_user(name):
            raise HTTPException(400, "cannot delete (unknown user or last admin)")
        return {"ok": True}

    @r.get("/apikeys")
    def keys(request: Request):
        who(request, admin=True)
        return auth.list_api_keys()

    @r.post("/apikeys")
    async def keys_add(request: Request):
        who(request, write=True)
        b = await request.json()
        try:
            token = auth.create_api_key(str(b.get("name", "key")), str(b.get("role", "readonly")))
        except ValueError as ex:
            raise HTTPException(400, str(ex)) from None
        return {"token": token}  # shown once

    @r.delete("/apikeys/{kid}")
    def keys_del(request: Request, kid: str):
        who(request, write=True)
        if not auth.delete_api_key(kid):
            raise HTTPException(404, "unknown key")
        return {"ok": True}

    # ---- backup ---------------------------------------------------------------------------
    @r.post("/backup/export")
    async def backup_export(request: Request):
        who(request, write=True)
        pw = str((await request.json()).get("password", ""))
        await run_in_threadpool(e.save_all_resume, 10.0)  # fresh resume data first
        try:
            blob = await run_in_threadpool(export_config, e.config_dir, pw)
        except BackupError as ex:
            raise HTTPException(400, str(ex)) from None
        return Response(blob, media_type="application/octet-stream")

    @r.post("/backup/import")
    async def backup_import(request: Request):
        who(request, write=True)
        form = await request.form()
        up = form.get("file")
        if up is None or not hasattr(up, "read"):
            raise HTTPException(400, "file missing")
        try:
            names = await run_in_threadpool(import_config, e.config_dir, str(form.get("password", "")), await up.read())
        except BackupError as ex:
            raise HTTPException(400, str(ex)) from None
        return {"restored": len(names), "restart_required": True}

    _ = ipaddress
    return r
