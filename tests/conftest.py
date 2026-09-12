import pytest
from fastapi.testclient import TestClient

from yt_transcript.app import create_app
from yt_transcript.config import Settings
from yt_transcript.jobs import JobManager
from yt_transcript.models import Segment, Transcript, TranscriptError, VideoRef


def fake_resolver(url, settings, expand_playlists=True):
    if "fail" in url:
        raise TranscriptError("cannot resolve")
    if "list=PL" in url and expand_playlists:
        return [
            VideoRef(url=f"https://www.youtube.com/watch?v=vid0000000{i}", id=f"vid0000000{i}", title=f"Video {i}",
                     duration=60 * i, playlist_title="My Playlist", playlist_index=i)
            for i in range(1, 4)
        ]
    vid = url.rsplit("v=", 1)[-1][:11] if "v=" in url else "abcdefghijk"
    return [VideoRef(url=f"https://www.youtube.com/watch?v={vid}", id=vid)]


def fake_transcriber(ref, settings, engine="auto", languages=("en",), status=None):
    if status:
        status("Fetching video info")
    if ref.id == "badbadbadba":
        raise TranscriptError("no captions here")
    if ref.id and ref.id.endswith("3"):
        raise TranscriptError("private video")
    segs = [Segment(0.0, 2.0, "Hello there."), Segment(2.0, 2.5, "This is a test."), Segment(8.0, 1.0, "Bye.")]
    return Transcript(
        video_id=ref.id or "x", url=ref.url, title=ref.title or f"Title {ref.id}", segments=segs,
        language=languages[0], source="auto-captions" if engine != "whisper" else "whisper", duration=10.0, channel="Chan",
    )


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data", workers=2)


@pytest.fixture
def manager(settings):
    m = JobManager(settings, resolver=fake_resolver, transcriber=fake_transcriber)
    m.start()
    yield m
    m.stop(wait=True)


@pytest.fixture
def client(settings, manager):
    app = create_app(settings=settings, manager=manager)
    with TestClient(app) as c:
        yield c
