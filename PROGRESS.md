# PROGRESS
Branch: `build/novatorrent`. Resume by reading this file + DECISIONS.md.

| Fase | Status | Getest | Niet getest |
|---|---|---|---|
| 0 Onderzoek/keuzes | klaar | n.v.t. | |
| 1 Kern + API | klaar | 15 tests: qB-API contract (login, add, info, delete, categorieën, tags, sync/maindata, prefs), Radarr-achtig clientscript, echte download+seed tussen 2 lokale instanties, resume na nette stop, move-on-complete, brute-force lockout, readonly/API-key | kill -9 crash-herstel; echte Radarr/Sonarr; hardlinks (geen speciale code, zelfde volume nodig); honderden torrents; /torrents/info 'trackers_count', 'availability' zijn benaderingen |
| 2 Netwerk/privacy | klaar | 6 tests: interface/IP-binding strings, fail-closed zonder binding, anonymous mode/proxy/encryptie in settings pack, IP-filter (eMule+PeerGuardian, level-filter), kill-switch pauzeert sessie en herstelt, /api/health 503 zonder VPN, poortbestand (Gluetun) werkt listen_port bij | Echte VPN/Gluetun, port-forward, UPnP/NAT-PMP, proxy-verbinding, daadwerkelijk lekken-test; PEX aan/uit geldt pas na herstart; IP-filter auto-update (URL) niet getest; IPv6 in PeerGuardian-formaat niet ondersteund |
| 3 Beheer/automatisering | klaar | 11 tests: alt-speed planning (dag/uur, over middernacht), seed-regels (ratio/tijd/inactief; pauzeren/verwijderen+data), schijfruimte-guard met hysterese, extra trackers + tracker add/edit/remove, peer-ban (persistent), RSS (parse, Torznab magneturl, regex/dedupe S01E02, SSRF-blokkade 169.254), meldingen Discord/ntfy/webhook (lokale ontvanger), geheimen gemaskeerd in API, hook zonder shell met env-vars en standaard uit | Telegram (alleen code, geen test: vaste host api.telegram.org); echte Prowlarr; wachtrij-regels 'trage torrents' (alleen libtorrent-instelling, niet gemeten); land van peers ontbreekt (geen GeoIP-database); qB-eigen /rss/* API niet geimplementeerd (eigen /api/nova/rss) |
| 4 Web-UI | klaar | 7 tests: NL/EN sleutels identiek, alle gebruikte sleutels bestaan, alle instellingen hebben label, JS-syntaxcheck (node --check; vond een echte fout), geen innerHTML, CSP/headers; handmatige browsertest (Playwright): login, torrentlijst, instellingenpagina, mobiel 390px, geen console-fouten | Toevoegen-dialoog, detailpaneel-tabs, RSS/gebruikers-UI en grafieken zijn niet in een browser doorgeklikt; licht thema alleen gezien; back-up-knoppen wachten op fase 5 endpoints |
| 5 Beveiliging/operatie | todo | | |

Notities:
- Docker-daemon draait niet in deze omgeving -> image-build staat in TODO-VOOR-DAVID.md.
