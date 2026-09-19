"""Desktop launcher.

Runs the server on this computer with sensible defaults (a per-user data
folder, a log file, the browser opened automatically) and tells the user how
to reach it from a phone on the same network. This is the entry point of the
packaged Mac/Windows/Linux builds.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import logging.handlers
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Optional

from . import __version__
from .config import Settings

APP_NAME = "Transcript Tool"
log = logging.getLogger(__name__)
_CONSOLE_LOGGER = __name__ + ".console"
_console_log = logging.getLogger(_CONSOLE_LOGGER)


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def user_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / APP_NAME
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "transcript-tool"


_HOSTNAME_LOOKUP_TIMEOUT = 1.0
_ADDRESS_CACHE_TTL = 10.0
_address_cache: Optional[tuple[float, list[str]]] = None
_address_lock = threading.Lock()


def _hostname_addresses(timeout: float = _HOSTNAME_LOOKUP_TIMEOUT) -> list[str]:
    """Addresses from resolving this machine's own name.

    ``getaddrinfo`` on the local hostname goes through the system resolver and
    can block for many seconds when the name does not resolve (a stock macOS
    machine asking mDNS, for instance). Run it on a throwaway thread and give
    up quickly, so a slow resolver can never stall a request.
    """
    result: list[str] = []

    def work() -> None:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                result.append(info[4][0])
        except (OSError, UnicodeError):
            pass

    t = threading.Thread(target=work, name="hostname-lookup", daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        log.debug("Hostname lookup did not finish within %.1fs; ignoring it", timeout)
        return []
    return result


def lan_addresses(use_cache: bool = True) -> list[str]:
    """This machine's IPv4 addresses that other devices can reach, best first.

    Cached briefly: it is read on every page load, and the hostname lookup can
    be slow. Addresses still follow the machine onto a new network within
    ``_ADDRESS_CACHE_TTL`` seconds.
    """
    global _address_cache
    now = time.monotonic()
    with _address_lock:
        cached = _address_cache
    if use_cache and cached and now - cached[0] < _ADDRESS_CACHE_TTL:
        return list(cached[1])

    found: list[str] = []

    def add(ip: str) -> None:
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            return
        if a.version != 4 or a.is_loopback or a.is_link_local or a.is_unspecified or a.is_multicast:
            return
        if ip not in found:
            found.append(ip)

    # connect() on a UDP socket sends nothing but makes the OS pick the
    # interface it would use for the default route, i.e. the Wi-Fi/LAN one.
    # This needs no name resolution, so it is both fast and the best guess.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 53))
            add(s.getsockname()[0])
    except OSError:
        pass
    # Then any other interface, so a machine on both Ethernet and Wi-Fi offers
    # the address the phone can actually reach.
    for ip in _hostname_addresses():
        add(ip)

    with _address_lock:
        _address_cache = (now, list(found))
    return found


def lan_urls(port: int) -> list[str]:
    return [f"http://{ip}:{port}" for ip in lan_addresses()]


def pick_port(preferred: int, host: str = "0.0.0.0", tries: int = 20) -> int:
    """Return ``preferred`` if free, otherwise the next free port after it."""
    for port in range(preferred, preferred + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
            except OSError:
                continue
            return port
    raise OSError(f"No free port between {preferred} and {preferred + tries - 1}")


def setup_logging(data_dir: Path, verbose: bool = False) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / "transcript-tool.log"
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    ]
    if sys.stderr is not None:  # windowed builds have no console
        console = logging.StreamHandler(sys.stderr)
        console.addFilter(lambda record: record.name != _CONSOLE_LOGGER)  # say() already printed it
        handlers.append(console)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    if not verbose:
        logging.getLogger("yt_transcript.youtube").setLevel(logging.WARNING)
    return log_path


def say(msg: str) -> None:
    """Print to the console when there is one, and always to the log file."""
    _console_log.info(msg)
    if sys.stdout is not None:
        try:
            print(msg, flush=True)
        except (OSError, ValueError):
            pass


class DesktopServer:
    """uvicorn in a background thread with a clean stop."""

    def __init__(self, settings: Settings) -> None:
        import uvicorn

        from .app import create_app

        self.settings = settings
        self.app = create_app(settings=settings, quit_callback=self.stop)
        self._server = uvicorn.Server(
            uvicorn.Config(self.app, host=settings.host, port=settings.port, log_level="info", log_config=None)
        )
        self._thread = threading.Thread(target=self._server.run, name="uvicorn", daemon=True)

    def start(self, timeout: float = 20.0) -> None:
        self._thread.start()
        deadline = time.monotonic() + timeout
        while not self._server.started:
            if not self._thread.is_alive():
                raise RuntimeError("Server failed to start; see the log file")
            if time.monotonic() > deadline:
                raise RuntimeError("Server did not start in time")
            time.sleep(0.05)

    def stop(self) -> None:
        self._server.should_exit = True

    def wait(self) -> None:
        while self._thread.is_alive():
            self._thread.join(0.5)

    def join(self, timeout: float = 10.0) -> None:
        self._thread.join(timeout)


def run_desktop(
    settings: Settings,
    port: Optional[int] = None,
    open_browser: bool = True,
    data_dir: Optional[str] = None,
    verbose: bool = False,
) -> int:
    settings.desktop = True
    settings.host = "0.0.0.0"
    if data_dir:
        settings.data_dir = Path(data_dir)
    elif not os.environ.get("DATA_DIR"):
        settings.data_dir = user_data_dir()
    log_path = setup_logging(settings.data_dir, verbose)
    try:
        settings.port = pick_port(port or settings.port or 8000)
    except OSError as e:
        say(f"Could not start: {e}")
        return 1

    server = DesktopServer(settings)
    try:
        server.start()
    except RuntimeError as e:
        say(f"Could not start: {e}")
        return 1

    local = f"http://127.0.0.1:{settings.port}/"
    phone = lan_urls(settings.port)  # also warms the address cache
    lines = [
        f"{APP_NAME} {__version__} is running.",
        f"  On this computer:  {local}",
        f"  On your phone:     {', '.join(phone) if phone else '(no network address found)'}   (same Wi-Fi)",
        f"  Transcripts:       {settings.data_dir}",
        f"  Log:               {log_path}",
        "Close this window or press Ctrl+C to stop.",
    ]
    say("\n".join(lines))
    if open_browser:
        try:
            webbrowser.open(local)
        except Exception:  # noqa: BLE001
            log.warning("Could not open a browser; open %s yourself", local)
    try:
        server.wait()
    except KeyboardInterrupt:
        say("Stopping…")
    finally:
        server.stop()
        server.join()
    return 0


# --------------------------------------------------------------------------- #
# Self check, used by CI to prove a packaged build actually works
# --------------------------------------------------------------------------- #


def selfcheck(out: Optional[str] = None, with_whisper: bool = True) -> int:
    """Start the app on a random port, hit its endpoints, and exercise the
    bundled libraries. Prints (and optionally writes) a JSON report."""
    from . import captions, urls, whisper_engine

    report: dict[str, Any] = {
        "app": APP_NAME,
        "version": __version__,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "frozen": is_frozen(),
        "errors": [],
    }

    def fail(msg: str) -> None:
        report["errors"].append(msg)

    try:
        import yt_dlp
        from yt_dlp.extractor import gen_extractor_classes

        report["yt_dlp"] = yt_dlp.version.__version__
        n = sum(1 for _ in gen_extractor_classes())
        report["extractors"] = n
        if n < 100:
            fail(f"only {n} yt-dlp extractors were bundled")
        # yt-dlp lists extractors lazily; make sure the real modules are bundled too.
        from yt_dlp.extractor.youtube import YoutubeIE, YoutubeTabIE

        if not YoutubeIE.suitable("https://www.youtube.com/watch?v=dQw4w9WgXcQ"):
            fail("YoutubeIE does not recognise a watch URL")
        if not YoutubeTabIE.suitable("https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"):
            fail("YoutubeTabIE does not recognise a playlist URL")
    except Exception as e:  # noqa: BLE001
        fail(f"yt-dlp import failed: {e!r}")

    try:
        assert urls.parse("https://youtu.be/dQw4w9WgXcQ").video_id == "dQw4w9WgXcQ"
        segs = captions.parse_json3({"events": [{"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "hi"}]}]})
        assert segs and segs[0].text == "hi"
    except Exception as e:  # noqa: BLE001
        fail(f"parsers broken: {e!r}")

    report["whisper"] = whisper_engine.available()
    if with_whisper and report["whisper"]:
        try:
            import ctranslate2

            report["ctranslate2"] = ctranslate2.__version__
            report["compute_types"] = sorted(ctranslate2.get_supported_compute_types("cpu"))
            import av

            report["av"] = av.__version__
        except Exception as e:  # noqa: BLE001
            fail(f"whisper native libraries failed: {e!r}")

    settings = Settings()
    settings.desktop = True
    settings.host = "127.0.0.1"
    settings.workers = 1
    import tempfile

    with tempfile.TemporaryDirectory(prefix="transcript-selfcheck-") as tmp:
        settings.data_dir = Path(tmp)
        try:
            settings.port = pick_port(18500, host="127.0.0.1")
            server = DesktopServer(settings)
            server.start()
        except Exception as e:  # noqa: BLE001
            fail(f"server failed to start: {e!r}")
            server = None
        if server is not None:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            base = f"http://127.0.0.1:{settings.port}"
            checks = {}
            for path in ("/healthz", "/api/config", "/", "/static/app.js", "/static/style.css", "/manifest.webmanifest", "/api/desktop"):
                try:
                    with opener.open(base + path, timeout=10) as r:
                        checks[path] = r.status
                        body = r.read()
                        if path == "/api/config":
                            report["config"] = json.loads(body)
                        if path == "/api/desktop":
                            d = json.loads(body)
                            report["lan_urls"] = d.get("urls")
                            if d.get("urls"):
                                qr = base + "/api/desktop/qr.svg?url=" + urllib.parse.quote(d["urls"][0], safe="")
                                with opener.open(qr, timeout=10) as r2:
                                    checks["/api/desktop/qr.svg"] = r2.status
                except Exception as e:  # noqa: BLE001
                    checks[path] = repr(e)
            report["http"] = checks
            bad = [p for p, s in checks.items() if s != 200]
            if bad:
                fail(f"endpoints failed: {bad}")
            server.stop()
            server.join()

    report["ok"] = not report["errors"]
    text = json.dumps(report, indent=2)
    if out:
        Path(out).write_text(text, encoding="utf-8")
    say(text)
    return 0 if report["ok"] else 1


import urllib.parse  # noqa: E402  (kept at the bottom to keep the top-level imports tidy)
