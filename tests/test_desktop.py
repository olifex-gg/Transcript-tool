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
