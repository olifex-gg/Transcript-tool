import pytest

from yt_transcript import youtube
from yt_transcript.models import TranscriptError


def info(subs=None, autos=None):
    def fmts(lang, name=None):
        return [{"ext": "json3", "url": f"https://x/{lang}.json3", "name": name}, {"ext": "vtt", "url": f"https://x/{lang}.vtt", "name": name}, {"ext": "ttml", "url": "u"}]

    return {
        "subtitles": {lang: fmts(lang, n) for lang, n in (subs or {}).items()},
        "automatic_captions": {lang: fmts(lang, n) for lang, n in (autos or {}).items()},
    }


def test_prefers_manual_over_auto():
    t = youtube.pick_caption_track(info(subs={"en": "English"}, autos={"en": "English"}), ["en"])
    assert t.kind == "manual" and t.lang == "en"


def test_language_prefix_match_and_orig():
    t = youtube.pick_caption_track(info(subs={"en-US": "English (US)"}), ["en"])
    assert t.lang == "en-US"
    t = youtube.pick_caption_track(info(autos={"de-orig": "German (Original)", "en": "English from German"}), ["de"])
    assert t.lang == "de-orig"


def test_falls_back_through_preferences_then_original():
    i = info(autos={"fr-orig": "French (Original)", "en": "English from French", "es": "Spanish from French"})
    assert youtube.pick_caption_track(i, ["ja", "es"]).lang == "es"
    assert youtube.pick_caption_track(i, ["ja"]).lang == "fr-orig"
    assert youtube.pick_caption_track(i, ["orig"]).lang == "fr-orig"
    t = youtube.pick_caption_track(i, ["en"])
    assert t.lang == "en" and t.is_translation


def test_no_tracks():
    assert youtube.pick_caption_track({"subtitles": {}, "automatic_captions": {}}, ["en"]) is None
    assert youtube.pick_caption_track(info(subs={"en": None}) | {"subtitles": {"en": [{"ext": "ttml", "url": "u"}]}}, ["en"]) is None


def test_refs_from_flat_playlist():
    i = {
        "_type": "playlist",
        "title": "PL title",
        "entries": [
            {"id": "aaaaaaaaaaa", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "title": "A", "duration": 10},
            None,
            {"id": "bbbbbbbbbbb", "title": "B", "playlist_index": 3},
            {"_type": "playlist", "id": "nested", "title": "Nested"},
            {"ie_key": "YoutubeTab", "url": "https://www.youtube.com/@x/videos"},
        ],
    }
    refs = youtube.refs_from_info(i, "https://www.youtube.com/playlist?list=PLx")
    assert [r.id for r in refs] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
    assert refs[0].playlist_title == "PL title" and refs[0].playlist_index == 1
    assert refs[1].url == "https://www.youtube.com/watch?v=bbbbbbbbbbb" and refs[1].playlist_index == 3


def test_refs_from_single_video_and_empty_playlist():
    refs = youtube.refs_from_info({"id": "ccccccccccc", "title": "C", "webpage_url": "https://www.youtube.com/watch?v=ccccccccccc"}, "x")
    assert len(refs) == 1 and refs[0].title == "C"
    with pytest.raises(TranscriptError):
        youtube.refs_from_info({"_type": "playlist", "entries": [None]}, "x")


def test_resolve_single_video_needs_no_network(settings):
    refs = youtube.resolve("https://youtu.be/dQw4w9WgXcQ", settings)
    assert len(refs) == 1 and refs[0].id == "dQw4w9WgXcQ"
    refs = youtube.resolve("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabcdefghijklmn", settings, expand_playlists=False)
    assert len(refs) == 1 and refs[0].id == "dQw4w9WgXcQ"


class _FakeResp:
    def __init__(self, data):
        self.data = data

    def read(self):
        return self.data


class _FakeYdl:
    def __init__(self, responses):
        self.responses = responses

    def urlopen(self, url):
        r = self.responses[url]
        if isinstance(r, Exception):
            raise r
        return _FakeResp(r)


def test_fetch_captions_falls_back_to_vtt():
    track = youtube.CaptionTrack(lang="en", kind="auto", formats=[
        {"ext": "vtt", "url": "v"}, {"ext": "json3", "url": "j"},
    ])
    ydl = _FakeYdl({"j": b"", "v": b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhi\n"})
    segs = youtube.fetch_captions(ydl, track)
    assert [s.text for s in segs] == ["hi"]
    ydl = _FakeYdl({"j": RuntimeError("boom"), "v": b"WEBVTT\n"})
    with pytest.raises(youtube.NoCaptionsError):
        youtube.fetch_captions(ydl, track)


def test_clean_error():
    assert youtube.clean_error(Exception("ERROR: [youtube] abc: Video unavailable; please report this issue on ...")) == "abc: Video unavailable"
