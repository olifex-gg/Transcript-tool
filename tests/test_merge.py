"""Combining several jobs' transcripts into one document."""

PL = "https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"
VID = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
BAD = "https://www.youtube.com/watch?v=badbadbadba"


def make(client, manager, url, **kw):
    job = client.post("/api/jobs", json={"urls": url, **kw}).json()
    manager.wait(job["id"], timeout=10)
    return client.get(f"/api/jobs/{job['id']}").json()


def test_merge_two_jobs_into_one_txt(client, manager):
    a = make(client, manager, VID)
    b = make(client, manager, PL)

    r = client.get("/api/merge", params={"jobs": f"{a['id']},{b['id']}"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert r.headers["content-disposition"].startswith("attachment;")
    assert "Combined%20transcripts.txt" in r.headers["content-disposition"]
    text = r.text

    # Contents block lists every finished video, numbered, with its job in brackets.
    assert text.startswith("Combined transcripts\n3 videos\n")
    assert "  1. Title dQw4w9WgXcQ\n" in text
    assert "  2. Video 1  (My Playlist)\n" in text
    assert "  3. Video 2  (My Playlist)\n" in text
    # The failed third video of the playlist is left out.
    assert "Video 3" not in text

    # One section per video, in the order the jobs were given.
    assert text.count("=" * 72) == 6
    assert text.index("Title dQw4w9WgXcQ\nhttps://") < text.index("Video 1\nhttps://") < text.index("Video 2\nhttps://")
    assert text.count("Hello there. This is a test.") == 3


def test_merge_order_follows_the_request(client, manager):
    a = make(client, manager, VID)
    b = make(client, manager, PL)
    text = client.get("/api/merge", params={"jobs": f"{b['id']},{a['id']}"}).text
    assert text.index("Video 1\nhttps://") < text.index("Title dQw4w9WgXcQ\nhttps://")


def test_merge_single_job_is_named_after_it_and_skips_contents(client, manager):
    a = make(client, manager, VID)
    text = client.get("/api/merge", params={"jobs": a["id"]}).text
    assert text.startswith("=" * 72)  # one video, so no table of contents
    r = client.get("/api/merge", params={"jobs": a["id"]})
    assert "Title%20dQw4w9WgXcQ.txt" in r.headers["content-disposition"]


def test_merge_options(client, manager):
    a = make(client, manager, PL)
    assert "[0:00] Hello there." in client.get("/api/merge", params={"jobs": a["id"], "timestamps": 1}).text
    # inline (no download) omits the attachment header, for the Copy button
    r = client.get("/api/merge", params={"jobs": a["id"], "download": 0})
    assert "content-disposition" not in r.headers
    r = client.get("/api/merge", params={"jobs": a["id"], "title": "My notes"})
    assert "My%20notes.txt" in r.headers["content-disposition"]


def test_merge_markdown_groups_by_job(client, manager):
    a = make(client, manager, VID)
    b = make(client, manager, PL)
    text = client.get("/api/merge", params={"jobs": f"{a['id']},{b['id']}", "format": "md"}).text
    assert text.startswith("# Combined transcripts\n")
    assert "## Title dQw4w9WgXcQ\n" in text and "## My Playlist\n" in text
    # videos are numbered continuously across jobs
    assert "### 1. Title dQw4w9WgXcQ\n" in text
    assert "### 2. Video 1\n" in text and "### 3. Video 2\n" in text
    assert text.count("## My Playlist\n") == 1  # the group heading is not repeated


def test_merge_deduplicates_and_ignores_blanks(client, manager):
    a = make(client, manager, VID)
    text = client.get("/api/merge", params={"jobs": f" {a['id']} , ,{a['id']}"}).text
    assert text.count("Title dQw4w9WgXcQ\nhttps://") == 1


def test_merge_rejects_bad_requests(client, manager):
    a = make(client, manager, VID)
    assert client.get("/api/merge", params={"jobs": ""}).status_code == 400
    assert client.get("/api/merge", params={"jobs": "nope"}).status_code == 404
    assert client.get("/api/merge", params={"jobs": f"{a['id']},nope"}).status_code == 404
    assert client.get("/api/merge", params={"jobs": a["id"], "format": "srt"}).status_code == 400
    assert client.get("/api/merge", params={"jobs": a["id"], "format": "docx"}).status_code == 400


def test_merge_needs_a_finished_transcript(client, manager):
    failed = make(client, manager, BAD)
    assert failed["counts"]["done"] == 0
    r = client.get("/api/merge", params={"jobs": failed["id"]})
    assert r.status_code == 409 and "finished" in r.json()["error"]
    # mixing a finished job with a failed one still works, using what is available
    ok = make(client, manager, VID)
    text = client.get("/api/merge", params={"jobs": f"{failed['id']},{ok['id']}"}).text
    assert "Title dQw4w9WgXcQ" in text


def test_recent_order_is_stable_for_jobs_created_together(client, manager):
    """Several jobs are usually created within the same second; the Recent list
    (which decides merge order) must not shuffle between refreshes."""
    ids = [make(client, manager, VID)["id"] for _ in range(4)]
    listed = [[j["id"] for j in client.get("/api/jobs").json()["jobs"]] for _ in range(5)]
    assert all(page == listed[0] for page in listed), "job order changed between identical requests"
    assert set(listed[0]) == set(ids)
    assert listed[0] == list(reversed(ids)), "newest job should be listed first"
