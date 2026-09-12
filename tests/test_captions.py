from yt_transcript.captions import merge_short_segments, parse_json3, parse_vtt
from yt_transcript.models import Segment


def test_parse_json3_basic_and_append():
    data = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 100, "wWinId": 1},  # window definition, no text
            {"tStartMs": 0, "dDurationMs": 2000, "segs": [{"utf8": "Hello"}, {"utf8": " world", "tOffsetMs": 500}]},
            {"tStartMs": 2000, "dDurationMs": 10, "segs": [{"utf8": "\n"}], "aAppend": 1},
            {"tStartMs": 2000, "dDurationMs": 1500, "segs": [{"utf8": "How &amp; why"}]},
            {"tStartMs": 3500, "dDurationMs": 500, "segs": [{"utf8": " indeed"}], "aAppend": 1},
            {"tStartMs": 5000, "dDurationMs": 0, "segs": [{"utf8": "Last"}]},
        ]
    }
    segs = parse_json3(data)
    assert [s.text for s in segs] == ["Hello world", "How & why indeed", "Last"]
    assert segs[0].start == 0.0 and segs[0].duration == 2.0
    assert segs[1].start == 2.0 and abs(segs[1].duration - 2.0) < 1e-6
    assert segs[2].duration == 2.0  # zero-length last cue gets a default


def test_parse_vtt_rolling_captions_dedup():
    vtt = """WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:01.990 align:start position:0%
 
hello<00:00:00.500><c> everyone</c>

00:00:01.990 --> 00:00:02.000 align:start position:0%
hello everyone
 

00:00:02.000 --> 00:00:04.000 align:start position:0%
hello everyone
welcome<00:00:02.500><c> back</c>

00:00:04.000 --> 00:00:04.010 align:start position:0%
welcome back

00:00:04.010 --> 00:00:06.000
welcome back
to the &lt;show&gt;
"""
    segs = parse_vtt(vtt)
    assert [s.text for s in segs] == ["hello everyone", "welcome back", "to the <show>"]
    assert segs[0].start == 0.0 and segs[0].duration == 2.0  # extended by the repeat cue
    assert segs[1].start == 2.0 and segs[2].start == 4.01


def test_parse_vtt_plain_and_srt():
    srt = """1
00:00:01,000 --> 00:00:03,000
First line

2
00:01:03,500 --> 00:01:05,000
Second <i>line</i>
"""
    segs = parse_vtt(srt)
    assert [s.text for s in segs] == ["First line", "Second line"]
    assert segs[1].start == 63.5 and segs[1].duration == 1.5


def test_merge_short_segments():
    segs = [Segment(0, 1, "Hi"), Segment(1, 1, "there"), Segment(2, 1, "friend of mine, how are you doing today?"), Segment(10, 1, "Later")]
    merged = merge_short_segments(segs, min_chars=10, max_gap=1.0)
    assert [m.text for m in merged] == ["Hi there friend of mine, how are you doing today?", "Later"]
    assert merged[0].start == 0 and merged[0].end == 3
