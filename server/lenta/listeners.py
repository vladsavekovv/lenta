"""HTTP and HTTPS side by side in one process, changed live from Server admin › Settings.

python -m lenta runs Manager.run(): the app's startup/shutdown happens once, and each protocol gets its
own uvicorn server on its own port, sharing that one app. apply() compares what the settings ask for
with what is running and starts, stops or restarts only the listener that changed, without restarting
LENTA (playback on the other protocol carries on).

Safety: at least one protocol stays on (the settings page and the API refuse otherwise); and if at
start nothing could listen (port taken, broken certificate), plain HTTP comes up on LENTA_PORT anyway
(/etc/lenta/lenta.env), so the administrator can always get back in to fix it.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket

import uvicorn

from . import certs
from .config import HOST, PORT
from .db import get_setting

log = logging.getLogger("lenta.listeners")

PROTOCOLS = ("http", "https")
CERT_CHECK_SECONDS = 3 * 3600          # notice renewed certificate files (Let's Encrypt) and IP changes
MANAGER: "Manager | None" = None


class _Server(uvicorn.Server):
    """A uvicorn server that leaves signals to the manager (several run in one event loop)."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield

    def install_signal_handlers(self) -> None:      # older uvicorn versions
        pass


def wanted() -> dict:
    """What the settings ask for: {'http': {'on', 'port'}, 'https': {...}}."""
    def port(key: str, default: int) -> int:
        try:
            p = int(get_setting(key) or default)
        except ValueError:
            p = default
        return p if 1 <= p <= 65535 else default
    return {
        "http": {"on": get_setting("net_http_enabled") != "0", "port": port("net_http_port", PORT)},
        "https": {"on": get_setting("net_https_enabled") != "0", "port": port("net_https_port", 8443)},
    }


def _bind(port: int) -> socket.socket:
    family = socket.AF_INET6 if ":" in HOST else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((HOST, port))
    except OSError:
        sock.close()
        raise
    sock.setblocking(False)
    return sock


def port_problem(port: int) -> str | None:
    """Why LENTA can't listen on this port, or None. Ports LENTA already listens on count as free."""
    if MANAGER and any(r["port"] == port for r in MANAGER.running.values()):
        return None
    if port < 1024:
        return f"Port {port} is reserved for the system; use 1024 or higher (e.g. 8443)"
    try:
        _bind(port).close()
    except PermissionError:
        return f"LENTA isn't allowed to use port {port}"
    except OSError:
        return f"Port {port} is already used by another program on this server"
    return None


def _why(exc: OSError, port: int) -> str:
    if isinstance(exc, PermissionError) or port < 1024:
        return f"not allowed to use port {port} (use 1024 or higher)"
    return f"port {port} is already used by another program"


class Manager:
    def __init__(self, app) -> None:
        self.app = app
        self.running: dict[str, dict] = {}          # proto -> {server, task, port, stamp}
        self.status: dict[str, dict] = {p: {"state": "off", "error": None, "port": None} for p in PROTOCOLS}
        self.fallback = False
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self.loop: asyncio.AbstractEventLoop | None = None

    # ---- one listener -----------------------------------------------------------------------
    async def _start(self, proto: str, port: int, files=None, stamp: str = "") -> bool:
        try:
            sock = _bind(port)
        except OSError as exc:
            self.status[proto] = {"state": "error", "port": port, "error": _why(exc, port)}
            log.error("%s can't start: %s", proto.upper(), self.status[proto]["error"])
            return False
        kw = {}
        if files:
            kw = {"ssl_certfile": str(files[0]), "ssl_keyfile": str(files[1])}
        # `forwarded_allow_ips` was `"*"`: any client's X-Forwarded-Proto was believed, and that header
        # decides request.url.scheme, which in turn picks the cookie name and whether it is marked
        # Secure. A network attacker could therefore make a plain-HTTP sign-in issue a Secure cookie
        # the browser would never send back. Only a proxy on this machine is believed now;
        # LENTA_TRUSTED_PROXIES widens it for someone genuinely running one in front.
        trusted = os.environ.get("LENTA_TRUSTED_PROXIES", "127.0.0.1,::1")
        cfg = uvicorn.Config(self.app, host=HOST, port=port, lifespan="off", proxy_headers=True,
                             forwarded_allow_ips=trusted, log_level="warning", timeout_keep_alive=30,
                             timeout_graceful_shutdown=3, **kw)
        try:
            cfg.load()
        except Exception as exc:                    # e.g. a certificate the TLS library refuses
            sock.close()
            self.status[proto] = {"state": "error", "port": port, "error": f"the certificate can't be used: {exc}"}
            log.error("%s can't start: %s", proto.upper(), exc)
            return False
        server = _Server(cfg)
        task = asyncio.create_task(server.serve(sockets=[sock]))
        for _ in range(200):
            if server.started or task.done():
                break
            await asyncio.sleep(0.025)
        if task.done() and not server.started:
            exc = task.exception() if not task.cancelled() else None
            sock.close()
            self.status[proto] = {"state": "error", "port": port, "error": f"didn't start: {exc}"}
            return False
        self.running[proto] = {"server": server, "task": task, "port": port, "stamp": stamp, "sock": sock}
        self.status[proto] = {"state": "running", "port": port, "error": None}
        log.info("%s listening on port %s", proto.upper(), port)
        return True

    async def _stop_one(self, proto: str) -> None:
        r = self.running.pop(proto, None)
        if not r:
            return
        r["server"].should_exit = True
        try:
            await asyncio.wait_for(asyncio.shield(r["task"]), 8)
        except (asyncio.TimeoutError, Exception):
            r["server"].force_exit = True
            with contextlib.suppress(Exception):
                await asyncio.wait_for(r["task"], 3)
        with contextlib.suppress(OSError):
            r["sock"].close()
        log.info("%s on port %s stopped", proto.upper(), r["port"])

    # ---- all of them ------------------------------------------------------------------------
    async def apply(self) -> dict:
        """Make the listeners match the settings. Returns the status."""
        async with self._lock:
            want = wanted()
            files, stamp, cert_error = None, "", None
            if want["https"]["on"]:
                try:
                    files = await asyncio.to_thread(certs.active_files)
                    stamp = certs.stamp(files)
                except Exception as exc:
                    cert_error = str(exc)
            for proto in PROTOCOLS:
                w, cur = want[proto], self.running.get(proto)
                on = w["on"] and not (proto == "https" and cert_error)
                if cur and (not on or cur["port"] != w["port"] or (proto == "https" and cur["stamp"] != stamp)):
                    await self._stop_one(proto)
                    cur = None
                if on and not cur:
                    await self._start(proto, w["port"], files if proto == "https" else None, stamp)
                elif not on:
                    self.status[proto] = ({"state": "error", "port": w["port"], "error": cert_error}
                                          if w["on"] and cert_error else {"state": "off", "port": None, "error": None})
            self.fallback = False
            if not self.running:                    # never leave the administrator locked out
                log.error("Nothing could listen; starting plain HTTP on port %s so you can fix the settings", PORT)
                self.status["http"].setdefault("error", None)
                if await self._start("http", PORT):
                    self.fallback = True
            return self.snapshot()

    def snapshot(self) -> dict:
        return {"http": dict(self.status["http"]), "https": dict(self.status["https"]), "fallback": self.fallback}

    def schedule_apply(self, delay: float = 0.6) -> None:
        """Apply soon, after the current request has been answered (it may be on a listener that moves)."""
        async def later():
            await asyncio.sleep(delay)
            try:
                await self.apply()
            except Exception:
                log.exception("Applying the HTTP/HTTPS settings failed")
        asyncio.get_running_loop().create_task(later())

    async def _watch_certs(self) -> None:
        while True:
            await asyncio.sleep(CERT_CHECK_SECONDS)
            try:
                r = self.running.get("https")
                if not r:
                    continue
                files = await asyncio.to_thread(certs.active_files)     # also renews LENTA's own certificate
                if certs.stamp(files) != r["stamp"]:
                    log.info("The HTTPS certificate changed; reloading it")
                    await self.apply()
            except Exception as exc:
                log.warning("Certificate check: %s", exc)

    async def run(self) -> None:
        global MANAGER
        MANAGER = self
        self.loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError, RuntimeError):
                self.loop.add_signal_handler(sig, self._stop.set)
        async with self.app.router.lifespan_context(self.app):
            await self.apply()
            watch = asyncio.create_task(self._watch_certs())
            await self._stop.wait()
            watch.cancel()
            for proto in list(self.running):
                await self._stop_one(proto)
