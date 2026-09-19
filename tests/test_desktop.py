import ipaddress
import json
import socket

from fastapi.testclient import TestClient

from yt_transcript import desktop
from yt_transcript.app import create_app


def test_user_data_dir_is_absolute_and_named():
    d = desktop.user_data_dir()
    assert d.is_absolute()
    assert "ranscript" in d.name


def test_lan_addresses_are_routable_ipv4():
    for ip in desktop.lan_addresses():
        a = ipaddress.ip_address(ip)
        assert a.version == 4 and not a.is_loopback and not a.is_link_local


def test_pick_port_skips_busy_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        busy = s.getsockname()[1]
        s.listen(1)
        free = desktop.pick_port(busy, host="127.0.0.1")
        assert free != busy and free > busy


def test_desktop_endpoints_disabled_by_default(client):
    assert client.get("/api/desktop").json() == {"desktop": False}
    assert client.get("/api/desktop/qr.svg").status_code == 404
    assert client.post("/api/desktop/quit").status_code == 404
    assert client.get("/api/config").json()["desktop"] is False


def test_desktop_endpoints(settings, manager, monkeypatch):
    settings.desktop = True
    settings.port = 8123
    monkeypatch.setattr(desktop, "lan_addresses", lambda: ["192.168.1.5", "10.0.0.7"])
    quits = []
    app = create_app(settings=settings, manager=manager, quit_callback=lambda: quits.append(1))
    with TestClient(app) as c:
        d = c.get("/api/desktop").json()
        assert d["desktop"] is True and d["urls"] == ["http://192.168.1.5:8123", "http://10.0.0.7:8123"]
        assert d["app"] == "Transcript Tool" and d["port"] == 8123
        r = c.get("/api/desktop/qr.svg", params={"url": "http://10.0.0.7:8123"})
        assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
        assert r.text.lstrip().startswith("<svg")
        assert c.get("/api/desktop/qr.svg").status_code == 200  # defaults to the first address
        assert c.get("/api/desktop/qr.svg", params={"url": "http://evil.example"}).status_code == 400
        # TestClient's fake client address is not loopback, so quit is refused...
        assert c.post("/api/desktop/quit").status_code == 403 and not quits
    with TestClient(app, client=("127.0.0.1", 4321)) as c:
        assert c.post("/api/desktop/quit").json() == {"ok": True}
        assert quits == [1]


def test_selfcheck_runs_end_to_end(tmp_path):
    out = tmp_path / "report.json"
    assert desktop.selfcheck(out=str(out), with_whisper=False) == 0
    report = json.loads(out.read_text())
    assert report["ok"] and report["extractors"] > 100
    assert report["http"]["/healthz"] == 200 and report["http"]["/api/desktop"] == 200


def test_hostname_lookup_cannot_stall_a_request(monkeypatch):
    """A machine whose own name does not resolve must not hang the app.

    This failed CI on a macOS runner: /api/desktop took longer than ten
    seconds because getaddrinfo blocked on the local hostname.
    """
    import socket as socket_mod
    import time

    calls = []

    def hanging_getaddrinfo(*a, **kw):
        calls.append(a)
        time.sleep(30)
        raise AssertionError("should never be waited on")

    monkeypatch.setattr(socket_mod, "getaddrinfo", hanging_getaddrinfo)
    monkeypatch.setattr(desktop, "_address_cache", None)
    monkeypatch.setattr(desktop, "_HOSTNAME_LOOKUP_TIMEOUT", 0.2)

    started = time.monotonic()
    addresses = desktop.lan_addresses(use_cache=False)
    elapsed = time.monotonic() - started
    assert elapsed < 5, f"lan_addresses blocked for {elapsed:.1f}s"
    assert calls, "the hostname lookup should still be attempted"
    # The UDP-socket probe needs no resolver, so the routable address survives.
    for ip in addresses:
        assert not ipaddress.ip_address(ip).is_loopback


def test_addresses_are_cached_between_calls(monkeypatch):
    monkeypatch.setattr(desktop, "_address_cache", None)
    calls = []
    monkeypatch.setattr(desktop, "_hostname_addresses", lambda *a, **kw: calls.append(1) or [])
    first = desktop.lan_addresses()
    second = desktop.lan_addresses()
    assert first == second and len(calls) == 1, "the second call should come from the cache"
    desktop.lan_addresses(use_cache=False)
    assert len(calls) == 2, "use_cache=False must recompute"
