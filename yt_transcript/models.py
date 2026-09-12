"""Plain data structures shared across the package."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class Segment:
    """One timed chunk of transcript text. Times are seconds."""

    start: float
    duration: float
    text: str

    @property
    def end(self) -> float:
        return self.start + self.duration

    def to_dict(self) -> dict[str, Any]:
        return {"start": round(self.start, 3), "duration": round(self.duration, 3), "text": self.text}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Segment":
        return cls(float(d["start"]), float(d["duration"]), str(d["text"]))


@dataclass
class VideoRef:
    """A video we intend to transcribe (before we know much about it)."""

    url: str
    id: Optional[str] = None
    title: Optional[str] = None
    duration: Optional[float] = None
    playlist_title: Optional[str] = None
    playlist_index: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "VideoRef":
        return cls(**{k: d.get(k) for k in ("url", "id", "title", "duration", "playlist_title", "playlist_index")})


@dataclass
class Transcript:
    """A finished transcript for one video."""

    video_id: str
    url: str
    title: str
    segments: list[Segment]
    language: Optional[str] = None
    source: str = "captions"  # "captions" | "auto-captions" | "whisper"
    duration: Optional[float] = None
    channel: Optional[str] = None
    upload_date: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.segments if s.text)

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_id": self.video_id,
            "url": self.url,
            "title": self.title,
            "language": self.language,
            "source": self.source,
            "duration": self.duration,
            "channel": self.channel,
            "upload_date": self.upload_date,
            "extra": self.extra,
            "segments": [s.to_dict() for s in self.segments],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Transcript":
        return cls(
            video_id=d["video_id"],
            url=d["url"],
            title=d.get("title") or d["video_id"],
            segments=[Segment.from_dict(s) for s in d.get("segments", [])],
            language=d.get("language"),
            source=d.get("source", "captions"),
            duration=d.get("duration"),
            channel=d.get("channel"),
            upload_date=d.get("upload_date"),
            extra=d.get("extra") or {},
        )


class TranscriptError(Exception):
    """Raised when a video cannot be transcribed."""


class NoCaptionsError(TranscriptError):
    """The video has no usable caption track."""
