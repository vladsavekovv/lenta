"""HTTP and HTTPS settings (Server admin › Settings › HTTP and HTTPS) and the certificate download."""
from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from . import auth, certs, listeners
from .config import PORT
from .db import get_setting, set_setting

router = APIRouter(prefix="/api/admin/network", dependencies=[Depends(auth.admin_user)])
public = APIRouter()


def _addresses() -> list[str]:
    _dns, ips = certs.local_names()
    return [ip for ip in ips if ip not in ("127.0.0.1", "::1") and ":" not in ip][:4]


def _state(request: Request) -> dict:
    w = listeners.wanted()
    m = listeners.MANAGER
    return {
        "managed": m is not None,
        "http_enabled": w["http"]["on"], "http_port": w["http"]["port"],
        "https_enabled": w["https"]["on"], "https_port": w["https"]["port"],
        "default_http_port": PORT,
        "cert_mode": certs.mode(),
        "cert_names": get_setting("net_cert_names"),
        "cert_path_crt": get_setting("net_cert_path_crt"),
        "cert_path_key": get_setting("net_cert_path_key"),
        "status": m.snapshot() if m else None,
        "certificate": certs.info(),
        "addresses": _addresses(),
        "this": {"scheme": request.url.scheme, "port": request.url.port, "host": request.url.hostname},
    }


@router.get("")
def network(request: Request):
    return _state(request)


class NetworkBody(BaseModel):
    http_enabled: bool
    http_port: int
    https_enabled: bool
    https_port: int
    cert_mode: str = "auto"
    cert_names: str = ""
    cert_path_crt: str = ""
    cert_path_key: str = ""


@router.put("")
async def save_network(body: NetworkBody, request: Request):
    if not body.http_enabled and not body.https_enabled:
        raise HTTPException(400, "Keep HTTP or HTTPS on: with both off nobody could reach LENTA")
    for label, port in (("HTTP", body.http_port), ("HTTPS", body.https_port)):
        if not 1024 <= port <= 65535:
            raise HTTPException(400, f"The {label} port must be between 1024 and 65535")
    if body.http_enabled and body.https_enabled and body.http_port == body.https_port:
        raise HTTPException(400, "HTTP and HTTPS need different ports")
    if body.cert_mode not in ("auto", "upload", "path"):
        raise HTTPException(400, "Unknown certificate choice")
    try:
        names = certs.parse_extra_names(body.cert_names)
    except certs.CertError as exc:
        raise HTTPException(400, str(exc))
    if body.https_enabled:
        if body.cert_mode == "upload" and not (certs.OWN_CRT.exists() and certs.OWN_KEY.exists()):
            raise HTTPException(400, "Upload your certificate and key first")
        if body.cert_mode == "path":
            try:
                certs.check_paths(body.cert_path_crt.strip(), body.cert_path_key.strip())
            except certs.CertError as exc:
                raise HTTPException(400, str(exc))
            except Exception as exc:
                raise HTTPException(400, f"The certificate files can't be used: {exc}")
    want = listeners.wanted()
    for proto, on, port in (("http", body.http_enabled, body.http_port), ("https", body.https_enabled, body.https_port)):
        if on and (not want[proto]["on"] or want[proto]["port"] != port):
            problem = listeners.port_problem(port)
            if problem:
                raise HTTPException(400, f"{proto.upper()}: {problem}")
    set_setting("net_http_enabled", "1" if body.http_enabled else "0")
    set_setting("net_http_port", "" if body.http_port == PORT else body.http_port)
    set_setting("net_https_enabled", "1" if body.https_enabled else "0")
    set_setting("net_https_port", body.https_port)
    set_setting("net_cert_mode", body.cert_mode)
    set_setting("net_cert_names", names)
    set_setting("net_cert_path_crt", body.cert_path_crt.strip())
    set_setting("net_cert_path_key", body.cert_path_key.strip())
    if listeners.MANAGER:
        listeners.MANAGER.schedule_apply()
    return {"ok": True, "applied": listeners.MANAGER is not None}


class CertUpload(BaseModel):
    files: list[str]            # base64 file contents (certificate, key, or one .pfx / combined .pem)
    password: str = ""


@router.post("/certificate")
def upload_certificate(body: CertUpload):
    if not body.files or len(body.files) > 4:
        raise HTTPException(400, "Choose your certificate and key files")
    try:
        blobs = [base64.b64decode(f) for f in body.files]
    except ValueError:
        raise HTTPException(400, "A file couldn't be read")
    if sum(len(b) for b in blobs) > 200_000:
        raise HTTPException(400, "Those files are too big to be a certificate and key")
    try:
        cert = certs.save_own(blobs, body.password or None)
    except certs.CertError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(400, f"The files can't be read: {exc}")
    if listeners.MANAGER and certs.mode() == "upload":
        listeners.MANAGER.schedule_apply()          # replacing a certificate in use: reload it
    return {"ok": True, "certificate": cert}


@router.post("/regenerate")
def regenerate():
    certs.make_auto(force=True)
    if listeners.MANAGER and certs.mode() == "auto":
        listeners.MANAGER.schedule_apply()
    return {"ok": True, "certificate": certs.describe(certs.AUTO_CRT)}


@router.get("/status")
def status():
    m = listeners.MANAGER
    return {"status": m.snapshot() if m else None, "certificate": certs.info()}


@public.get("/api/https/lenta-ca.crt", include_in_schema=False)
def download_ca():
    """LENTA's own certificate authority: install it on a device once and the browser trusts LENTA's HTTPS."""
    return Response(certs.ca_pem(), media_type="application/x-x509-ca-cert",
                    headers={"Content-Disposition": 'attachment; filename="lenta-ca.crt"',
                             "Cache-Control": "no-store"})
