"""Recognise and normalise YouTube URLs.

We only need to answer two questions here: is this a single video, a playlist,
or something we should hand to yt-dlp to figure out; and what is the id?
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, urlparse

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
PLAYLIST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{13,}$")

_YT_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
    "youtu.be",
    "www.youtu.be",
}

# URL path prefixes that carry a video id as the next segment.
_VIDEO_PATH_PREFIXES = ("embed", "shorts", "live", "v", "e")


@dataclass(frozen=True)
class ParsedUrl:
    kind: str  # "video" | "playlist" | "unknown"
    url: str  # normalised URL to hand to yt-dlp
    video_id: Optional[str] = None
    playlist_id: Optional[str] = None


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def playlist_url(playlist_id: str) -> str:
    return f"https://www.youtube.com/playlist?list={playlist_id}"


def _clean(text: str) -> str:
    text = text.strip()
    # People paste "Check this out: https://..." from share sheets. Pull out the URL.
    m = re.search(r"https?://[^\s<>\"']+", text)
    if m:
        text = m.group(0)
    return text.rstrip(".,;)")


def parse(text: str) -> ParsedUrl:
    """Classify a pasted URL or id.

    Playlist ids beginning with ``RD`` (YouTube "mixes") are infinite,
    auto-generated lists; we ignore those and treat the URL as a single video.
    """
    raw = _clean(text)
    if not raw:
        raise ValueError("Empty URL")

    if VIDEO_ID_RE.match(raw):
        return ParsedUrl("video", watch_url(raw), video_id=raw)

    if not re.match(r"^[a-z]+://", raw, re.I):
        raw = "https://" + raw

    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if host not in _YT_HOSTS:
        # Not YouTube. Let yt-dlp try; it supports hundreds of sites, and a
        # caption/whisper pipeline works for most of them.
        return ParsedUrl("unknown", raw)

    qs = parse_qs(parsed.query)
    list_id = (qs.get("list") or [None])[0]
    if list_id and list_id.startswith("RD"):
        list_id = None  # mixes are not real playlists
    if list_id and not PLAYLIST_ID_RE.match(list_id):
        list_id = None

    video_id: Optional[str] = None
    path_parts = [p for p in parsed.path.split("/") if p]

    if host.endswith("youtu.be"):
        if path_parts and VIDEO_ID_RE.match(path_parts[0]):
            video_id = path_parts[0]
    else:
        v = (qs.get("v") or [None])[0]
        if v and VIDEO_ID_RE.match(v):
            video_id = v
        elif len(path_parts) >= 2 and path_parts[0] in _VIDEO_PATH_PREFIXES and VIDEO_ID_RE.match(path_parts[1]):
            video_id = path_parts[1]

    if path_parts and path_parts[0] == "playlist" and list_id:
        return ParsedUrl("playlist", playlist_url(list_id), playlist_id=list_id)

    if video_id and list_id:
        # A video opened from within a playlist. Keep both; the caller decides
        # whether to expand the playlist.
        return ParsedUrl("video", watch_url(video_id), video_id=video_id, playlist_id=list_id)

    if video_id:
        return ParsedUrl("video", watch_url(video_id), video_id=video_id)

    if list_id:
        return ParsedUrl("playlist", playlist_url(list_id), playlist_id=list_id)

    # Channel pages, @handles, /c/..., /user/... -> yt-dlp treats them as playlists.
    return ParsedUrl("unknown", raw)


_URL_RE = re.compile(r"https?://[^\s<>\"']+")


def split_input(text: str) -> list[str]:
    """Pull URLs out of a pasted blob.

    Full ``http(s)://`` links are extracted wherever they appear (share sheets
    add prose around them). Otherwise whitespace/comma separated tokens count
    if they look like a host name or a bare 11-character video id.
    """
    text = text or ""
    found = [m.group(0).rstrip(".,;)") for m in _URL_RE.finditer(text)]
    if found:
        return found
    urls: list[str] = []
    for token in re.split(r"[\s,]+", text):
        token = token.strip()
        if token and (VIDEO_ID_RE.match(token) or ("." in token and "/" in token) or token.startswith("www.")):
            urls.append(token)
    return urls
