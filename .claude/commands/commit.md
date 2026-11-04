---
description: Maak een nette commit van de huidige wijzigingen (zonder te pushen)
allowed-tools: Bash(git status), Bash(git diff:*), Bash(git add:*), Bash(git commit:*), Bash(uv run pytest:*), Bash(uv run ruff:*)
---

1. Draai `uv run ruff check .` en `uv run pytest`. Bij een fout: stop en meld het.
2. Bekijk `git status` en `git diff`. Voeg nooit `.env`, geheimen of rapporten met persoonlijke paden toe.
3. Stel een korte commitmelding voor in het Nederlands (één regel, gebiedende wijs).
4. Voer `git add` voor de bedoelde bestanden en `git commit` uit.

Push niet. Vraag mij daarvoor apart.
