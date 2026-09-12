import pytest

from yt_transcript import urls


@pytest.mark.parametrize(
    "raw,kind,video,playlist",
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "video", "dQw4w9WgXcQ", None),
        ("https://youtu.be/dQw4w9WgXcQ", "video", "dQw4w9WgXcQ", None),
        ("https://youtu.be/dQw4w9WgXcQ?si=abc&t=42", "video", "dQw4w9WgXcQ", None),
        ("https://m.youtube.com/watch?v=dQw4w9WgXcQ&feature=share", "video", "dQw4w9WgXcQ", None),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "video", "dQw4w9WgXcQ", None),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", "video", "dQw4w9WgXcQ", None),
        ("https://www.youtube.com/live/dQw4w9WgXcQ?feature=share", "video", "dQw4w9WgXcQ", None),
        ("https://music.youtube.com/watch?v=dQw4w9WgXcQ&list=RDAMVMdQw4w9WgXcQ", "video", "dQw4w9WgXcQ", None),
        ("youtube.com/watch?v=dQw4w9WgXcQ", "video", "dQw4w9WgXcQ", None),
        ("dQw4w9WgXcQ", "video", "dQw4w9WgXcQ", None),
        ("https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf", "playlist", None, "PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf&index=2", "video", "dQw4w9WgXcQ", "PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"),
        ("https://www.youtube.com/watch?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf", "playlist", None, "PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"),
        ("https://www.youtube.com/@veritasium/videos", "unknown", None, None),
        ("https://vimeo.com/123456", "unknown", None, None),
    ],
)
def test_parse(raw, kind, video, playlist):
    p = urls.parse(raw)
    assert p.kind == kind
    assert p.video_id == video
    assert p.playlist_id == playlist


def test_parse_extracts_url_from_share_text():
    p = urls.parse("Check this out! https://youtu.be/dQw4w9WgXcQ via YouTube")
    assert p.kind == "video" and p.video_id == "dQw4w9WgXcQ"


def test_mix_playlists_are_ignored():
    p = urls.parse("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ")
    assert p.kind == "video" and p.playlist_id is None


def test_normalised_urls():
    assert urls.parse("https://youtu.be/dQw4w9WgXcQ").url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert urls.parse("https://www.youtube.com/playlist?list=PLabcdefghijklmn").url.endswith("list=PLabcdefghijklmn")


def test_empty_raises():
    with pytest.raises(ValueError):
        urls.parse("   ")


def test_split_input():
    assert urls.split_input("https://a.com/x\nhttps://b.com/y, https://c.com/z.") == ["https://a.com/x", "https://b.com/y", "https://c.com/z"]
    assert urls.split_input("Watch this https://youtu.be/dQw4w9WgXcQ now") == ["https://youtu.be/dQw4w9WgXcQ"]
    assert urls.split_input("youtu.be/dQw4w9WgXcQ dQw4w9WgXcQ") == ["youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"]
    assert urls.split_input("no link here") == []
    assert urls.split_input("") == []
