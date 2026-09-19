"""FastAPI web app: a small JSON API plus the mobile-friendly UI in ./static."""

from __future__ import annotations

import base64
import io
import logging
import secrets
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, desktop, formats, whisper_engine
from .config import Settings
from .formats import FORMATS, MIME_TYPES, filename_for, render, safe_filename
from .jobs import ITEM_DONE, JobManager
from .models import Transcript
from .transcribe import ENGINES

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"


class CreateJobRequest(BaseModel):
    urls: list[str] | str = Field(..., description="One or more YouTube video/playlist URLs")
    language: Optional[str] = Field(None, description='Preferred caption language(s), e.g. "en" or "en,de"')
    engine: Optional[str] = Field(None, description="auto | captions | whisper")
    expand_playlists: Optional[bool] = Field(None, description="Transcribe every video in a playlist URL")


def create_app(
    settings: Optional[Settings] = None,
    manager: Optional[JobManager] = None,
    quit_callback: Optional[Callable[[], None]] = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    manager = manager or JobManager(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        manager.start()
        try:
            yield
        finally:
            manager.stop()

    app = FastAPI(title=desktop.APP_NAME, version=__version__, lifespan=lifespan, docs_url="/api/docs", redoc_url=None)
    app.state.settings = settings
    app.state.manager = manager

    # ------------------------------------------------------------------ auth
    if settings.password:
        expected_user = settings.username
        expected_pass = settings.password

        @app.middleware("http")
        async def basic_auth(request: Request, call_next):
            if request.url.path == "/healthz":
                return await call_next(request)
            header = request.headers.get("authorization", "")
            ok = False
            if header.lower().startswith("basic "):
                try:
                    user, _, pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
                    ok = secrets.compare_digest(user, expected_user) and secrets.compare_digest(pw, expected_pass)
                except Exception:  # noqa: BLE001
                    ok = False
            if not ok:
                return Response(
                    "Authentication required",
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="yt-transcript"'},
                )
            return await call_next(request)

    # ------------------------------------------------------------------ helpers
    def _job_or_404(job_id: str):
        job = manager.get(job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        return job

    def _transcript_or_404(job_id: str, index: int) -> Transcript:
        job = _job_or_404(job_id)
        if index < 0 or index >= len(job.items):
            raise HTTPException(404, "No such item")
        item = job.items[index]
        if item.status != ITEM_DONE:
            raise HTTPException(409, f"Transcript not ready (status: {item.status})")
        t = manager.transcript(job_id, index)
        if t is None:
            raise HTTPException(404, "Transcript file missing")
        return t

    def _check_format(fmt: str) -> str:
        fmt = (fmt or "txt").lower()
        if fmt not in FORMATS:
            raise HTTPException(400, f"format must be one of {', '.join(FORMATS)}")
        return fmt

    def _text_response(body: str, fmt: str, filename: str, download: bool) -> Response:
        headers = {}
        if download:
            headers["Content-Disposition"] = _content_disposition(filename)
        return Response(body, media_type=MIME_TYPES[fmt], headers=headers)

    # ------------------------------------------------------------------ API
    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"ok": True, "version": __version__}

    @app.get("/api/config")
    def api_config() -> dict[str, Any]:
        return {
            "version": __version__,
            "default_language": settings.default_language,
            "default_engine": settings.default_engine,
            "expand_playlists": settings.expand_playlists,
            "engines": list(ENGINES),
            "formats": list(FORMATS),
            "whisper_available": whisper_engine.available(),
            "whisper_model": settings.whisper_model,
            "desktop": settings.desktop,
            "app": desktop.APP_NAME,
        }

    @app.post("/api/jobs", status_code=201)
    def api_create_job(req: CreateJobRequest) -> dict[str, Any]:
        try:
            job = manager.create(req.urls, language=req.language, engine=req.engine, expand_playlists=req.expand_playlists)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return job.to_dict()

    @app.get("/api/jobs")
    def api_list_jobs(limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
        return {"jobs": [j.to_dict(include_items=False) for j in manager.list()[:limit]]}

    @app.get("/api/jobs/{job_id}")
    def api_get_job(job_id: str) -> dict[str, Any]:
        return _job_or_404(job_id).to_dict()

    @app.delete("/api/jobs/{job_id}")
    def api_delete_job(job_id: str) -> dict[str, Any]:
        if not manager.delete(job_id):
            raise HTTPException(404, "Job not found")
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/cancel")
    def api_cancel_job(job_id: str) -> dict[str, Any]:
        job = manager.cancel(job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        return job.to_dict()

    @app.post("/api/jobs/{job_id}/retry")
    def api_retry_job(job_id: str) -> dict[str, Any]:
        job = manager.retry(job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        return job.to_dict()

    @app.get("/api/jobs/{job_id}/items/{index}")
    def api_get_item(
        job_id: str,
        index: int,
        format: str = Query("txt"),
        timestamps: bool = Query(False),
        download: bool = Query(False),
    ) -> Response:
        fmt = _check_format(format)
        t = _transcript_or_404(job_id, index)
        body = render(t, fmt, timestamps=timestamps)
        return _text_response(body, fmt, filename_for(t, fmt), download)

    @app.get("/api/jobs/{job_id}/items/{index}/segments")
    def api_get_item_segments(job_id: str, index: int) -> dict[str, Any]:
        t = _transcript_or_404(job_id, index)
        return t.to_dict()

    @app.get("/api/jobs/{job_id}/zip")
    def api_zip(job_id: str, format: str = Query("txt"), timestamps: bool = Query(False)) -> Response:
        fmt = _check_format(format)
        job = _job_or_404(job_id)
        buf = io.BytesIO()
        count = 0
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for item in job.items:
                if item.status != ITEM_DONE:
                    continue
                t = manager.transcript(job_id, item.index)
                if t is None:
                    continue
                name = filename_for(t, fmt, index=item.index + 1 if len(job.items) > 1 else None)
                zf.writestr(name, render(t, fmt, timestamps=timestamps))
                count += 1
        if count == 0:
            raise HTTPException(409, "No finished transcripts yet")
        filename = f"{safe_filename(job.title or job.id)}.zip"
        return Response(
            buf.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": _content_disposition(filename)},
        )

    @app.get("/api/jobs/{job_id}/combined")
    def api_combined(
        job_id: str,
        format: str = Query("md"),
        timestamps: bool = Query(False),
        download: bool = Query(False),
    ) -> Response:
        fmt = _check_format(format)
        if fmt not in ("md", "txt"):
            raise HTTPException(400, "combined output supports md or txt")
        job = _job_or_404(job_id)
        parts: list[str] = []
        title = job.title or job.id
        if fmt == "md":
            parts.append(f"# {title}\n")
        for item in job.items:
            if item.status != ITEM_DONE:
                continue
            t = manager.transcript(job_id, item.index)
            if t is None:
                continue
            body = formats.to_txt(t, timestamps=timestamps).rstrip("\n")
            if fmt == "md":
                head = [f"## {item.index + 1}. {t.title}" if len(job.items) > 1 else f"## {t.title}", ""]
                head.append(f"- Source: {t.url}")
                if t.duration:
                    head.append(f"- Duration: {formats.fmt_clock(t.duration)}")
                head.append(f"- Transcript: {t.source}")
                parts.append("\n".join(head) + "\n\n" + body + "\n")
            else:
                bar = "=" * 72
                parts.append(f"{bar}\n{t.title}\n{t.url}\n{bar}\n\n{body}\n")
        if not parts or (fmt == "md" and len(parts) == 1):
            raise HTTPException(409, "No finished transcripts yet")
        filename = f"{safe_filename(title)}.{fmt}"
        return _text_response("\n".join(parts), fmt, filename, download)

    # ------------------------------------------------------------------ desktop launcher
    @app.get("/api/desktop")
    def api_desktop() -> dict[str, Any]:
        if not settings.desktop:
            return {"desktop": False}
        return {
            "desktop": True,
            "app": desktop.APP_NAME,
            "version": __version__,
            "port": settings.port,
            "urls": desktop.lan_urls(settings.port),
            "data_dir": str(settings.data_dir),
        }

    @app.get("/api/desktop/qr.svg")
    def api_desktop_qr(url: Optional[str] = None) -> Response:
        if not settings.desktop:
            raise HTTPException(404, "Not running in desktop mode")
        allowed = desktop.lan_urls(settings.port)
        url = url or (allowed[0] if allowed else None)
        if not url or url not in allowed:
            raise HTTPException(400, "url must be one of this computer's addresses")
        import qrcode
        import qrcode.image.svg as qsvg

        img = qrcode.make(url, image_factory=qsvg.SvgPathImage, box_size=10, border=1)
        return Response(img.to_string(encoding="unicode"), media_type="image/svg+xml", headers={"Cache-Control": "no-cache"})

    @app.post("/api/desktop/quit")
    def api_desktop_quit(request: Request) -> dict[str, Any]:
        if not settings.desktop or quit_callback is None:
            raise HTTPException(404, "Not running in desktop mode")
        host = request.client.host if request.client else ""
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise HTTPException(403, "Quit is only allowed from the computer the app runs on")
        quit_callback()
        return {"ok": True}

    # ------------------------------------------------------------------ share-sheet / shortcut entry point
    @app.get("/t")
    def quick_transcribe(
        url: Optional[str] = None,
        text: Optional[str] = None,
        title: Optional[str] = None,
        language: Optional[str] = None,
        engine: Optional[str] = None,
        playlist: Optional[bool] = None,
    ):
        """GET endpoint for share sheets / iOS Shortcuts: /t?url=<youtube url>."""
        blob = " ".join(x for x in (url, text, title) if x)
        try:
            job = manager.create(blob, language=language, engine=engine, expand_playlists=playlist)
        except ValueError:
            return RedirectResponse("/?error=" + "no-url", status_code=303)
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

    # ------------------------------------------------------------------ UI
    index_html = STATIC_DIR / "index.html"

    @app.get("/", include_in_schema=False)
    def ui_index() -> FileResponse:
        return FileResponse(index_html, headers={"Cache-Control": "no-cache"})

    @app.get("/jobs/{job_id}", include_in_schema=False)
    def ui_job(job_id: str) -> FileResponse:
        return FileResponse(index_html, headers={"Cache-Control": "no-cache"})

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def ui_manifest() -> FileResponse:
        return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/sw.js", include_in_schema=False)
    def ui_sw() -> FileResponse:
        return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.exception_handler(HTTPException)
    async def http_exc(request: Request, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code, headers=exc.headers)

    return app


def _content_disposition(filename: str) -> str:
    from urllib.parse import quote

    ascii_name = filename.encode("ascii", "ignore").decode("ascii").replace('"', "") or "transcript"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


# uvicorn entry point: `uvicorn --factory yt_transcript.app:create_app`
