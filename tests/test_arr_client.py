import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from arr_client_check import run  # noqa: E402


def test_arr_client_flow(client):
    assert run(client) == []
