"""Design-system guard rails: colour contrast of the CSS tokens, icon names, PWA manifest."""

import json
import re
from pathlib import Path

import pytest

WEB = Path(__file__).parent.parent / "src" / "novatorrent" / "web"
CSS = (WEB / "style.css").read_text(encoding="utf-8")


def tokens(selector: str) -> dict[str, str]:
    """CSS custom properties declared in the block that starts with `selector {`."""
    m = re.search(re.escape(selector) + r"\s*\{(.*?)\n\}", CSS, re.S)
    assert m, selector
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})\b", m.group(1)))


DARK = tokens(":root")
LIGHT = {**DARK, **tokens(':root[data-theme="light"]')}  # light overrides dark; shared tokens are inherited


def lum(hex_color: str) -> float:
    rgb = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("name,theme", [("dark", DARK), ("light", LIGHT)])
def test_text_tokens_meet_wcag_aa(name, theme):
    for fg in ("--text", "--text-2", "--text-3"):
        for bg in ("--bg", "--surface", "--surface-2"):
            assert contrast(theme[fg], theme[bg]) >= 4.5, f"{name}: {fg} on {bg} = {contrast(theme[fg], theme[bg]):.2f}"
    # status/accent text is used on tinted pills and on the surface
    for fg in ("--accent", "--ok", "--warn", "--bad", "--dl", "--queue"):
        assert contrast(theme[fg], theme["--surface"]) >= 4.5, (
            f"{name}: {fg} = {contrast(theme[fg], theme['--surface']):.2f}"
        )
    assert contrast(theme["--accent-ink"], theme["--accent-btn"]) >= 4.5, f"{name}: primary button label"


@pytest.mark.parametrize("name,theme", [("dark", DARK), ("light", LIGHT)])
def test_chart_series_have_3_to_1_against_the_surface(name, theme):
    for s in ("--s1", "--s2"):
        assert contrast(theme[s], theme["--surface"]) >= 3.0, f"{name}: {s}"


def test_every_icon_used_in_js_exists():
    defined = set(re.findall(r"^  (\w+): \[", (WEB / "icons.js").read_text(encoding="utf-8"), re.M))
    assert len(defined) > 30
    used: set[str] = set()
    for f in WEB.glob("*.js"):
        text = f.read_text(encoding="utf-8")
        used |= set(re.findall(r'icon\("(\w+)"', text))  # icon("name")
        used |= set(re.findall(r'icon:\s*"(\w+)"', text))  # menu items: { icon: "play" }
    app = (WEB / "app.js").read_text(encoding="utf-8")
    for block in re.findall(r"const (?:VIEWS|STATUS) = \[(.*?)\];", app, re.S):  # tables: [id, icon, ...]
        used |= set(re.findall(r'\["\w+",\s*"(\w+)"', block))
    assert used, "no icon usage found"
    assert not sorted(used - defined), sorted(used - defined)


def test_manifest_and_icon_files():
    manifest = json.loads((WEB / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert manifest["name"] == "NovaTorrent" and manifest["display"] == "standalone"
    for icon in manifest["icons"]:
        assert (WEB / icon["src"]).exists()
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert 'rel="manifest"' in html and 'name="theme-color"' in html and "viewport-fit=cover" in html


def test_no_inline_scripts_or_handlers_in_html():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert "<script>" not in html and not re.search(r"\son\w+=", html)  # CSP: script-src 'self'
