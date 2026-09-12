"""Parsers for the caption formats YouTube hands out.

json3 is what we ask for first; it carries clean per-cue timing. VTT is the
fallback and needs de-duplication because YouTube's auto-generated VTT
repeats each line as it "rolls" onto the screen.
"""

from __future__ import annotations

import html
import re
from typing import Any, Iterable, Optional

from .models import Segment

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_TIMESTAMP_RE = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})")


def _norm(text: str) -> str:
    text = _TAG_RE.sub("", text)  # strip <c>/<i>/timing tags first...
    text = html.unescape(text)  # ...so an escaped "&lt;show&gt;" survives as text
    return _WS_RE.sub(" ", text).strip()


def parse_json3(data: dict[str, Any]) -> list[Segment]:
    """Convert YouTube's ``fmt=json3`` payload into segments."""
    segments: list[Segment] = []
    for ev in data.get("events") or []:
        segs = ev.get("segs")
        if not segs:
            continue  # window/style definition events carry no text
        text = _norm("".join(s.get("utf8", "") for s in segs))
        if not text:
            continue
        start = float(ev.get("tStartMs", 0)) / 1000.0
        dur = float(ev.get("dDurationMs", 0)) / 1000.0
        if ev.get("aAppend") and segments:
            prev = segments[-1]
            prev.text = _norm(prev.text + " " + text)
            prev.duration = max(prev.duration, start + dur - prev.start)
            continue
        if segments and segments[-1].text == text and abs(segments[-1].start - start) < 0.05:
            continue
        segments.append(Segment(start, dur, text))
    return _fix_durations(segments)


def _parse_ts(ts: str) -> float:
    m = _TIMESTAMP_RE.search(ts)
    if not m:
        raise ValueError(f"Bad timestamp: {ts!r}")
    h, mnt, s, ms = m.groups()
    return int(h or 0) * 3600 + int(mnt) * 60 + int(s) + int(ms) / 1000.0


def parse_vtt(text: str) -> list[Segment]:
    """Parse WebVTT (or SRT, which is close enough) into segments.

    YouTube's auto-generated VTT "rolls": every cue repeats the previous
    line above the new one, and a few-millisecond cue shows the finished line.
    Lines already seen in the preceding cue are dropped, and a cue with
    nothing new just extends the previous segment.

    Cues end at a truly empty line; YouTube puts a space-only line *inside*
    cues, so we must not treat whitespace lines as separators.
    """
    segments: list[Segment] = []
    prev_lines: list[str] = []
    cur: Optional[list[Any]] = None  # [start, end, raw text lines]

    def flush(before_timing: bool = False) -> None:
        nonlocal cur, prev_lines
        if cur is None:
            return
        start, end, raw_lines = cur
        cur = None
        if before_timing and raw_lines and raw_lines[-1].strip().isdigit():
            raw_lines = raw_lines[:-1]  # SRT cue index glued to the next cue
        cue_lines = [ln for ln in (_norm(ln) for ln in raw_lines) if ln]
        if not cue_lines:
            return
        new_lines = [ln for ln in cue_lines if ln not in prev_lines]
        prev_lines = cue_lines
        if not new_lines:
            if segments:
                segments[-1].duration = max(segments[-1].duration, end - segments[-1].start)
            return
        segments.append(Segment(start, max(end - start, 0.0), " ".join(new_lines)))

    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if "-->" in line:
            flush(before_timing=True)
            try:
                start_s, end_s = line.split("-->", 1)
                cur = [_parse_ts(start_s), _parse_ts(end_s), []]
            except ValueError:
                cur = None
        elif line == "":
            flush()
        elif cur is not None:
            cur[2].append(line)
    flush()
    return _fix_durations(segments)


def _fix_durations(segments: list[Segment]) -> list[Segment]:
    """Give zero-length cues a sensible duration (until the next cue starts)."""
    for i, seg in enumerate(segments):
        if seg.duration <= 0:
            if i + 1 < len(segments):
                seg.duration = max(segments[i + 1].start - seg.start, 0.5)
            else:
                seg.duration = 2.0
    return segments


def merge_short_segments(segments: Iterable[Segment], min_chars: int = 40, max_gap: float = 1.0) -> list[Segment]:
    """Join tiny consecutive cues into more readable lines (used for SRT/VTT output)."""
    out: list[Segment] = []
    for seg in segments:
        if out and len(out[-1].text) < min_chars and seg.start - out[-1].end <= max_gap:
            prev = out[-1]
            prev.text = f"{prev.text} {seg.text}".strip()
            prev.duration = seg.end - prev.start
        else:
            out.append(Segment(seg.start, seg.duration, seg.text))
    return out
