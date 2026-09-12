"""Pick a transcription strategy for one video and run it."""

from __future__ import annotations

import logging
import tempfile
from typing import Callable, Optional, Sequence

import yt_dlp

from . import whisper_engine, youtube
from .config import Settings
from .models import NoCaptionsError, Transcript, TranscriptError, VideoRef

log = logging.getLogger(__name__)

ENGINES = ("auto", "captions", "whisper")
StatusCallback = Callable[[str], None]


def transcribe(
    ref: VideoRef,
    settings: Settings,
    engine: str = "auto",
    languages: Sequence[str] = ("en",),
    status: Optional[StatusCallback] = None,
) -> Transcript:
    """Return a transcript for one video, or raise TranscriptError.

    ``engine``: ``captions`` uses YouTube's own subtitle tracks (fast, free),
    ``whisper`` runs local speech-to-text on the audio, ``auto`` tries captions
    first and falls back to Whisper when the video has none.
    """
    if engine not in ENGINES:
        raise ValueError(f"engine must be one of {ENGINES}")
    notify = status or (lambda _msg: None)

    notify("Fetching video info")
    info = youtube.fetch_info(ref.url, settings)
    if info.get("is_live"):
        raise TranscriptError("This is a live stream; try again once it has ended")

    meta = dict(
        video_id=info.get("id") or ref.id or ref.url,
        url=info.get("webpage_url") or ref.url,
        title=info.get("title") or ref.title or info.get("id") or ref.url,
        duration=info.get("duration") or ref.duration,
        channel=info.get("channel") or info.get("uploader"),
        upload_date=info.get("upload_date"),
    )

    if engine in ("auto", "captions"):
        try:
            return _from_captions(info, settings, languages, meta, notify)
        except NoCaptionsError as e:
            if engine == "captions":
                raise
            if not whisper_engine.available():
                raise NoCaptionsError(
                    f"{e}. Install the optional Whisper extra to transcribe audio directly."
                ) from e
            log.info("No captions for %s (%s); falling back to Whisper", meta["video_id"], e)

    return _from_whisper(ref, settings, languages, meta, notify)


def _from_captions(info, settings, languages, meta, notify) -> Transcript:
    track = youtube.pick_caption_track(info, languages)
    if track is None:
        raise NoCaptionsError("This video has no captions or subtitles")
    notify(f"Downloading {track.kind} captions ({track.lang})")
    with yt_dlp.YoutubeDL(youtube.base_opts(settings)) as ydl:
        segments = youtube.fetch_captions(ydl, track)
    return Transcript(
        segments=segments,
        language=track.lang,
        source="captions" if track.kind == "manual" else "auto-captions",
        extra={"caption_name": track.name, "machine_translated": track.is_translation},
        **meta,
    )


def _from_whisper(ref, settings, languages, meta, notify) -> Transcript:
    if not whisper_engine.available():
        raise TranscriptError('Whisper is not installed. Run: pip install "yt-transcript[whisper]"')
    duration = meta.get("duration") or 0
    limit = settings.whisper_max_minutes
    if limit and duration and duration > limit * 60:
        raise TranscriptError(
            f"Video is {int(duration // 60)} min long; Whisper is limited to {limit} min "
            "(raise WHISPER_MAX_MINUTES to allow it)"
        )
    lang = next((lang for lang in languages if lang), None)
    with tempfile.TemporaryDirectory(prefix="yt-audio-") as tmp:
        notify("Downloading audio")
        path = youtube.download_audio(ref.url, settings, tmp)
        notify(f"Transcribing with Whisper ({settings.whisper_model})")

        def progress(frac: float) -> None:
            notify(f"Transcribing with Whisper ({settings.whisper_model}) {int(frac * 100)}%")

        try:
            segments, detected = whisper_engine.transcribe_file(
                path,
                model_name=settings.whisper_model,
                language=lang,
                device=settings.whisper_device,
                compute_type=settings.whisper_compute_type,
                progress=progress,
            )
        except Exception as e:  # noqa: BLE001
            raise TranscriptError(f"Whisper failed: {e}") from e
    if not segments:
        raise TranscriptError("Whisper produced no text (silent audio?)")
    return Transcript(
        segments=segments,
        language=detected or lang,
        source="whisper",
        extra={"whisper_model": settings.whisper_model},
        **meta,
    )
