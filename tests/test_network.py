from fastapi.testclient import TestClient

from conftest import PASSWORD
from novatorrent.app import create_app
from novatorrent.ipfilter import load_ip_filter, parse_lines
from novatorrent.netconf import build_settings_pack, listen_interfaces
from novatorrent.netmon import NetMonitor
from novatorrent.settings import DEFAULTS


def cfg(**kw):
    d = dict(DEFAULTS)
    d.update(kw)
    return d


def test_listen_binding():
    assert listen_interfaces(cfg(listen_port=5000, nova_ipv6=False)) == "0.0.0.0:5000"
    assert listen_interfaces(cfg(listen_port=5000, current_network_interface="tun0")) == "tun0:5000"
    assert listen_interfaces(cfg(listen_port=5000, current_interface_address="10.2.0.2")) == "10.2.0.2:5000"
    assert "[::]:5000" in listen_interfaces(cfg(listen_port=5000))
    # fail closed when a VPN binding is required but missing
    assert listen_interfaces(cfg(nova_require_vpn_binding=True)) == "127.0.0.1:0"


def test_pack_privacy_options():
    p = build_settings_pack(cfg(anonymous_mode=True, encryption=1, upnp=True))
    assert p["anonymous_mode"] is True and p["out_enc_policy"] == 0
    assert p["enable_upnp"] is False and p["enable_lsd"] is False  # forced off in anonymous mode
    p = build_settings_pack(cfg(proxy_type="SOCKS5", proxy_ip="10.0.0.1", proxy_port=1080, lsd=True))
    assert p["proxy_type"] == 2 and p["enable_lsd"] is False and p["proxy_peer_connections"] is True
    p = build_settings_pack(cfg(proxy_type="HTTP", proxy_username="u"))
    assert p["proxy_type"] == 5
    p = build_settings_pack(cfg(alt_speed_active=True, alt_dl_limit=100))
    assert p["download_rate_limit"] == 100 * 1024


def test_ip_filter_formats(tmp_path):
    text = (
        "# comment\n"
        "001.002.003.000 - 001.002.003.255 , 000 , bad range\n"
        "005.005.005.005 - 005.005.005.009 , 200 , allowed level\n"
        "Some Org:9.9.9.0-9.9.9.255\n"
        "garbage line\n"
    )
    assert parse_lines(text) == [("1.2.3.0", "1.2.3.255"), ("9.9.9.0", "9.9.9.255")]
    f = tmp_path / "f.dat"
    f.write_text(text)
    flt, n = load_ip_filter(f)
    assert n == 2
    assert flt.access("1.2.3.4") != 0 and flt.access("8.8.8.8") == 0


def test_monitor_kill_switch_and_recovery(cfg):
    from novatorrent.engine import Engine
    from novatorrent.settings import Settings, State

    s = Settings(cfg)
    s.update({"current_network_interface": "tun0", "nova_require_vpn_binding": True})
    e = Engine(cfg, s, State(cfg))
    e.start()
    present = {"tun0": False}
    m = NetMonitor(e, iface_check=lambda n: present[n], addr_check=lambda a: True)
    try:
        m.tick()
        assert e.vpn_ok is False and "tun0" in e.vpn_reason
        assert e.ses.is_paused()
        present["tun0"] = True
        m.tick()
        assert e.vpn_ok is True and not e.ses.is_paused()
    finally:
        e.stop()


def test_health_fails_without_vpn(cfg):
    with TestClient(create_app(cfg)) as c:
        assert c.get("/api/health").status_code == 200
        c.app.state.engine.set_vpn_state(False, "test")
        assert c.get("/api/health").status_code == 503
        c.post("/api/v2/auth/login", data={"username": "admin", "password": PASSWORD})
        assert c.get("/api/v2/transfer/info").status_code == 200  # API stays reachable
        c.app.state.engine.set_vpn_state(True, "")
        assert c.get("/api/health").status_code == 200


def test_port_file_updates_listen_port(cfg, tmp_path):
    from novatorrent.engine import Engine
    from novatorrent.settings import Settings, State

    pf = tmp_path / "forwarded_port"
    pf.write_text("51234\n")
    s = Settings(cfg)
    s.update({"nova_port_file": str(pf)})
    e = Engine(cfg, s, State(cfg))
    e.start()
    try:
        NetMonitor(e).tick()
        assert s.get("listen_port") == 51234
        pf.write_text("not-a-port")
        NetMonitor(e).tick()
        assert s.get("listen_port") == 51234
    finally:
        e.stop()
