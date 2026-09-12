"""Render a Transcript into the file formats people actually want."""

from __future__ import annotations

import json
import re
from typing import Iterable

from .captions import merge_short_segments
from .models import Segment, Transcript

FORMATS = ("txt", "md", "srt", "vtt", "json")
EXTENSIONS = {"txt": "txt", "md": "md", "srt": "srt", "vtt": "vtt", "json": "json"}
MIME_TYPES = {
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "srt": "application/x-subrip; charset=utf-8",
    "vtt": "text/vtt; charset=utf-8",
    "json": "application/json; charset=utf-8",
}


def fmt_clock(seconds: float, always_hours: bool = False) -> str:
    seconds = max(0.0, float(seconds))
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h or always_hours:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"


def _fmt_ts(seconds: float, sep: str) -> str:
    seconds = max(0.0, float(seconds))
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms == 1000:
        seconds += 1
        ms = 0
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_paragraphs(segments: Iterable[Segment], max_gap: float = 2.5, max_chars: int = 700) -> list[str]:
    """Join cues into paragraphs. A new paragraph starts on a long pause,
    or once a paragraph gets long *and* a sentence just ended."""
    paras: list[str] = []
    cur: list[str] = []
    cur_len = 0
    last_end = None
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        gap = (seg.start - last_end) if last_end is not None else 0.0
        ended_sentence = bool(cur) and re.search(r"[.!?][\"')\]]?$", cur[-1]) is not None
        if cur and (gap > max_gap or (cur_len >= max_chars and ended_sentence)):
            paras.append(" ".join(cur))
            cur, cur_len = [], 0
        cur.append(text)
        cur_len += len(text) + 1
        last_end = seg.end
    if cur:
        paras.append(" ".join(cur))
    return paras


def to_txt(t: Transcript, timestamps: bool = False) -> str:
    if timestamps:
        long = (t.duration or (t.segments[-1].end if t.segments else 0)) >= 3600
        lines = [f"[{fmt_clock(s.start, long)}] {s.text}" for s in t.segments if s.text]
        return "\n".join(lines) + "\n"
    return "\n\n".join(to_paragraphs(t.segments)) + "\n"


def to_md(t: Transcript, timestamps: bool = False) -> str:
    head = [f"# {t.title}", ""]
    meta = [f"- Source: {t.url}"]
    if t.channel:
        meta.append(f"- Channel: {t.channel}")
    if t.duration:
        meta.append(f"- Duration: {fmt_clock(t.duration)}")
    if t.language:
        meta.append(f"- Language: {t.language}")
    meta.append(f"- Transcript: {t.source}")
    head.extend(meta)
    head.append("")
    return "\n".join(head) + "\n" + to_txt(t, timestamps=timestamps)


def to_srt(t: Transcript) -> str:
    out = []
    for i, seg in enumerate(merge_short_segments(t.segments), 1):
        out.append(f"{i}\n{_fmt_ts(seg.start, ',')} --> {_fmt_ts(seg.end, ',')}\n{seg.text}\n")
    return "\n".join(out)


def to_vtt(t: Transcript) -> str:
    out = ["WEBVTT", ""]
    for seg in merge_short_segments(t.segments):
        out.append(f"{_fmt_ts(seg.start, '.')} --> {_fmt_ts(seg.end, '.')}\n{seg.text}\n")
    return "\n".join(out)


def to_json(t: Transcript) -> str:
    d = t.to_dict()
    d["text"] = t.text
    return json.dumps(d, ensure_ascii=False, indent=2) + "\n"


def render(t: Transcript, fmt: str, timestamps: bool = False) -> str:
    fmt = (fmt or "txt").lower()
    if fmt == "txt":
        return to_txt(t, timestamps)
    if fmt == "md":
        return to_md(t, timestamps)
    if fmt == "srt":
        return to_srt(t)
    if fmt == "vtt":
        return to_vtt(t)
    if fmt == "json":
        return to_json(t)
    raise ValueError(f"Unknown format: {fmt}")


def safe_filename(name: str, max_len: int = 120) -> str:
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", " ", name or "").strip()
    name = re.sub(r"\s+", " ", name)
    return (name[:max_len].rstrip(" .") or "transcript")


def filename_for(t: Transcript, fmt: str, index: int | None = None) -> str:
    base = safe_filename(t.title)
    if index is not None:
        base = f"{index:03d} - {base}"
    return f"{base}.{EXTENSIONS[fmt]}"
