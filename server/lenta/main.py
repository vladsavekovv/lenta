"""LENTA Media Server: FastAPI application."""
import asyncio
import mimetypes
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import (about, api_admin, api_auth, api_editor, api_extras, api_library, api_playback, api_prefs, api_subtitles, logs,
               intros, scanner, trailers, transcode, trickplay, watcher)
from .config import APP_NAME, VERSION, WEB_DIR, ensure_dirs
from .db import get_setting, init_db

log = logs.get("server")
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("application/vnd.android.package-archive", ".apk")
mimetypes.add_type("application/widget", ".wgt")
mimetypes.add_type("text/plain", ".sh")


async def _scheduled_scans() -> None:
    await asyncio.sleep(10)
    watcher.start()                      # new media in library folders is noticed and scanned within a minute or two
    about.request_refresh()              # About panel details for titles matched before an upgrade
    elapsed = 0
    minutes = 0
    while True:
        await asyncio.sleep(60)
        elapsed += 1
        minutes += 1
        if minutes == 1 or minutes % 1440 == 0:      # trailer index: a minute after start, then daily
            trailers.request_index()
        if minutes == 3:                             # seek-bar thumbnails: start the background worker
            trickplay.start()
        if minutes == 4:                             # intro detection for TV shows
            intros.start()
        try:
            interval = int(get_setting("scan_interval_minutes") or 0)
        except ValueError:
            interval = 0
        if interval > 0 and elapsed >= interval:
            elapsed = 0
            if not scanner.state["running"]:
                log.info("Scheduled library scan")
                scanner.request_scan(None)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logs.setup_logging()
    ensure_dirs()
    init_db()
    log.info("%s %s starting", APP_NAME, VERSION)
    transcode.start_housekeeping()
    threading.Thread(target=transcode.detect_hardware, daemon=True).start()
    task = asyncio.create_task(_scheduled_scans())
    yield
    task.cancel()
    transcode.stop_all()


app = FastAPI(title=APP_NAME, version=VERSION, lifespan=lifespan, docs_url="/api/docs", redoc_url=None,
              openapi_url="/api/openapi.json")
app.include_router(api_auth.router)
app.include_router(api_library.router)
app.include_router(api_playback.router)
app.include_router(api_admin.router)
app.include_router(api_prefs.router)
app.include_router(api_subtitles.router)
app.include_router(api_extras.router)
app.include_router(about.router)
app.include_router(api_editor.router)
app.include_router(trickplay.router)
app.include_router(intros.router)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Unhandled error on %s: %s", request.url.path, exc)
    return JSONResponse({"detail": "The server hit an unexpected error. Details are in the server log."},
                        status_code=500)


# ---- LENTA Web Client ------------------------------------------------------------
# The client is a separate static app; the server hosts it for convenience.
if WEB_DIR.exists():
    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/", StaticFiles(directory=WEB_DIR), name="web")


@app.middleware("http")
async def revalidate_web_files(request, call_next):
    """Browsers must check the web client's files on every load (a quick 304 when unchanged), so
    an update is picked up by a normal refresh instead of an old cached copy hanging around."""
    response = await call_next(request)
    if request.url.path.endswith((".js", ".css", ".html", "/")) and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-cache"
    return response
