"""Optional local speech-to-text via faster-whisper.

Only imported lazily so the base install stays small. Install with
``pip install "yt-transcript[whisper]"``.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from .models import Segment

log = logging.getLogger(__name__)

_model_lock = threading.Lock()
_models: dict[tuple[str, str, str], object] = {}


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def _get_model(name: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel

    key = (name, device, compute_type)
    with _model_lock:
        if key not in _models:
            log.info("Loading Whisper model %s (%s/%s)", name, device, compute_type)
            _models[key] = WhisperModel(name, device=device, compute_type=compute_type)
        return _models[key]


def transcribe_file(
    path: str,
    model_name: str = "small",
    language: Optional[str] = None,
    device: str = "auto",
    compute_type: str = "int8",
    progress: Optional[Callable[[float], None]] = None,
) -> tuple[list[Segment], Optional[str]]:
    """Transcribe an audio file. Returns (segments, detected_language)."""
    model = _get_model(model_name, device, compute_type)
    lang = None if language in (None, "", "auto", "orig", "*") else language
    seg_iter, info = model.transcribe(path, language=lang, vad_filter=True, beam_size=5)
    total = float(getattr(info, "duration", 0) or 0)
    segments: list[Segment] = []
    for s in seg_iter:
        text = (s.text or "").strip()
        if not text:
            continue
        segments.append(Segment(float(s.start), max(float(s.end) - float(s.start), 0.0), text))
        if progress and total:
            progress(min(float(s.end) / total, 1.0))
    return segments, getattr(info, "language", None)
