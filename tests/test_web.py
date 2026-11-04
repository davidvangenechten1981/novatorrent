import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).parent.parent / "src" / "novatorrent" / "web"
DICT = json.loads((WEB / "i18n.json").read_text(encoding="utf-8"))
JS = sorted(WEB.glob("*.js"))


def test_languages_have_identical_keys():
    en, nl = set(DICT["en"]), set(DICT["nl"])
    assert en == nl, f"missing in nl: {sorted(en - nl)}; missing in en: {sorted(nl - en)}"
    assert all(DICT["nl"][k].strip() for k in nl)


def test_all_literal_translation_keys_exist():
    used = set()
    for f in JS:
        used |= set(re.findall(r'\bt\("([a-zA-Z0-9_.]+)"\)', f.read_text(encoding="utf-8")))
        used |= set(
            re.findall(
                r'"((?:tb|trk|peer|file|add|common|bar|stats|logs|help|set|nav|login|det|rss|users|keys|backup|del|flt)\.[a-zA-Z0-9_.]+)"',
                f.read_text(encoding="utf-8"),
            )
        )
    missing = sorted(k for k in used if not k.endswith(".") and k not in DICT["en"])
    assert not missing, missing


def test_dynamic_key_families_complete():
    en = DICT["en"]
    states = [
        "downloading",
        "stalledDL",
        "forcedDL",
        "queuedDL",
        "pausedDL",
        "metaDL",
        "forcedMetaDL",
        "uploading",
        "stalledUP",
        "forcedUP",
        "queuedUP",
        "pausedUP",
        "checkingDL",
        "checkingUP",
        "checkingResumeData",
        "allocating",
        "moving",
        "error",
        "missingFiles",
    ]
    assert all(f"state.{s}" in en for s in states)
    schema = (WEB / "settings.js").read_text(encoding="utf-8")
    keys = re.findall(r'\["([a-z_0-9]+)", (?:[BITF]|DAYS|sel|\{)', schema)
    assert len(keys) > 60
    from novatorrent.settings import DEFAULTS

    for k in keys:
        assert f"set.{k}" in en, k
        assert k in DEFAULTS, k
    for sec in re.findall(r"^  (\w+): \[\[", schema, re.M):
        assert f"sec.{sec}" in en, sec
    for col in re.findall(
        r'"(\w+)"',
        re.search(r"const ALL_COLS = \[(.*?)\]", (WEB / "app.js").read_text(encoding="utf-8"), re.S).group(1),
    ):
        assert f"col.{col}" in en, col


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("f", JS, ids=lambda p: p.name)
def test_js_syntax(f):
    r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_no_innerhtml_in_ui():
    for f in JS:
        assert "innerHTML" not in f.read_text(encoding="utf-8"), f.name


def test_static_ui_served_and_headers(client):
    r = client.get("/")
    assert r.status_code == 200 and "NovaTorrent" in r.text
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert client.get("/app.js").status_code == 200
    assert client.get("/i18n.json").json()["nl"]["login.title"] == "Inloggen"
