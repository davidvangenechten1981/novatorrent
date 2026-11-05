"""Hard-kill test: the process is killed without a clean shutdown; torrents must survive a restart."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

PASSWORD = "crash-test-pass-1"  # fake test credential
HASHES = [f"{i:040x}" for i in (0xA1, 0xA2, 0xA3)]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start(cfg: Path, port: int) -> subprocess.Popen:
    env = dict(
        os.environ,
        NOVA_CONFIG_DIR=str(cfg),
        NOVA_PORT=str(port),
        NOVA_BIND="127.0.0.1",
        NOVA_ADMIN_PASSWORD=PASSWORD,
    )
    return subprocess.Popen(
        [sys.executable, "-m", "novatorrent"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _login(port: int, proc: subprocess.Popen, timeout: float = 40.0) -> httpx.Client:
    c = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5)
    end = time.time() + timeout
    while time.time() < end:
        assert proc.poll() is None, "server exited during startup"
        try:
            r = c.post("/api/v2/auth/login", data={"username": "admin", "password": PASSWORD})
            if r.text == "Ok.":
                return c
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise AssertionError("server did not come up")


def test_kill_minus_nine_keeps_torrents(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "settings.json").write_text(
        json.dumps({"save_path": str(tmp_path / "dl"), "dht": False, "lsd": False, "upnp": False, "random_port": True})
    )
    port = _free_port()
    proc = _start(cfg, port)
    try:
        c = _login(port, proc)
        c.post("/api/v2/torrents/createCategory", data={"category": "sonarr", "savePath": ""})
        for h in HASHES:
            r = c.post(
                "/api/v2/torrents/add",
                data={"urls": f"magnet:?xt=urn:btih:{h}&dn=x", "paused": "true", "category": "sonarr"},
            )
            assert r.text == "Ok."
        c.close()
        # wait until resume data is on disk, then kill without any chance to shut down cleanly
        end = time.time() + 30
        while time.time() < end and len(list((cfg / "resume").glob("*.fastresume"))) < len(HASHES):
            time.sleep(0.3)
        written = len(list((cfg / "resume").glob("*.fastresume")))
    finally:
        proc.kill()  # TerminateProcess on Windows, SIGKILL elsewhere
        proc.wait(timeout=10)
    assert written == len(HASHES), "resume data was not written before the kill"

    # a half-written/corrupt resume file must not stop the next start
    (cfg / "resume" / ("f" * 40 + ".fastresume")).write_bytes(b"garbage")

    port2 = _free_port()
    proc2 = _start(cfg, port2)
    try:
        c2 = _login(port2, proc2)
        items = c2.get("/api/v2/torrents/info").json()
        assert sorted(i["hash"] for i in items) == sorted(HASHES)
        assert all(i["category"] == "sonarr" for i in items)
        c2.close()
    finally:
        proc2.kill()
        proc2.wait(timeout=10)
