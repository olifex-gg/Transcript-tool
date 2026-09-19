import io
import json
import zipfile

from yt_transcript.jobs import JobManager

PL = "https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"
VID = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def wait(client, manager, job_id):
    manager.wait(job_id, timeout=10)
    return client.get(f"/api/jobs/{job_id}").json()


def test_single_video_job(client, manager):
    r = client.post("/api/jobs", json={"urls": VID, "language": "en"})
    assert r.status_code == 201, r.text
    job = wait(client, manager, r.json()["id"])
    assert job["status"] == "done"
    assert job["counts"]["done"] == 1 and job["title"] == "Title dQw4w9WgXcQ"
    item = job["items"][0]
    assert item["source"] == "auto-captions" and item["words"] == 7

    r = client.get(f"/api/jobs/{job['id']}/items/0?format=txt")
    assert r.text == "Hello there. This is a test.\n\nBye.\n"
    r = client.get(f"/api/jobs/{job['id']}/items/0?format=srt&download=1")
    assert r.headers["content-disposition"].startswith("attachment;")
    assert r.text.startswith("1\n00:00:00,000 --> 00:00:04,500\n")
    r = client.get(f"/api/jobs/{job['id']}/items/0/segments")
    assert len(r.json()["segments"]) == 3
    r = client.get(f"/api/jobs/{job['id']}/items/0?format=docx")
    assert r.status_code == 400


def test_playlist_job_with_partial_failure(client, manager):
    job = wait(client, manager, client.post("/api/jobs", json={"urls": [PL]}).json()["id"])
    assert job["title"] == "My Playlist"
    assert job["status"] == "done"
    c = job["counts"]
    assert c["total"] == 3 and c["done"] == 2 and c["failed"] == 1
    failed = [i for i in job["items"] if i["status"] == "failed"][0]
    assert failed["error"] == "private video" and failed["title"] == "Video 3"

    # zip contains one file per finished video, numbered by playlist position
    r = client.get(f"/api/jobs/{job['id']}/zip?format=md")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert names == ["001 - Video 1.md", "002 - Video 2.md"]

    r = client.get(f"/api/jobs/{job['id']}/combined?format=md")
    assert r.text.startswith("# My Playlist\n\n## 1. Video 1\n")
    assert "## 2. Video 2" in r.text
    r = client.get(f"/api/jobs/{job['id']}/combined?format=txt&timestamps=1")
    assert "[0:00] Hello there." in r.text and "Video 2\nhttps://" in r.text

    # not-ready transcript
    r = client.get(f"/api/jobs/{job['id']}/items/2")
    assert r.status_code == 409


def test_playlist_not_expanded_when_asked(client, manager):
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"
    job = wait(client, manager, client.post("/api/jobs", json={"urls": url, "expand_playlists": False}).json()["id"])
    assert job["counts"]["total"] == 1 and job["items"][0]["video_id"] == "dQw4w9WgXcQ"


def test_multiple_urls_dedup_and_resolve_errors(client, manager):
    job = wait(client, manager, client.post("/api/jobs", json={"urls": [VID, VID, "https://www.youtube.com/watch?v=failfailfai"]}).json()["id"])
    assert job["counts"]["total"] == 1 and job["status"] == "done"
    assert "cannot resolve" in job["error"]


def test_all_failed_then_retry(client, manager, monkeypatch):
    job = wait(client, manager, client.post("/api/jobs", json={"urls": "https://www.youtube.com/watch?v=badbadbadba"}).json()["id"])
    assert job["status"] == "failed" and job["items"][0]["error"] == "no captions here"
    assert client.get(f"/api/jobs/{job['id']}/zip").status_code == 409

    # Fix the "video" and retry
    from tests import conftest

    good = lambda ref, settings, **kw: conftest.fake_transcriber(type(ref)(url=ref.url, id="goodgoodgoo"), settings, **kw)
    monkeypatch.setattr(manager, "transcriber", good)
    r = client.post(f"/api/jobs/{job['id']}/retry")
    assert r.status_code == 200
    job = wait(client, manager, job["id"])
    assert job["status"] == "done" and job["counts"]["done"] == 1


def test_bad_requests(client):
    assert client.post("/api/jobs", json={"urls": "   "}).status_code == 400
    assert client.post("/api/jobs", json={"urls": VID, "engine": "magic"}).status_code == 400
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.delete("/api/jobs/nope").status_code == 404


def test_delete_and_list(client, manager):
    a = wait(client, manager, client.post("/api/jobs", json={"urls": VID}).json()["id"])
    ids = [j["id"] for j in client.get("/api/jobs").json()["jobs"]]
    assert a["id"] in ids
    assert client.delete(f"/api/jobs/{a['id']}").json() == {"ok": True}
    assert client.get(f"/api/jobs/{a['id']}").status_code == 404
    assert not (manager.settings.jobs_dir / a["id"]).exists()


def test_quick_transcribe_redirect_and_ui(client, manager):
    r = client.get("/t", params={"text": "Look: " + VID + " wow"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/jobs/")
    job_id = r.headers["location"].rsplit("/", 1)[1]
    assert wait(client, manager, job_id)["status"] == "done"
    r = client.get("/t", params={"text": "no link here"}, follow_redirects=False)
    assert r.headers["location"] == "/?error=no-url"
    assert "<title>Transcript Tool</title>" in client.get("/").text
    assert client.get(f"/jobs/{job_id}").status_code == 200
    assert client.get("/manifest.webmanifest").json()["share_target"]["action"] == "/"
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/healthz").json()["ok"] is True
    assert "whisper_available" in client.get("/api/config").json()


def test_jobs_persist_and_resume(settings, client, manager):
    a = wait(client, manager, client.post("/api/jobs", json={"urls": PL}).json()["id"])
    manager.stop(wait=True)

    # Simulate a crash mid-run: one item left "running" on disk.
    p = settings.jobs_dir / a["id"] / "job.json"
    d = json.loads(p.read_text())
    d["items"][0]["status"] = "running"
    d["status"] = "running"
    p.write_text(json.dumps(d))

    from tests.conftest import fake_resolver, fake_transcriber

    m2 = JobManager(settings, resolver=fake_resolver, transcriber=fake_transcriber)
    m2.start()
    try:
        job = m2.wait(a["id"], timeout=10)
        assert job.status == "done" and job.counts()["done"] == 2
        assert m2.transcript(a["id"], 0) is not None
    finally:
        m2.stop(wait=True)


def test_basic_auth(settings, manager):
    from fastapi.testclient import TestClient

    from yt_transcript.app import create_app

    settings.password = "secret"
    app = create_app(settings=settings, manager=manager)
    with TestClient(app) as c:
        assert c.get("/healthz").status_code == 200
        r = c.get("/api/config")
        assert r.status_code == 401 and "Basic" in r.headers["www-authenticate"]
        assert c.get("/api/config", auth=("yt", "wrong")).status_code == 401
        assert c.get("/api/config", auth=("yt", "secret")).status_code == 200
