# TODO VOOR DAVID
Dingen die ik niet zelf uitvoer of niet kon testen.

1. Docker Desktop starten en de image bouwen/testen:
   `docker build -t novatorrent:dev .` en `docker run --rm -p 8080:8080 novatorrent:dev`

2. VPN-test met Gluetun (na deploy): in de NovaTorrent-container `docker exec novatorrent wget -qO- ifconfig.me`
   moet het VPN-IP geven. Stop dan Gluetun (`docker stop gluetun`): `/api/health` moet 503 geven
   en er mag geen verkeer lekken. Zet `current_network_interface` op `tun0` en `nova_require_vpn_binding` op true
   (UI > Instellingen > Netwerk, of via `setPreferences`).
3. Gluetun port forwarding: laat Gluetun `VPN_PORT_FORWARDING_STATUS_FILE=/tmp/gluetun/forwarded_port` schrijven,
   mount die map read-only in NovaTorrent en zet `nova_port_file` op dat pad.
