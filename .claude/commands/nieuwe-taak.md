---
description: Start een nieuwe taak op een eigen branch
argument-hint: [korte-naam-van-de-taak]
allowed-tools: Bash(git status), Bash(git switch:*)
---

Maak een nieuwe branch voor deze taak: `git switch -c feature/$ARGUMENTS`.

Controleer eerst met `git status` dat er geen onbewaarde wijzigingen zijn. Zo wel: meld ze en stop. Meld daarna op welke branch we nu zitten.
