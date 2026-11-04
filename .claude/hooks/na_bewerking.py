"""PostToolUse hook: run `ruff check` on a Python file Claude just edited.

Problems are printed to stderr with exit code 2, so Claude sees them and fixes them.
Does nothing when the file is not Python, the project has no pyproject.toml, or ruff is missing.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    bestand = (data.get("tool_input") or {}).get("file_path", "")
    if not bestand.endswith(".py") or not Path(bestand).is_file():
        return 0
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or ".")
    if not (root / "pyproject.toml").exists():
        return 0
    if shutil.which("uv"):
        cmd = ["uv", "run", "ruff", "check", "--quiet", bestand]
    elif shutil.which("ruff"):
        cmd = ["ruff", "check", "--quiet", bestand]
    else:
        return 0
    try:
        res = subprocess.run(
            cmd, cwd=root, capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0
    if res.returncode != 0:
        print(f"ruff vindt problemen in {bestand}:\n{res.stdout}{res.stderr}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
