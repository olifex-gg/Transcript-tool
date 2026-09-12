"""Everything that touches yt-dlp: resolving playlists, listing caption
tracks, downloading captions and (for Whisper) audio."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import yt_dlp

from . import urls
from .captions import parse_json3, parse_vtt
from .config import Settings
from .models import NoCaptionsError, Segment, TranscriptError, VideoRef

log = logging.getLogger(__name__)

_CAPTION_EXT_PRIORITY = ("json3", "vtt", "srt")


class _Logger:
    """Route yt-dlp's chatter through the standard logging module."""

    def debug(self, msg: str) -> None:
        if not msg.startswith("[debug]"):
            log.debug(msg)

    def info(self, msg: str) -> None:
        log.debug(msg)

    def warning(self, msg: str) -> None:
        log.warning(msg)

    def error(self, msg: str) -> None:
        log.error(msg)


def base_opts(settings: Settings) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _Logger(),
        "skip_download": True,
        "retries": 3,
        "extractor_retries": 3,
        "socket_timeout": 30,
        "color": {"stdout": "no_color", "stderr": "no_color"},
    }
    if settings.cookies_file:
        opts["cookiefile"] = settings.cookies_file
    if settings.proxy:
        opts["proxy"] = settings.proxy
    opts.update(settings.ytdlp_opts or {})
    return opts


def clean_error(e: BaseException) -> str:
    msg = str(e)
    for prefix in ("ERROR: ", "[youtube] "):
        if msg.startswith(prefix):
            msg = msg[len(prefix):]
    # Drop yt-dlp's "please report this issue" boilerplate.
    for marker in ("; please report", ". Please report"):
        if marker in msg:
            msg = msg.split(marker, 1)[0]
    return msg.strip() or e.__class__.__name__


# --------------------------------------------------------------------------- #
# Resolving what to transcribe
# --------------------------------------------------------------------------- #


def resolve(url: str, settings: Settings, expand_playlists: bool = True) -> list[VideoRef]:
    """Turn a pasted URL into a list of videos.

    Single video URLs never hit the network here (the transcriber fetches
    metadata once anyway). Playlists, channels and unknown URLs are expanded
    with yt-dlp's flat extraction, which is one request per page of results.
    """
    parsed = urls.parse(url)
    if parsed.kind == "video" and not (expand_playlists and parsed.playlist_id):
        return [VideoRef(url=parsed.url, id=parsed.video_id)]

    target = parsed.url
    if parsed.kind == "video" and parsed.playlist_id:
        target = urls.playlist_url(parsed.playlist_id)

    opts = base_opts(settings)
    opts.update(
        {
            "extract_flat": "in_playlist",
            "noplaylist": not expand_playlists,
            "playlistend": settings.max_playlist_items,
            "lazy_playlist": False,
        }
    )
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(target, download=False)
    except yt_dlp.utils.DownloadError as e:
        raise TranscriptError(clean_error(e)) from e
    if not info:
        raise TranscriptError(f"Could not resolve {url}")
    return refs_from_info(info, target)


def refs_from_info(info: dict[str, Any], fallback_url: str) -> list[VideoRef]:
    if info.get("_type") == "playlist" or "entries" in info:
        refs: list[VideoRef] = []
        playlist_title = info.get("title")
        for i, e in enumerate(_iter_entries(info.get("entries")), 1):
            if not e:
                continue  # private / deleted video
            if e.get("_type") == "playlist" or str(e.get("ie_key", "")).endswith("Tab"):
                continue  # nested playlist (e.g. a channel's "Playlists" tab)
            vid = e.get("id")
            u = e.get("url") or e.get("webpage_url") or (urls.watch_url(vid) if vid else None)
            if not u:
                continue
            refs.append(
                VideoRef(
                    url=u,
                    id=vid,
                    title=e.get("title"),
                    duration=e.get("duration"),
                    playlist_title=playlist_title,
                    playlist_index=e.get("playlist_index") or i,
                )
            )
        if not refs:
            raise TranscriptError("Playlist is empty or every video in it is unavailable")
        return refs
    return [
        VideoRef(
            url=info.get("webpage_url") or fallback_url,
            id=info.get("id"),
            title=info.get("title"),
            duration=info.get("duration"),
        )
    ]


def _iter_entries(entries: Any) -> Iterable[Optional[dict[str, Any]]]:
    if entries is None:
        return []
    return list(entries)  # yt-dlp may hand us a generator


# --------------------------------------------------------------------------- #
# Caption tracks
# --------------------------------------------------------------------------- #


@dataclass
class CaptionTrack:
    lang: str
    kind: str  # "manual" | "auto"
    formats: list[dict[str, Any]]
    name: Optional[str] = None

    @property
    def is_translation(self) -> bool:
        return bool(self.name and " from " in self.name)


def _lang_base(lang: str) -> str:
    return lang.lower().split("-")[0]


def list_caption_tracks(info: dict[str, Any]) -> list[CaptionTrack]:
    tracks: list[CaptionTrack] = []
    for kind, key in (("manual", "subtitles"), ("auto", "automatic_captions")):
        for lang, fmts in (info.get(key) or {}).items():
            fmts = [f for f in fmts or [] if f.get("url") and f.get("ext") in _CAPTION_EXT_PRIORITY]
            if fmts:
                name = next((f.get("name") for f in fmts if f.get("name")), None)
                tracks.append(CaptionTrack(lang=lang, kind=kind, formats=fmts, name=name))
    return tracks


def pick_caption_track(info: dict[str, Any], preferred: Iterable[str]) -> Optional[CaptionTrack]:
    """Choose the best track: human-made subtitles beat auto captions,
    and the caller's preferred languages beat everything else. A preference
    of "orig" or "*" means "whatever language the video is in"."""
    tracks = list_caption_tracks(info)
    if not tracks:
        return None
    manual = [t for t in tracks if t.kind == "manual"]
    auto = [t for t in tracks if t.kind == "auto"]

    def find(pool: list[CaptionTrack], want: str) -> Optional[CaptionTrack]:
        want = want.lower()
        for t in pool:
            if t.lang.lower() == want:
                return t
        for t in pool:
            if t.lang.lower() == f"{want}-orig":
                return t
        for t in pool:
            if _lang_base(t.lang) == _lang_base(want) and not t.is_translation:
                return t
        for t in pool:
            if _lang_base(t.lang) == _lang_base(want):
                return t
        return None

    def original(pool: list[CaptionTrack]) -> Optional[CaptionTrack]:
        for t in pool:
            if t.lang.lower().endswith("-orig"):
                return t
        for t in pool:
            if not t.is_translation:
                return t
        return pool[0] if pool else None

    for want in preferred:
        if want in ("orig", "*", "original", ""):
            return original(manual) or original(auto)
        found = find(manual, want) or find(auto, want)
        if found:
            return found
    return original(manual) or original(auto)


def fetch_captions(ydl: yt_dlp.YoutubeDL, track: CaptionTrack) -> list[Segment]:
    """Download and parse a caption track, trying the cleanest format first."""
    errors: list[str] = []
    ordered = sorted(track.formats, key=lambda f: _CAPTION_EXT_PRIORITY.index(f["ext"]))
    for fmt in ordered:
        try:
            raw = ydl.urlopen(fmt["url"]).read()
            if not raw or not raw.strip():
                errors.append(f"{fmt['ext']}: empty response")
                continue
            if fmt["ext"] == "json3":
                segments = parse_json3(json.loads(raw.decode("utf-8")))
            else:
                segments = parse_vtt(raw.decode("utf-8", "replace"))
            if segments:
                return segments
            errors.append(f"{fmt['ext']}: no cues")
        except Exception as e:  # noqa: BLE001 - we want to try the next format
            errors.append(f"{fmt['ext']}: {clean_error(e)}")
    raise NoCaptionsError("Caption track could not be downloaded (" + "; ".join(errors) + ")")


# --------------------------------------------------------------------------- #
# Metadata + audio
# --------------------------------------------------------------------------- #


def fetch_info(url: str, settings: Settings) -> dict[str, Any]:
    opts = base_opts(settings)
    opts["noplaylist"] = True
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        raise TranscriptError(clean_error(e)) from e
    if not info:
        raise TranscriptError(f"No metadata returned for {url}")
    if info.get("_type") == "playlist":
        entries = [e for e in _iter_entries(info.get("entries")) if e]
        if not entries:
            raise TranscriptError("URL resolved to an empty playlist")
        info = entries[0]
    return info


def download_audio(url: str, settings: Settings, dest_dir: str) -> str:
    """Download the best audio-only stream. Returns the file path."""
    opts = base_opts(settings)
    opts.update(
        {
            "skip_download": False,
            "noplaylist": True,
            "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
            "outtmpl": os.path.join(dest_dir, "%(id)s.%(ext)s"),
            "overwrites": True,
        }
    )
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = None
            for rd in info.get("requested_downloads") or []:
                if rd.get("filepath"):
                    path = rd["filepath"]
                    break
            if not path:
                path = ydl.prepare_filename(info)
    except yt_dlp.utils.DownloadError as e:
        raise TranscriptError(clean_error(e)) from e
    if not path or not os.path.exists(path):
        raise TranscriptError("Audio download failed")
    return path
