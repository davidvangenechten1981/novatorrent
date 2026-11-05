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

4. Image bouwen en testen (Docker Desktop starten): 
   `docker build -t novatorrent:dev .` en
   `docker run --rm -p 8080:8080 -e NOVA_ADMIN_PASSWORD=kies-iets-sterks -v ${PWD}/cfg:/config -v ${PWD}/dat:/data novatorrent:dev`
   Daarna `docker ps` (status moet "healthy" worden) en open http://localhost:8080.
   Test ook de veilige opties: voeg `--read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true --user 1026:100` toe.
5. Multi-arch: `docker buildx build --platform linux/amd64,linux/arm64 -t novatorrent:test .` (controleert o.a. dat het libtorrent-wiel voor arm64 bestaat).
6. GitHub (pas na jouw akkoord pushen): `git push -u origin build/novatorrent`, daarna een tag `v0.1.0` voor de image-workflow.
   Zet Dependabot aan in de repo-instellingen.
7. Radarr/Sonarr koppelen en een echte download testen: zie README, stap-voor-stap.
8. Kies een sterk wachtwoord, verwijder `initial_password.txt`, maak een aparte gebruiker voor de arr-apps.
9. Gebruik een openbare testtorrent (bijv. een Linux-ISO) voor de eerste echte download.
