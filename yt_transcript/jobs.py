"""Background job queue with on-disk persistence.

A job is one submission (one or more URLs). Resolving playlists expands a job
into items; each item is one video. Items run on a small thread pool so a
long playlist is processed a few videos at a time. State is written to
``DATA_DIR/jobs/<id>/`` after every change, so jobs survive a restart and a
phone that lost the tab can come back to the results.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from . import transcribe as transcribe_mod
from . import youtube
from .config import Settings
from .models import Transcript, TranscriptError, VideoRef
from .urls import split_input

log = logging.getLogger(__name__)

Resolver = Callable[[str, Settings, bool], list[VideoRef]]
Transcriber = Callable[..., Transcript]

ITEM_QUEUED, ITEM_RUNNING, ITEM_DONE, ITEM_FAILED, ITEM_SKIPPED = "queued", "running", "done", "failed", "skipped"
JOB_QUEUED, JOB_RESOLVING, JOB_RUNNING, JOB_DONE, JOB_FAILED, JOB_CANCELLED = (
    "queued",
    "resolving",
    "running",
    "done",
    "failed",
    "cancelled",
)


def _now() -> str:
    # Microseconds, not seconds: several jobs are often created in the same
    # second and the Recent list (and merge order) must not shuffle between
    # refreshes.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@dataclass
class JobItem:
    index: int
    ref: VideoRef
    status: str = ITEM_QUEUED
    message: Optional[str] = None
    error: Optional[str] = None
    title: Optional[str] = None
    duration: Optional[float] = None
    language: Optional[str] = None
    source: Optional[str] = None
    words: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "ref": self.ref.to_dict(),
            "status": self.status,
            "message": self.message,
            "error": self.error,
            "title": self.title or self.ref.title,
            "duration": self.duration if self.duration is not None else self.ref.duration,
            "language": self.language,
            "source": self.source,
            "words": self.words,
            "video_id": self.ref.id,
            "url": self.ref.url,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "JobItem":
        return cls(
            index=int(d["index"]),
            ref=VideoRef.from_dict(d["ref"]),
            status=d.get("status", ITEM_QUEUED),
            message=d.get("message"),
            error=d.get("error"),
            title=d.get("title"),
            duration=d.get("duration"),
            language=d.get("language"),
            source=d.get("source"),
            words=d.get("words"),
        )


@dataclass
class Job:
    id: str
    inputs: list[str]
    language: str
    engine: str
    expand_playlists: bool
    status: str = JOB_QUEUED
    title: Optional[str] = None
    error: Optional[str] = None
    items: list[JobItem] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    cancelled: bool = False

    @property
    def languages(self) -> list[str]:
        return [lang.strip() for lang in self.language.replace(";", ",").split(",") if lang.strip()] or ["en"]

    def counts(self) -> dict[str, int]:
        c = {k: 0 for k in (ITEM_QUEUED, ITEM_RUNNING, ITEM_DONE, ITEM_FAILED, ITEM_SKIPPED)}
        for it in self.items:
            c[it.status] = c.get(it.status, 0) + 1
        c["total"] = len(self.items)
        return c

    def to_dict(self, include_items: bool = True) -> dict[str, Any]:
        d = {
            "id": self.id,
            "inputs": self.inputs,
            "language": self.language,
            "engine": self.engine,
            "expand_playlists": self.expand_playlists,
            "status": self.status,
            "title": self.title or (self.inputs[0] if self.inputs else self.id),
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "counts": self.counts(),
        }
        if include_items:
            d["items"] = [it.to_dict() for it in self.items]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Job":
        return cls(
            id=d["id"],
            inputs=list(d.get("inputs") or []),
            language=d.get("language") or "en",
            engine=d.get("engine") or "auto",
            expand_playlists=bool(d.get("expand_playlists", True)),
            status=d.get("status", JOB_QUEUED),
            title=d.get("title"),
            error=d.get("error"),
            items=[JobItem.from_dict(i) for i in d.get("items") or []],
            created_at=d.get("created_at") or _now(),
            updated_at=d.get("updated_at") or _now(),
            cancelled=bool(d.get("cancelled", False)),
        )


class JobManager:
    def __init__(
        self,
        settings: Settings,
        resolver: Optional[Resolver] = None,
        transcriber: Optional[Transcriber] = None,
    ) -> None:
        self.settings = settings
        self.resolver: Resolver = resolver or youtube.resolve
        self.transcriber: Transcriber = transcriber or transcribe_mod.transcribe
        self.jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._pool: Optional[ThreadPoolExecutor] = None
        self._last_save: dict[str, float] = {}
        self.settings.jobs_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        if self._pool is not None:
            return
        self._pool = ThreadPoolExecutor(max_workers=self.settings.workers, thread_name_prefix="transcribe")
        self._load_from_disk()
        for job in sorted(self.jobs.values(), key=lambda j: j.created_at):
            self._resume(job)

    def stop(self, wait: bool = False) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=wait, cancel_futures=True)
            self._pool = None

    def _submit(self, fn: Callable[..., None], *args: Any) -> None:
        if self._pool is None:
            raise RuntimeError("JobManager not started")
        self._pool.submit(self._guard, fn, *args)

    @staticmethod
    def _guard(fn: Callable[..., None], *args: Any) -> None:
        try:
            fn(*args)
        except Exception:  # noqa: BLE001
            log.exception("Unhandled error in job worker")

    # ------------------------------------------------------------------ persistence

    def _job_dir(self, job_id: str) -> Path:
        return self.settings.jobs_dir / job_id

    def _save(self, job: Job, force: bool = True) -> None:
        """Write job.json. Message-only updates are throttled to one write per second."""
        now = time.monotonic()
        if not force and now - self._last_save.get(job.id, 0.0) < 1.0:
            return
        with self._lock:
            if job.id not in self.jobs:
                return  # deleted while a worker was still reporting progress
            self._last_save[job.id] = now
            job.updated_at = _now()
            d = self._job_dir(job.id)
            d.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(job.to_dict(include_items=True) | {"cancelled": job.cancelled}, ensure_ascii=False)
            tmp = d / f"job.json.{threading.get_ident()}.tmp"
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, d / "job.json")

    def _save_transcript(self, job: Job, item: JobItem, t: Transcript) -> None:
        d = self._job_dir(job.id) / "items"
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f"{item.index}.json.tmp"
        tmp.write_text(json.dumps(t.to_dict(), ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, d / f"{item.index}.json")

    def transcript(self, job_id: str, index: int) -> Optional[Transcript]:
        p = self._job_dir(job_id) / "items" / f"{index}.json"
        if not p.exists():
            return None
        return Transcript.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def _load_from_disk(self) -> None:
        for d in sorted(self.settings.jobs_dir.iterdir() if self.settings.jobs_dir.exists() else []):
            f = d / "job.json"
            if not f.is_file():
                continue
            try:
                job = Job.from_dict(json.loads(f.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001
                log.exception("Skipping unreadable job file %s", f)
                continue
            self.jobs[job.id] = job

    def _resume(self, job: Job) -> None:
        """Re-queue whatever was in flight when the process last stopped."""
        if job.status in (JOB_DONE, JOB_FAILED, JOB_CANCELLED):
            return
        if job.status in (JOB_QUEUED, JOB_RESOLVING) and not job.items:
            job.status = JOB_QUEUED
            self._save(job)
            self._submit(self._resolve_job, job.id)
            return
        pending = False
        for it in job.items:
            if it.status == ITEM_RUNNING:
                it.status = ITEM_QUEUED
                it.message = None
            if it.status == ITEM_QUEUED:
                pending = True
                self._submit(self._run_item, job.id, it.index)
        if pending:
            job.status = JOB_RUNNING
            self._save(job)
        else:
            self._finish_if_complete(job)

    # ------------------------------------------------------------------ public API

    def create(
        self,
        text_or_urls: str | Sequence[str],
        language: Optional[str] = None,
        engine: Optional[str] = None,
        expand_playlists: Optional[bool] = None,
    ) -> Job:
        urls = split_input(text_or_urls if isinstance(text_or_urls, str) else "\n".join(text_or_urls))
        if not urls:
            raise ValueError("No YouTube link found in the input")
        engine = (engine or self.settings.default_engine or "auto").lower()
        if engine not in transcribe_mod.ENGINES:
            raise ValueError(f"engine must be one of {transcribe_mod.ENGINES}")
        job = Job(
            id=secrets.token_urlsafe(8),
            inputs=urls,
            language=(language or self.settings.default_language or "en").strip(),
            engine=engine,
            expand_playlists=self.settings.expand_playlists if expand_playlists is None else bool(expand_playlists),
        )
        with self._lock:
            self.jobs[job.id] = job
            self._save(job)
        self._submit(self._resolve_job, job.id)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)

    def list(self) -> list[Job]:
        """Newest first. The id breaks ties so the order is stable across calls
        even for jobs written by an older build with second-precision times."""
        return sorted(self.jobs.values(), key=lambda j: (j.created_at, j.id), reverse=True)

    def cancel(self, job_id: str) -> Optional[Job]:
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            job.cancelled = True
            for it in job.items:
                if it.status == ITEM_QUEUED:
                    it.status = ITEM_SKIPPED
            if job.status not in (JOB_DONE, JOB_FAILED):
                job.status = JOB_CANCELLED
            self._save(job)
            return job

    def delete(self, job_id: str) -> bool:
        with self._lock:
            job = self.jobs.pop(job_id, None)
            if job is None:
                return False
            job.cancelled = True
        shutil.rmtree(self._job_dir(job_id), ignore_errors=True)
        return True

    def retry(self, job_id: str) -> Optional[Job]:
        """Re-run failed (and skipped) items, or re-resolve a job that failed before it had items."""
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            job.cancelled = False
            job.error = None
            if not job.items:
                job.status = JOB_QUEUED
                self._save(job)
                self._submit(self._resolve_job, job.id)
                return job
            retried = False
            for it in job.items:
                if it.status in (ITEM_FAILED, ITEM_SKIPPED):
                    it.status, it.error, it.message = ITEM_QUEUED, None, None
                    retried = True
                    self._submit(self._run_item, job.id, it.index)
            if retried:
                job.status = JOB_RUNNING
            self._save(job)
            return job

    # ------------------------------------------------------------------ workers

    def _resolve_job(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job is None or job.cancelled:
            return
        with self._lock:
            job.status = JOB_RESOLVING
            self._save(job)
        refs: list[VideoRef] = []
        errors: list[str] = []
        for url in job.inputs:
            try:
                refs.extend(self.resolver(url, self.settings, job.expand_playlists))
            except (TranscriptError, ValueError) as e:
                errors.append(f"{url}: {e}")
            except Exception as e:  # noqa: BLE001
                log.exception("Resolver crashed for %s", url)
                errors.append(f"{url}: {e}")
        with self._lock:
            if job.cancelled:
                return
            if not refs:
                job.status = JOB_FAILED
                job.error = "; ".join(errors) or "Nothing to transcribe"
                self._save(job)
                return
            # De-duplicate videos that appear more than once (same video pasted twice, etc.)
            seen: set[str] = set()
            items: list[JobItem] = []
            for ref in refs:
                key = ref.id or ref.url
                if key in seen:
                    continue
                seen.add(key)
                items.append(JobItem(index=len(items), ref=ref))
            job.items = items
            job.error = "; ".join(errors) or None
            if not job.title:
                first = refs[0]
                if first.playlist_title:
                    job.title = first.playlist_title
                elif len(items) == 1 and first.title:
                    job.title = first.title
                elif len(items) > 1:
                    job.title = f"{len(items)} videos"
            job.status = JOB_RUNNING
            self._save(job)
        for it in job.items:
            self._submit(self._run_item, job.id, it.index)

    def _run_item(self, job_id: str, index: int) -> None:
        job = self.jobs.get(job_id)
        if job is None or job.cancelled:
            return
        item = job.items[index]
        with self._lock:
            if item.status != ITEM_QUEUED:
                return
            item.status = ITEM_RUNNING
            item.message = "Starting"
            self._save(job)

        def status(msg: str) -> None:
            item.message = msg
            self._save(job, force=False)

        try:
            t = self.transcriber(item.ref, self.settings, engine=job.engine, languages=job.languages, status=status)
        except TranscriptError as e:
            self._item_failed(job, item, str(e))
            return
        except Exception as e:  # noqa: BLE001
            log.exception("Transcriber crashed for %s", item.ref.url)
            self._item_failed(job, item, f"{e.__class__.__name__}: {e}")
            return

        self._save_transcript(job, item, t)
        with self._lock:
            item.status = ITEM_DONE
            item.message = None
            item.error = None
            item.title = t.title
            item.duration = t.duration
            item.language = t.language
            item.source = t.source
            item.words = len(t.text.split())
            if job.title in (None, "", job.inputs[0]) and len(job.items) == 1:
                job.title = t.title
            self._finish_if_complete(job)
            self._save(job)

    def _item_failed(self, job: Job, item: JobItem, error: str) -> None:
        with self._lock:
            item.status = ITEM_FAILED
            item.message = None
            item.error = error
            self._finish_if_complete(job)
            self._save(job)

    def _finish_if_complete(self, job: Job) -> None:
        if any(it.status in (ITEM_QUEUED, ITEM_RUNNING) for it in job.items):
            return
        if job.cancelled:
            job.status = JOB_CANCELLED
            return
        c = job.counts()
        if c["total"] and c[ITEM_DONE] == 0:
            job.status = JOB_FAILED
            if not job.error:
                job.error = "Every video failed"
        else:
            job.status = JOB_DONE

    # ------------------------------------------------------------------ helpers

    def wait(self, job_id: str, timeout: float = 30.0) -> Job:
        """Block until a job stops running (used by the CLI and tests)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if job.status in (JOB_DONE, JOB_FAILED, JOB_CANCELLED):
                return job
            time.sleep(0.05)
        raise TimeoutError(f"Job {job_id} still running after {timeout}s")
