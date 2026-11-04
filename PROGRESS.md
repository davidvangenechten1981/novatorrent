# PROGRESS
Branch: `build/novatorrent`. Resume by reading this file + DECISIONS.md.

| Fase | Status | Getest | Niet getest |
|---|---|---|---|
| 0 Onderzoek/keuzes | klaar | n.v.t. | |
| 1 Kern + API | klaar | 15 tests: qB-API contract (login, add, info, delete, categorieën, tags, sync/maindata, prefs), Radarr-achtig clientscript, echte download+seed tussen 2 lokale instanties, resume na nette stop, move-on-complete, brute-force lockout, readonly/API-key | kill -9 crash-herstel; echte Radarr/Sonarr; hardlinks (geen speciale code, zelfde volume nodig); honderden torrents; /torrents/info 'trackers_count', 'availability' zijn benaderingen |
| 2 Netwerk/privacy | todo | | |
| 3 Beheer/automatisering | todo | | |
| 4 Web-UI | todo | | |
| 5 Beveiliging/operatie | todo | | |

Notities:
- Docker-daemon draait niet in deze omgeving -> image-build staat in TODO-VOOR-DAVID.md.
