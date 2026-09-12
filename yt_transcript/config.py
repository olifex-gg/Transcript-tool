"""Runtime settings, all overridable through environment variables."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    try:
        return int(v) if v not in (None, "") else default
    except ValueError:
        return default


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path("data"))
    host: str = "0.0.0.0"
    port: int = 8000
    password: Optional[str] = None  # optional HTTP basic auth password (user is "yt")
    username: str = "yt"

    default_language: str = "en"
    default_engine: str = "auto"  # auto | captions | whisper
    expand_playlists: bool = True
    max_playlist_items: int = 500
    workers: int = 2  # videos transcribed in parallel

    whisper_model: str = "small"
    whisper_device: str = "auto"
    whisper_compute_type: str = "int8"
    whisper_max_minutes: int = 240  # refuse to run Whisper on longer videos (0 = no limit)

    cookies_file: Optional[str] = None
    proxy: Optional[str] = None
    ytdlp_opts: dict[str, Any] = field(default_factory=dict)  # raw extra yt-dlp options (JSON)

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls()
        s.data_dir = Path(os.environ.get("DATA_DIR", "data"))
        s.host = os.environ.get("HOST", s.host)
        s.port = _env_int("PORT", s.port)
        s.password = os.environ.get("APP_PASSWORD") or None
        s.username = os.environ.get("APP_USERNAME", s.username)
        s.default_language = os.environ.get("DEFAULT_LANGUAGE", s.default_language)
        s.default_engine = os.environ.get("DEFAULT_ENGINE", s.default_engine)
        s.expand_playlists = _env_bool("EXPAND_PLAYLISTS", s.expand_playlists)
        s.max_playlist_items = _env_int("MAX_PLAYLIST_ITEMS", s.max_playlist_items)
        s.workers = max(1, _env_int("WORKERS", s.workers))
        s.whisper_model = os.environ.get("WHISPER_MODEL", s.whisper_model)
        s.whisper_device = os.environ.get("WHISPER_DEVICE", s.whisper_device)
        s.whisper_compute_type = os.environ.get("WHISPER_COMPUTE_TYPE", s.whisper_compute_type)
        s.whisper_max_minutes = _env_int("WHISPER_MAX_MINUTES", s.whisper_max_minutes)
        s.cookies_file = os.environ.get("YT_COOKIES_FILE") or None
        s.proxy = os.environ.get("YT_PROXY") or None
        raw = os.environ.get("YTDLP_OPTS")
        if raw:
            try:
                s.ytdlp_opts = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValueError(f"YTDLP_OPTS is not valid JSON: {e}") from e
        return s

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"
