"""LENTA Media Server: FastAPI application."""
import asyncio
import mimetypes
import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import (about, api_admin, api_auth, api_editor, api_extras, api_library, api_livetv, api_network, api_playback,
               api_prefs, api_streaming, api_subtitles, api_system, certs, epg, intros, livestream, livetv, logs,
               scanner, streaming, trailers, transcode, trickplay, watcher)
from .config import APP_NAME, TMDB_KEY_ENV, VERSION, WEB_DIR, ensure_dirs
from .db import get_setting, init_db, set_setting

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
        if minutes == 5 or minutes % 720 == 0:       # what the streaming services are showing: twice a day
            streaming.request_refresh()              # (the fetch itself only runs when a day has passed)
        try:
            interval = int(get_setting("scan_interval_minutes") or 0)
        except ValueError:
            interval = 0
        if interval > 0 and elapsed >= interval:
            elapsed = 0
            if not scanner.state["running"]:
                # The safety net, not the way new media arrives: the watcher sees a new film within a
                # minute and scans only its folder. This walks the libraries to catch what a folder's
                # modification time cannot show, and stops the moment it finds nothing changed.
                log.debug("Checking the libraries for changes")
                scanner.request_scan(None, periodic=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logs.setup_logging()
    ensure_dirs()
    init_db()
    # LENTA_TMDB_KEY: for the person who would rather put their key in a compose file than click
    # through a settings page. Only ever fills an empty setting — a key entered in Settings is theirs
    # and is never overwritten by the environment.
    if TMDB_KEY_ENV and not get_setting("tmdb_api_key"):
        set_setting("tmdb_api_key", TMDB_KEY_ENV)
        log.info("TMDB key taken from LENTA_TMDB_KEY")
    log.info("%s %s starting", APP_NAME, VERSION)
    transcode.start_housekeeping()
    threading.Thread(target=transcode.detect_hardware, daemon=True).start()
    # this machine's names and addresses, asked for once here rather than while someone waits for a page
    threading.Thread(target=certs.local_names, name="lenta-names-warm", daemon=True).start()
    livetv.start_background()           # finds USB tuners and scans a new one, quietly
    epg.start_sweeping()                # fills in the guide for multiplexes nobody is watching
    task = asyncio.create_task(_scheduled_scans())
    yield
    task.cancel()
    transcode.stop_all()
    livestream.stop_all()               # let go of the tuners before the process goes away


# The schema and the documentation page answered **anyone**, signed in or not: a stranger on the network
# got every path, parameter and schema in one request. That is not a hole by itself — it is the map an
# attacker would otherwise have to draw by hand. They are off unless asked for, and nothing in the client
# reads them. `LENTA_API_DOCS=1` brings them back for development.
_DOCS = os.environ.get("LENTA_API_DOCS") == "1"
app = FastAPI(title=APP_NAME, version=VERSION, lifespan=lifespan, redoc_url=None,
              docs_url="/api/docs" if _DOCS else None,
              openapi_url="/api/openapi.json" if _DOCS else None)
app.include_router(api_auth.router)
app.include_router(api_library.router)
app.include_router(api_playback.router)
app.include_router(api_admin.router)
app.include_router(api_prefs.router)
app.include_router(api_subtitles.router)
app.include_router(api_extras.router)
app.include_router(about.router)
app.include_router(api_system.router)
app.include_router(api_editor.router)
app.include_router(api_network.router)
app.include_router(api_streaming.router)
app.include_router(api_streaming.read)
app.include_router(api_network.public)
app.include_router(api_livetv.router)
app.include_router(api_livetv.read)
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


# What the browser is told to enforce on LENTA's behalf. There was none of this before 1.19.0.
#
# `frame-ancestors 'none'` is the one with teeth: nothing may put LENTA in a frame, which closes
# clickjacking and removes framing as a route to the client's own message listeners. `X-Frame-Options`
# says the same thing to anything too old to read a CSP.
#
# `script-src` has to keep `'unsafe-inline'` **and** `'unsafe-eval'`, and both are load-bearing:
# `web/index.html` bootstraps with inline script, and it chooses between the three television clients
# by handing a snippet to `new Function()` to see whether the engine can parse it — which is the only
# way to test for syntax without a parse error taking the whole page with it. Written without
# `'unsafe-eval'`, this CSP made `new Function` throw, `parses()` answer false for everything, and
# **every browser in the house fall through to the Chromium-47 bundle.** `tests/test_about_page.py`
# caught it; it would otherwise have been a silent, total downgrade of the client.
#
# So the CSP does **not** stop an injected script, and it is not pretended that it does — `html`
# escaping in `ui.js` and `safeUrl` on every link from a third party are what do that job. Since
# `'unsafe-inline'` has to be there anyway, `'unsafe-eval'` alongside it costs nothing further. What
# the directive still buys is that an injected `<script src>` can only name this server.
#
# `connect-src 'self'` keeps an injected script from posting what it reads to another host, and
# `object-src 'none'` and `base-uri 'self'` close two old tricks for nothing.
#
# **The media and image hosts are not a guess** — they are the same ones the server already enforces.
# `trailer_sources.valid_key()` accepts a media address only if it is a local `/api/items/.../trailer-file`
# or matches `_APPLE_HOST` (`*.apple.com` / `*.mzstatic.com`), so those two plus `'self'` are the complete
# set the client can ever be handed. Apple serves both the theme-music previews and the Apple TV
# trailers from there, and leaving them out of `media-src` is what silently killed **theme music** in
# 1.19.0 — the audio element was blocked and the failure surfaced only as nothing playing.
# `i.ytimg.com` is the YouTube thumbnail strip in the trailer picker (`editor.js`), the one external
# image the client loads directly rather than through `/api/img`.
# `blob:`/`data:` are needed by hls.js and by the artwork the client draws itself.
CSP = ("default-src 'self'; "
       "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
       "style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob: https://i.ytimg.com; "
       "media-src 'self' data: blob: https://*.apple.com https://*.mzstatic.com; "
       "font-src 'self' data:; "
       "connect-src 'self'; "
       "frame-src 'self' https://www.youtube-nocookie.com https://www.youtube.com; "
       "object-src 'none'; "
       "base-uri 'self'; "
       "form-action 'self'; "
       "frame-ancestors 'none'")

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    # A stream URL may legitimately carry ?token= (see auth.QUERY_TOKEN_PATHS), so no address LENTA
    # serves may travel to another site in a Referer header.
    "Referrer-Policy": "no-referrer",
}


@app.middleware("http")
async def revalidate_web_files(request, call_next):
    """Browsers must check the web client's files on every load (a quick 304 when unchanged), so
    an update is picked up by a normal refresh instead of an old cached copy hanging around.
    Also where LENTA states what the browser should enforce for it (see SECURITY_HEADERS)."""
    response = await call_next(request)
    if request.url.path.endswith((".js", ".css", ".html", "/")) and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-cache"
    for header, value in SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    return response
