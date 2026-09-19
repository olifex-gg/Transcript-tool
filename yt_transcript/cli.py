"""Command line entry points.

    yt-transcript serve                      # run the web app
    yt-transcript URL [URL ...] [options]    # transcribe from a terminal
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .config import Settings
from .formats import FORMATS, filename_for, render
from .models import TranscriptError
from .transcribe import ENGINES


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="yt-transcript", description="Transcribe YouTube videos and playlists.")
    p.add_argument("--version", action="version", version=f"yt-transcript {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="show yt-dlp / whisper logging")
    sub = p.add_subparsers(dest="command")

    s = sub.add_parser("serve", help="run the web app (default: http://0.0.0.0:8000)")
    s.add_argument("--host", default=None)
    s.add_argument("--port", type=int, default=None)
    s.add_argument("--reload", action="store_true", help=argparse.SUPPRESS)

    g = sub.add_parser("get", help="transcribe one or more URLs to files")
    g.add_argument("urls", nargs="+", metavar="URL")
    g.add_argument("-l", "--language", default=None, help='preferred caption language(s), e.g. "en" or "en,de" (default from DEFAULT_LANGUAGE or en)')
    g.add_argument("-f", "--format", default="txt", choices=FORMATS, help="output format (default: txt)")
    g.add_argument("-o", "--out", default="transcripts", help="output directory (default: ./transcripts)")
    g.add_argument("--engine", default=None, choices=ENGINES, help="auto (default), captions, or whisper")
    g.add_argument("--timestamps", action="store_true", help="include [mm:ss] timestamps in txt/md output")
    g.add_argument("--no-playlist", action="store_true", help="only transcribe the video, even if the link is part of a playlist")
    g.add_argument("--stdout", action="store_true", help="print transcripts instead of writing files")

    d = sub.add_parser("desktop", help="run as a desktop app: opens the browser, shows how to connect a phone")
    d.add_argument("--port", type=int, default=None, help="port to listen on (default 8000, or the next free one)")
    d.add_argument("--no-browser", action="store_true", help="don't open a browser window")
    d.add_argument("--data-dir", default=None, help="where to keep transcripts (default: per-user app data folder)")

    c = sub.add_parser("selfcheck", help="verify this installation/bundle works and print a JSON report")
    c.add_argument("--out", default=None, help="also write the report to this file")
    c.add_argument("--no-whisper", action="store_true", help="skip loading the Whisper native libraries")
    return p


def main(argv: list[str] | None = None) -> int:
    from .desktop import is_frozen, run_desktop, selfcheck

    argv = list(sys.argv[1:] if argv is None else argv)
    commands = ("serve", "get", "desktop", "selfcheck")
    if not argv and is_frozen():
        argv = ["desktop"]  # double-clicked packaged app
    # `yt-transcript <url>` is the common case; treat it as `get <url>`.
    if argv and argv[0] not in commands + ("-h", "--help", "--version") and not argv[0].startswith("-"):
        argv.insert(0, "get")
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "desktop":
        return run_desktop(
            Settings.from_env(), port=args.port, open_browser=not args.no_browser, data_dir=args.data_dir, verbose=args.verbose
        )
    if args.command == "selfcheck":
        return selfcheck(out=args.out, with_whisper=not args.no_whisper)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s" if args.verbose else "%(message)s",
    )
    if not args.verbose:
        logging.getLogger("yt_transcript.youtube").setLevel(logging.WARNING)
    settings = Settings.from_env()

    if args.command == "serve":
        return _serve(settings, args)
    if args.command == "get":
        return _get(settings, args)
    parser.print_help()
    return 2


def _serve(settings: Settings, args: argparse.Namespace) -> int:
    import uvicorn

    host = args.host or settings.host
    port = args.port or settings.port
    print(f"Serving on http://{host}:{port}  (data dir: {settings.data_dir.resolve()})")
    if settings.password:
        print(f"HTTP basic auth enabled (user: {settings.username})")
    uvicorn.run("yt_transcript.app:create_app", factory=True, host=host, port=port, reload=args.reload, log_level="info")
    return 0


def _get(settings: Settings, args: argparse.Namespace) -> int:
    from . import youtube
    from .transcribe import transcribe

    languages = [lang.strip() for lang in (args.language or settings.default_language or "en").split(",") if lang.strip()]
    engine = args.engine or settings.default_engine
    out_dir = Path(args.out)
    failures = 0
    refs = []
    for url in args.urls:
        try:
            refs.extend(youtube.resolve(url, settings, expand_playlists=not args.no_playlist))
        except (TranscriptError, ValueError) as e:
            print(f"error: {url}: {e}", file=sys.stderr)
            failures += 1
    if not refs:
        return 1
    if not args.stdout:
        out_dir.mkdir(parents=True, exist_ok=True)
    multi = len(refs) > 1
    for i, ref in enumerate(refs, 1):
        label = ref.title or ref.id or ref.url
        print(f"[{i}/{len(refs)}] {label}", file=sys.stderr)
        try:
            t = transcribe(ref, settings, engine=engine, languages=languages, status=lambda m: print(f"    {m}…", file=sys.stderr))
        except TranscriptError as e:
            print(f"    failed: {e}", file=sys.stderr)
            failures += 1
            continue
        body = render(t, args.format, timestamps=args.timestamps)
        if args.stdout:
            if multi:
                print(f"\n===== {t.title} =====\n")
            sys.stdout.write(body)
        else:
            path = out_dir / filename_for(t, args.format, index=i if multi else None)
            path.write_text(body, encoding="utf-8")
            print(f"    -> {path}  ({t.source}, {t.language})", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
