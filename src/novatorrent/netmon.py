"""Background monitor: VPN binding kill-switch, Gluetun forwarded-port file, IP filter refresh."""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .engine import Engine
from .ipfilter import load_ip_filter, update_from_url


def iface_present(name: str) -> bool:
    try:
        return name in {n for _, n in socket.if_nameindex()}
    except OSError:
        return False


def addr_present(addr: str) -> bool:
    """True if the local address is assigned to an interface (bind test, no traffic is sent)."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    fam = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
    try:
        with socket.socket(fam, socket.SOCK_DGRAM) as s:
            s.bind((str(ip), 0))
        return True
    except OSError:
        return False


class NetMonitor:
    """Fails closed: when the required binding is missing the whole session is paused."""

    def __init__(
        self,
        engine: Engine,
        iface_check: Callable[[str], bool] = iface_present,
        addr_check: Callable[[str], bool] = addr_present,
        interval: float = 5.0,
    ):
        self.e = engine
        self.iface_check, self.addr_check = iface_check, addr_check
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_filter = 0.0
        self.e.vpn_ok, self.e.vpn_reason = True, ""

    def start(self) -> None:
        self.tick()  # evaluate once before accepting traffic
        self._thread = threading.Thread(target=self._loop, name="netmon", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(3)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.tick()
            except Exception as ex:
                self.e.add_log(f"netmon error: {type(ex).__name__}", 8)

    def evaluate(self) -> tuple[bool, str]:
        s = self.e.settings.snapshot()
        iface, addr = s["current_network_interface"].strip(), s["current_interface_address"].strip()
        if s["nova_require_vpn_binding"] and not (iface or addr):
            return False, "VPN binding required but no interface/address configured"
        if iface and not self.iface_check(iface):
            return False, f"network interface '{iface}' is not available"
        if addr and not self.addr_check(addr):
            return False, f"address '{addr}' is not assigned to this host"
        return True, ""

    def tick(self) -> None:
        self._port_file()
        ok, reason = self.evaluate()
        if ok != self.e.vpn_ok or reason != self.e.vpn_reason:
            self.e.set_vpn_state(ok, reason)
        self._ip_filter()

    def _port_file(self) -> None:
        path = self.e.settings.get("nova_port_file")
        if not path:
            return
        try:
            port = int(Path(path).read_text().strip())
        except (OSError, ValueError):
            return
        if 1024 <= port <= 65535 and port != self.e.settings.get("listen_port"):
            self.e.settings.update({"listen_port": port, "random_port": False})
            self.e.apply_settings()
            self.e.add_log(f"Listen port updated from port file: {port}", 2)

    def _ip_filter(self) -> None:
        s = self.e.settings.snapshot()
        url, hours = s["nova_ip_filter_url"], max(int(s["nova_ip_filter_interval_h"]), 1)
        if url and s["ip_filter_enabled"] and time.time() - self._last_filter > hours * 3600:
            self._last_filter = time.time()
            dest = self.e.config_dir / "ipfilter.dat"
            try:
                if self.e.vpn_ok:  # never fetch outside the VPN binding
                    update_from_url(url, dest)
                    self.e.settings.update({"ip_filter_path": str(dest)})
                    self.e.apply_ip_filter()
            except Exception as ex:
                self.e.add_log(f"IP filter update failed: {type(ex).__name__}", 4)


__all__ = ["NetMonitor", "load_ip_filter"]
