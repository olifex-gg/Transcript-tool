import json

from yt_transcript import formats
from yt_transcript.models import Segment, Transcript


def make_t(long=False):
    segs = [Segment(0.0, 2.0, "Hello there."), Segment(2.0, 2.5, "This is a test"), Segment(7.5, 1.0, "of paragraphs.")]
    if long:
        segs.append(Segment(3700.0, 1.0, "An hour later."))
    return Transcript(video_id="abc", url="https://www.youtube.com/watch?v=abc", title="My: Video / Title?", segments=segs,
                      language="en", source="auto-captions", duration=3701.0 if long else 9.0, channel="Chan")


def test_txt_paragraphs_split_on_gap():
    out = formats.to_txt(make_t())
    assert out == "Hello there. This is a test\n\nof paragraphs.\n"


def test_txt_timestamps():
    out = formats.to_txt(make_t(), timestamps=True)
    assert out.splitlines()[0] == "[0:00] Hello there."
    assert out.splitlines()[2] == "[0:07] of paragraphs."
    long = formats.to_txt(make_t(long=True), timestamps=True)
    assert long.splitlines()[0] == "[0:00:00] Hello there."
    assert long.splitlines()[-1] == "[1:01:40] An hour later."


def test_srt_and_vtt():
    srt = formats.to_srt(make_t())
    assert srt.startswith("1\n00:00:00,000 --> 00:00:04,500\nHello there. This is a test\n")
    assert "00:00:07,500 --> 00:00:08,500\nof paragraphs." in srt
    vtt = formats.to_vtt(make_t())
    assert vtt.startswith("WEBVTT\n\n00:00:00.000 --> 00:00:04.500\n")


def test_md_and_json():
    md = formats.to_md(make_t())
    assert md.startswith("# My: Video / Title?\n\n- Source: https://www.youtube.com/watch?v=abc\n- Channel: Chan\n")
    assert "Hello there." in md
    j = json.loads(formats.to_json(make_t()))
    assert j["video_id"] == "abc" and j["text"].startswith("Hello there.") and len(j["segments"]) == 3


def test_filename_is_safe():
    assert formats.filename_for(make_t(), "srt") == "My Video Title.srt"
    assert formats.filename_for(make_t(), "txt", index=3) == "003 - My Video Title.txt"
    assert formats.safe_filename("") == "transcript"


def test_render_unknown_format():
    import pytest

    with pytest.raises(ValueError):
        formats.render(make_t(), "docx")
