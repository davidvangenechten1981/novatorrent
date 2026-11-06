"""Radarr/Sonarr import by hardlink: the library link must not disturb seeding and must survive removal."""

import os
import shutil

from conftest import info_hash, make_torrent
from test_p2p import free_port, make_engine, wait


def test_hardlink_import_keeps_seeding_and_survives_delete(tmp_path):
    data, src = make_torrent(tmp_path / "origin")
    h = info_hash(data)
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    shutil.copy(src, seed_dir / src.name)
    pa, pb = free_port(), free_port()
    a, b = make_engine(tmp_path, "a", pa), make_engine(tmp_path, "b", pb)
    try:
        a.add(data=data, save_path=str(seed_dir))
        assert wait(lambda: a.list_info()[0]["progress"] == 1.0)
        b.add(data=data)

        def done() -> bool:
            handle = b.handle(h)  # may still be None right after add()
            if handle and not b.peers(h):  # keep (re)connecting until the swarm link is up
                handle.connect_peer(("127.0.0.1", pa))
            return b.list_info()[0]["progress"] == 1.0

        assert wait(done, 60), "download did not finish"
        downloaded = tmp_path / "b-dl" / src.name

        # what Radarr/Sonarr do on import: hardlink into the library (same volume)
        library = tmp_path / "library"
        library.mkdir()
        link = library / "Movie (2024).bin"
        try:
            os.link(downloaded, link)
        except OSError as e:  # filesystem without hardlink support
            import pytest

            pytest.skip(f"hardlinks not supported here: {e}")
        assert os.stat(downloaded).st_nlink == 2
        assert os.path.samefile(downloaded, link)

        # the torrent keeps seeding and is not rechecked or errored by the link
        assert wait(lambda: b.list_info()[0]["state"] in ("stalledUP", "uploading"))
        assert b.list_info()[0]["progress"] == 1.0

        # Cleanuparr/Sonarr "remove torrent + files": the library copy must stay intact
        assert b.remove(h, delete_files=True)
        assert wait(lambda: not downloaded.exists())
        assert wait(lambda: link.read_bytes() == src.read_bytes(), 5), "library copy incomplete"
    finally:
        a.stop()
        b.stop()
