# SAMENVATTING — NovaTorrent

Branch `build/novatorrent` (niet gepusht). 59 tests groen, `ruff` schoon.

## Wat werkt (bewezen door tests)
- **Engine**: libtorrent via Python; echte download + seed tussen twee lokale instanties, resume na nette stop zonder hercontrole, move-on-complete.
- **qBittorrent-API v2**: login/cookie, version, preferences, torrents info/properties/files/trackers/add/delete/pause/resume/recheck/categorieën/tags/share-limits/filePrio/setLocation, transfer, sync/maindata (diffs), peers bannen. Clientscript doet de aanroepen van Radarr/Sonarr (`scripts/arr_client_check.py`).
- **Netwerk/privacy** (logica): binden aan interface/IP, fail-closed zonder binding, kill-switch pauzeert sessie, `/api/health` = 503 zonder VPN, poortbestand (Gluetun), IP-filter (eMule/PeerGuardian), anonymous mode, proxy, encryptie in de libtorrent-instellingen.
- **Beheer**: alt-speed planning, seedregels (ratio/tijd/inactief → pauzeren/verwijderen), schijfruimte-guard, trackerbeheer, extra trackers, RSS/Torznab met regex en dubbelcontrole, meldingen (Discord/ntfy/webhook), hook zonder shell (standaard uit).
- **Web-UI** (NL/EN, donker/licht, mobiel): handmatig in een browser gecontroleerd (login, lijst, instellingen, 390px); vertalingscontrole en JS-syntaxcheck als tests.
- **Beveiliging**: argon2, brute-force-blokkade, rollen + API-sleutels, CSRF, Host-check, LAN-only, rate-limit, versleutelde back-up.

## Deels / niet getest
- **Niet gedraaid**: Docker-build en container (Docker-daemon was uit), multi-arch, GitHub Actions, compose in Portainer.
- **Niet getest**: echte VPN/Gluetun en port-forward, UPnP, proxy-verbinding, lekken-test, echte Radarr/Sonarr/Prowlarr/Cleanuparr, Telegram, honderden torrents (prestaties), crash met `kill -9`, hardlinks.
- UI: toevoegen-dialoog, detail-tabs, RSS/gebruikers-schermen niet doorgeklikt.
- Ontbreekt bewust: land van peers (geen GeoIP), qBittorrent-eigen `/rss/*`-API, `.env.example` (geblokkeerd door je rechten; zie README).
- PEX-wijziging geldt pas na herstart.

## Bekende risico's
- De kill-switch is binding + sessie-pauze; zonder Gluetun-firewall is dat de enige laag. Test dit zelf (TODO 2).
- Statuscache van 0,8 s en één statuspoll per handle: bij honderden torrents mogelijk traag.
- Eén keer een flaky p2p-test gezien (opgelost met herhaald verbinden); op trage machines kan dit terugkomen.

## Bij thuiskomst (exacte stappen: `TODO-VOOR-DAVID.md`)
1. Docker Desktop starten, image bouwen en starten (TODO 4), healthcheck controleren.
2. (OWNER is al vervangen) `git push` na jouw akkoord, tag `v0.1.0`.
3. Stack in Portainer (README), eerste login, wachtwoord wijzigen.
4. Radarr/Sonarr koppelen (README), testdownload met een Linux-ISO.
5. Gluetun-variant en VPN-test (TODO 2 en 3).
