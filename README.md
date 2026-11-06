# NovaTorrent

Eigen torrent-downloader (libtorrent) als Docker-container, met web-UI (NL/EN) en een
qBittorrent-WebUI-v2-compatibele API, zodat Radarr, Sonarr, Lidarr, Prowlarr-flows en Cleanuparr hem als
"qBittorrent" kunnen gebruiken.

> Status: gebouwd en getest op Windows met echte libtorrent-sessies. De Docker-image, VPN/Gluetun,
> port-forwarding en de koppeling met echte Radarr/Sonarr zijn **nog niet in praktijk getest**:
> zie `TODO-VOOR-DAVID.md` en `SAMENVATTING.md`.

## Installatie in Portainer

1. Image bouwen en publiceren: tag `vX.Y.Z` pushen op GitHub (workflow `docker.yml`). **Let op:** het image heet dan
   `X.Y.Z` (zonder `v`), bijvoorbeeld `ghcr.io/davidvangenechten1981/novatorrent:0.1.2`. Of lokaal:
   `docker build -t novatorrent:latest .` en `NOVA_IMAGE=novatorrent:latest` gebruiken.
2. Portainer > Stacks > Add stack > plak `docker-compose.yml` (zonder VPN) of `docker-compose.gluetun.yml`.
3. Variabelen invullen (Environment variables):

| Variabele | Standaard | Uitleg |
|---|---|---|
| `PUID` / `PGID` | 1026 / 100 | gebruiker/groep die de bestanden schrijft (NAS) |
| `TZ` | Europe/Brussels | tijdzone |
| `WEBUI_PORT` | 8080 | poort van de web-UI |
| `BT_PORT` | 6881 | BitTorrent-poort (TCP+UDP) |
| `CONFIG_PATH` | ./config | map voor instellingen, resume-data (`/config`) |
| `DATA_PATH` | ./data | downloadmap (`/data`); zet Radarr/Sonarr op **hetzelfde volume** voor hardlinks |
| `NOVA_IMAGE` | ghcr.io/davidvangenechten1981/novatorrent:latest | image dat de GitHub-workflow publiceert |
| `NOVA_ADMIN_PASSWORD` | leeg | eerste wachtwoord; leeg = willekeurig, in `CONFIG_PATH/initial_password.txt` |
| `NOVA_ALLOWED_HOSTS` | leeg | hostnamen waarmee je de UI opent (bescherming tegen DNS-rebinding) |
| `NOVA_METRICS` | 0 | `1` zet `/metrics` (Prometheus) aan; vereist inloggen of API-sleutel |
| `NOVA_AUTH_DISABLED` | 0 | `1` = geen inlog voor directe LAN-verzoeken. Weigert te starten samen met `NOVA_ALLOW_WAN`; verzoeken via een reverse proxy (`X-Forwarded-*`) moeten wel inloggen; het completion-script is dan uit. Houd `NOVA_ALLOWED_HOSTS` gevuld. |
| `NOVA_RATE_LIMIT` | 30 | aanvragen per seconde per IP |
| `NOVA_ALLOW_WAN` | niet gezet | standaard weigert NovaTorrent clients buiten het LAN; `1` heft dat op |

Zorg dat `CONFIG_PATH` en `DATA_PATH` schrijfbaar zijn voor `PUID:PGID`. Open daarna `http://NAS:8080`,
log in als `admin` en **verander het wachtwoord** (Instellingen > Gebruikers). Verwijder `initial_password.txt`.

De container draait niet als root, met `cap_drop: ALL`, `no-new-privileges` en een read-only rootfs.
Daarom wordt `PUID/PGID` via `user:` in compose gezet in plaats van een root-entrypoint.

## VPN-opzet (Gluetun)

Gebruik `docker-compose.gluetun.yml`. NovaTorrent deelt de netwerkstack van Gluetun
(`network_mode: "service:gluetun"`); de poorten staan daarom op de **gluetun**-service.
- `NOVA_BIND_INTERFACE=tun0` + `NOVA_REQUIRE_VPN=1`: NovaTorrent bindt aan de tunnel. Verdwijnt `tun0`,
  dan pauzeert de hele sessie, toont de UI "VPN: WEG" en geeft `/api/health` HTTP 503.
- `LAN_SUBNET` zet `FIREWALL_OUTBOUND_SUBNETS`, nodig om Prowlarr/Radarr op je LAN te bereiken.
- Port-forwarding: zet `VPN_PORT_FORWARDING=on` (provider moet dit ondersteunen). Gluetun schrijft de poort naar
  `/gluetun/forwarded_port`; NovaTorrent leest dat bestand en past de luisterpoort automatisch aan.
  Alternatief: `POST /api/v2/app/setPreferences` met `json={"listen_port":12345}` en een API-sleutel (`Authorization: Bearer nt_...`).
- Controle: `docker exec novatorrent python -c "import urllib.request;print(urllib.request.urlopen('https://ifconfig.me').read())"`
  moet het VPN-IP geven.

## Koppelen met Radarr / Sonarr / Lidarr (stap voor stap)

1. Radarr > Settings > Download Clients > **+** > **qBittorrent**.
2. Host: IP van de NAS (of `gluetun` als Radarr in hetzelfde Docker-netwerk zit). Poort: `8080`. SSL uit.
3. Gebruikersnaam/wachtwoord: een NovaTorrent-gebruiker (maak er een met rol "Beheren" voor de arr-apps).
4. Category: `radarr` (Sonarr: `sonarr`, Lidarr: `lidarr`). De categorie wordt automatisch aangemaakt.
5. **Test** en **Save**. Controle zonder arr-app: `NOVA_URL=http://NAS:8080 NOVA_USER=admin NOVA_PASS=... uv run python scripts/arr_client_check.py`.
6. Remote path mapping nodig? Alleen als Radarr de map `/data` anders ziet dan NovaTorrent. Laat beide `/data` mounten.
7. Cleanuparr: kies "qBittorrent" als client met dezelfde gegevens.
8. Homepage-widget: `type: qbittorrent`, `url`, `username`, `password` (of een view-only gebruiker). Uptime Kuma: HTTP-monitor op `/api/health`.

## Zoeken in je indexers (Torznab)

NovaTorrent kan zoeken in je eigen Torznab-indexers (bijvoorbeeld die van Prowlarr) en een resultaat met één klik toevoegen.
1. Prowlarr: Indexers > menu van een indexer > **Copy Torznab Feed** voor de URL (`…/<id>/api`). De API-sleutel staat onder
   Settings > General.
2. NovaTorrent: Instellingen > **Zoek-indexers (Torznab)** > naam, URL en sleutel invullen > Toevoegen > **Testen**.
3. Knop **Zoeken** bovenin: zoekterm, kies indexers en categorie, **Toevoegen**.

De API-sleutel en de download-URL's (daar zit vaak een sleutel in) blijven op de server: de browser krijgt alleen een korte
resultaat-id. Alleen beheerders kunnen zoeken. Interne adressen zoals loopback en link-local worden geblokkeerd; LAN-adressen mogen.

## Rollen, sleutels en beveiliging

- Rollen: **Beheren** (alles) en **Alleen kijken** (alleen lees-endpoints). API-sleutels hebben dezelfde rollen en worden
  maar één keer getoond (alleen de hash wordt bewaard).
- Wachtwoorden: argon2id. Na 5 foute logins blokkeert het IP-adres (oplopend, max. 1 uur).
- Alleen LAN (RFC1918, loopback, link-local, 100.64/10) standaard. Rate-limit per IP. CSP en andere beveiligingsheaders.
- Webhook-URL's en tokens worden nooit teruggestuurd naar de browser (`***`).
- Het completion-script is standaard uit en draait zonder shell; gegevens komen via `NOVA_*`-omgevingsvariabelen.

## Back-up

- In de UI: Instellingen > Back-up > exporteren met wachtwoord (`.ntbak`, AES-256-GCM, scrypt). Bevat instellingen,
  categorieën, RSS en resume-data; **niet** gebruikers/API-sleutels. Importeren vereist een herstart van de container.
- Handmatig: stop de container en kopieer de map `CONFIG_PATH` (bijv. naar `\\davidnas2020\claude\backups`).

## Probleemoplossing

| Probleem | Oplossing |
|---|---|
| UI zegt "VPN: WEG", health 503 | `tun0` bestaat niet: controleer Gluetun (`docker logs gluetun`). Na herstel hervat NovaTorrent vanzelf. |
| Radarr "Unable to connect" | Poort/host fout, of Radarr zit buiten het LAN-bereik; controleer `NOVA_ALLOWED_HOSTS`. |
| Radarr/Sonarr krijgt 403 | Verkeerde gebruiker/wachtwoord, of IP tijdelijk geblokkeerd (5 foute pogingen). |
| Geen schrijfrechten | `CONFIG_PATH`/`DATA_PATH` moeten schrijfbaar zijn voor `PUID:PGID`. |
| Hardlinks werken niet | Downloads en bibliotheek moeten op hetzelfde volume/dezelfde mount staan. |
| Geen verbindingen | Poort-forward ontbreekt: gebruik Gluetun-port-forwarding of zet UPnP aan (zonder VPN). |
| Downloads pauzeren vanzelf | Vrije schijfruimte onder de drempel (Instellingen > Downloads). |
| PEX-wijziging heeft geen effect | PEX-instelling geldt pas na herstart. |

## Ontwikkelen

```powershell
uv sync --python 3.12
uv run ruff check .
uv run pytest
$env:NOVA_CONFIG_DIR="$PWD\.devconfig"; $env:NOVA_ADMIN_PASSWORD="dev-only-pass"; $env:PYTHONPATH="src"; uv run python -m novatorrent
```
Python 3.12 of 3.13 (libtorrent-wielen). Architectuur: zie `DECISIONS.md`.
