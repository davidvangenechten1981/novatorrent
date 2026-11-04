import json
import os
from pathlib import Path

import libtorrent as lt
import pytest
from fastapi.testclient import TestClient

from novatorrent.app import create_app

PASSWORD = "test-pass-123"  # fake test credential


def make_torrent(root: Path, name: str = "payload.bin", size: int = 2 * 1024 * 1024) -> tuple[bytes, Path]:
    """Create a local random file and a .torrent for it (no network, no copyrighted data)."""
    root.mkdir(parents=True, exist_ok=True)
    f = root / name
    f.write_bytes(os.urandom(size))
    fs = lt.file_storage()
    lt.add_files(fs, str(f))
    t = lt.create_torrent(fs, piece_size=65536)
    lt.set_piece_hashes(t, str(root))
    return lt.bencode(t.generate()), f


def info_hash(data: bytes) -> str:
    return str(lt.torrent_info(lt.bdecode(data)).info_hashes().v1)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVA_ADMIN_PASSWORD", PASSWORD)
    c = tmp_path / "config"
    c.mkdir()
    (c / "settings.json").write_text(
        json.dumps(
            {
                "save_path": str(tmp_path / "dl"),
                "dht": False,
                "lsd": False,
                "upnp": False,
                "random_port": True,
                "nova_ipv6": False,
            }
        )
    )
    return c


@pytest.fixture
def client(cfg):
    with TestClient(create_app(cfg)) as c:
        r = c.post("/api/v2/auth/login", data={"username": "admin", "password": PASSWORD})
        assert r.text == "Ok."
        yield c
