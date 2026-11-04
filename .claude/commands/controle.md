---
description: Draai tests en linter en geef een korte status
allowed-tools: Bash(uv run pytest:*), Bash(uv run ruff:*), Bash(git status), Bash(git diff:*)
---

Controleer het project:

1. Draai `uv run ruff check .` en `uv run ruff format --check .`.
2. Draai `uv run pytest`.
3. Bekijk `git status`.

Geef in het Nederlands een korte status: wat slaagt, wat faalt (met bestand en regel), en wat je voorstelt. Repareer niets zonder het eerst te melden.
