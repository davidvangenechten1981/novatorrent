"""Real download + seed between two local engines, and resume after restart."""

import json
import shutil
import socket
import time
from pathlib import Path

from conftest import info_hash, make_torrent
from novatorrent.engine import Engine
from novatorrent.settings import Settings, State


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_engine(root: Path, name: str, port: int) -> Engine:
    cfg = root / name
    cfg.mkdir(parents=True)
    (cfg / "settings.json").write_text(
        json.dumps(
            {
                "save_path": str(root / f"{name}-dl"),
                "dht": False,
                "lsd": False,
                "upnp": False,
                "listen_port": port,
                "current_interface_address": "127.0.0.1",
                "nova_ipv6": False,
            }
        )
    )
    e = Engine(cfg, Settings(cfg), State(cfg))
    e.start()
    return e


def wait(cond, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.2)
    return False


def test_download_and_seed_between_instances(tmp_path):
    data, src = make_torrent(tmp_path / "origin")
    h = info_hash(data)
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    shutil.copy(src, seed_dir / src.name)
    pa, pb = free_port(), free_port()
    a, b = make_engine(tmp_path, "a", pa), make_engine(tmp_path, "b", pb)
    try:
        a.add(data=data, save_path=str(seed_dir), category="seed")
        assert wait(lambda: a.list_info()[0]["progress"] == 1.0), "seeder did not verify data"
        assert wait(lambda: a.list_info()[0]["state"] in ("stalledUP", "uploading"))

        b.add(data=data)

        def done() -> bool:
            if not b.peers(h):  # keep (re)connecting until the swarm link is up
                b.handle(h).connect_peer(("127.0.0.1", pa))
            return b.list_info()[0]["progress"] == 1.0

        assert wait(done, 60), "download did not finish"
        assert (tmp_path / "b-dl" / src.name).read_bytes() == src.read_bytes()
        assert wait(lambda: b.list_info()[0]["completion_on"] > 0)
        assert wait(lambda: a.list_info()[0]["uploaded"] > 0)
    finally:
        a.stop()
        b.stop()


def test_resume_after_restart_without_recheck(tmp_path):
    data, src = make_torrent(tmp_path / "origin")
    h = info_hash(data)
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    shutil.copy(src, seed_dir / src.name)
    a = make_engine(tmp_path, "a", free_port())
    a.add(data=data, save_path=str(seed_dir), category="cat", tags=["t1"])
    assert wait(lambda: a.list_info()[0]["progress"] == 1.0)
    a.stop()  # nice shutdown writes resume data
    assert (tmp_path / "a" / "resume" / f"{h}.fastresume").exists()

    cfg = tmp_path / "a"
    a2 = Engine(cfg, Settings(cfg), State(cfg))
    a2.start()
    try:
        # progress is restored from resume data immediately (no full recheck needed)
        info = a2.list_info()[0]
        assert info["hash"] == h and info["category"] == "cat" and info["tags"] == "t1"
        assert wait(lambda: a2.list_info()[0]["progress"] == 1.0, 10)
        assert a2.list_info()[0]["state"] not in ("checkingDL", "checkingUP")
    finally:
        a2.stop()


def test_move_on_complete(tmp_path):
    data, src = make_torrent(tmp_path / "origin")
    h = info_hash(data)
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    shutil.copy(src, seed_dir / src.name)
    pa, pb = free_port(), free_port()
    a, b = make_engine(tmp_path, "a", pa), make_engine(tmp_path, "b", pb)
    b.settings.update({"temp_path_enabled": True, "temp_path": str(tmp_path / "incomplete")})
    try:
        a.add(data=data, save_path=str(seed_dir))
        assert wait(lambda: a.list_info()[0]["progress"] == 1.0)
        b.add(data=data)

        def done() -> bool:
            if not b.peers(h):
                b.handle(h).connect_peer(("127.0.0.1", pa))
            return b.list_info()[0]["progress"] == 1.0

        assert wait(done, 60)
        final = tmp_path / "b-dl" / src.name
        assert wait(final.exists, 20), "file was not moved to the final path"
    finally:
        a.stop()
        b.stop()
