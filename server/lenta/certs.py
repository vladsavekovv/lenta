"""HTTPS certificates: LENTA's own (automatic) or yours (uploaded, or files already on the server).

Automatic mode works like mkcert: LENTA makes a small certificate authority once ("LENTA local CA",
10 years) and signs a server certificate with it for this machine's names and IP addresses. Browsers
warn about it until the CA certificate is installed on the device (Server admin › Settings › HTTPS ›
Download); after that the warning is gone for good, also when the server certificate is renewed or the
IP address changes (it is re-signed by the same CA). Server certificates last 825 days (Apple's limit)
and are renewed by themselves 30 days before they expire, or as soon as a name or address is missing.

Your own certificate: PEM files (certificate with its chain + private key, also both in one file) or a
PKCS#12 bundle (.pfx/.p12, with its password). They are checked and stored as PEM in the data folder.
Or point LENTA at files on the server (e.g. Let's Encrypt's fullchain.pem and privkey.pem): LENTA reads
them where they are, so renewals are picked up (it looks every few hours).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import os
import re
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .config import DATA_DIR
from .db import get_setting

CERT_DIR = DATA_DIR / "certs"
CA_CRT, CA_KEY = CERT_DIR / "lenta-ca.crt", CERT_DIR / "lenta-ca.key"
AUTO_CRT, AUTO_KEY = CERT_DIR / "server.crt", CERT_DIR / "server.key"
OWN_CRT, OWN_KEY = CERT_DIR / "own.crt", CERT_DIR / "own.key"

LEAF_DAYS = 825
RENEW_DAYS = 30


class CertError(ValueError):
    """A certificate problem worth showing to the administrator as is."""


# ---- names and addresses of this machine -------------------------------------------------------

_names_cache: tuple[list[str], list[str]] | None = None
_names_at = 0.0
_names_lock = threading.Lock()
NAMES_TTL = 300          # seconds; a machine's own name and addresses do not change between page loads


def local_names() -> tuple[list[str], list[str]]:
    """(DNS names, IP addresses) this server is likely reached by on the home network — from a cache.

    _local_names() asks the resolver and runs a program, and on a network whose DNS server is slow or
    away (a domain controller that is off, a VPN that is up) the reverse lookup inside it waits for the
    resolver's own timeout — five seconds, typically, per nameserver. That used to happen on every
    request for the Network page, and the Settings pages ask for it too, which is why they sometimes
    took longer to appear than anything else in LENTA. So: answer from the cache, and work out the new
    answer in a thread of its own, where nobody is waiting for it.
    """
    global _names_cache, _names_at
    fresh = _names_cache is not None and (time.monotonic() - _names_at) < NAMES_TTL
    if _names_cache is not None and not fresh and _names_lock.acquire(blocking=False):
        def refresh() -> None:
            global _names_cache, _names_at
            try:
                got = _local_names()
                _names_cache, _names_at = got, time.monotonic()
            finally:
                _names_lock.release()
        threading.Thread(target=refresh, name="lenta-names", daemon=True).start()
    if _names_cache is None:                      # the very first call: warmed at start-up, see main.py
        with _names_lock:
            if _names_cache is None:
                _names_cache, _names_at = _local_names(), time.monotonic()
    dns, ips = _names_cache
    return list(dns), list(ips)                   # callers append to these (_wanted)


def _local_names() -> tuple[list[str], list[str]]:
    """(DNS names, IP addresses) this server is likely reached by — asked of the system. Can be slow."""
    host = socket.gethostname().split(".")[0] or "lenta"
    dns = [host, f"{host}.local", "localhost"]
    try:
        fqdn = socket.getfqdn()
        if fqdn and "." in fqdn and fqdn not in dns and not fqdn.endswith(".localdomain"):
            dns.append(fqdn)
    except OSError:
        pass
    ips: list[str] = ["127.0.0.1", "::1"]
    try:
        out = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=3).stdout
        ips += out.split()
    except (OSError, subprocess.SubprocessError):
        pass
    try:                                    # the address used for the default route (no packet is sent)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))
        ips.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    clean: list[str] = []
    for ip in ips:
        try:
            a = ipaddress.ip_address(ip.split("%")[0])
        except ValueError:
            continue
        if a.is_link_local or str(a) in clean:
            continue
        clean.append(str(a))
    return dns, clean


def extra_names() -> list[str]:
    """Names and addresses the administrator added (Settings › HTTPS › Other names), e.g. a domain."""
    return [n for n in re.split(r"[\s,;]+", get_setting("net_cert_names") or "") if n]


def parse_extra_names(text: str) -> str:
    out = []
    for n in re.split(r"[\s,;]+", text or ""):
        if not n:
            continue
        n = n.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]
        try:
            out.append(str(ipaddress.ip_address(n)))
            continue
        except ValueError:
            pass
        if not re.fullmatch(r"(\*\.)?[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*", n):
            raise CertError(f"“{n}” is not a host name or IP address")
        out.append(n)
    return ", ".join(dict.fromkeys(out))


def _wanted() -> tuple[list[str], list[str]]:
    dns, ips = local_names()
    for n in extra_names():
        try:
            ip = str(ipaddress.ip_address(n))
            if ip not in ips:
                ips.append(ip)
        except ValueError:
            if n not in dns:
                dns.append(n)
    return dns, ips


# ---- files ---------------------------------------------------------------------------------------

def _write(path: Path, data: bytes, secret: bool = False) -> None:
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CERT_DIR, 0o700)
    fd, tmp = tempfile.mkstemp(dir=CERT_DIR, prefix=".tmp-")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.chmod(tmp, 0o600 if secret else 0o644)
    os.replace(tmp, path)


def _pem_key(key) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# ---- LENTA's own certificates --------------------------------------------------------------------

_ca_cache: tuple[str, object, object] | None = None       # (stamp, certificate, key)


def _ensure_ca():
    global _ca_cache
    if CA_CRT.exists() and CA_KEY.exists():
        # Loading the authority's own key parses an RSA key and checks it over, which takes about as long
        # as everything else on the Network page put together. It is the same two files every time, so
        # keep it, and notice by their mtime and size if they are ever replaced.
        mark = stamp((CA_CRT, CA_KEY))
        if _ca_cache and _ca_cache[0] == mark:
            return _ca_cache[1], _ca_cache[2]
        ca = x509.load_pem_x509_certificate(CA_CRT.read_bytes())
        key = serialization.load_pem_private_key(CA_KEY.read_bytes(), None)
        if ca.not_valid_after_utc > _now() + dt.timedelta(days=LEAF_DAYS):
            _ca_cache = (mark, ca, key)
            return ca, key
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    host = socket.gethostname().split(".")[0] or "server"
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"LENTA local CA ({host})"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "LENTA Media Server")])
    ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
    ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
          .serial_number(x509.random_serial_number())
          .not_valid_before(_now() - dt.timedelta(days=1)).not_valid_after(_now() + dt.timedelta(days=3650))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                       content_commitment=False, key_encipherment=False, data_encipherment=False,
                                       key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
          .add_extension(ski, critical=False)
          .sign(key, hashes.SHA256()))
    _write(CA_KEY, _pem_key(key), secret=True)
    _write(CA_CRT, ca.public_bytes(serialization.Encoding.PEM))
    AUTO_CRT.unlink(missing_ok=True)            # a new CA means new server certificates
    _ca_cache = (stamp((CA_CRT, CA_KEY)), ca, key)
    return ca, key


def _auto_needs_renewal() -> str | None:
    """Why the automatic server certificate must be (re)made, or None."""
    if not (AUTO_CRT.exists() and AUTO_KEY.exists()):
        return "missing"
    try:
        cert = x509.load_pem_x509_certificate(AUTO_CRT.read_bytes())
    except ValueError:
        return "unreadable"
    if cert.not_valid_after_utc < _now() + dt.timedelta(days=RENEW_DAYS):
        return "expiring"
    dns, ips = _names_in(cert)
    want_dns, want_ips = _wanted()
    if not set(want_dns) <= set(dns) or not set(want_ips) <= set(ips):
        return "names"
    return None


def make_auto(force: bool = False) -> bool:
    """Make sure the automatic server certificate exists and fits; returns True when a new one was made."""
    ca, ca_key = _ensure_ca()
    if not force and not _auto_needs_renewal():
        return False
    dns, ips = _wanted()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)     # RSA: older TVs and phones
    host = socket.gethostname().split(".")[0] or "lenta"
    sans = [x509.DNSName(d) for d in dns] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
            .issuer_name(ca.subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(_now() - dt.timedelta(days=1)).not_valid_after(_now() + dt.timedelta(days=LEAF_DAYS))
            .add_extension(x509.SubjectAlternativeName(sans), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=True, content_commitment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    _write(AUTO_KEY, _pem_key(key), secret=True)
    _write(AUTO_CRT, cert.public_bytes(serialization.Encoding.PEM) + ca.public_bytes(serialization.Encoding.PEM))
    return True


def ca_pem() -> bytes:
    _ensure_ca()
    return CA_CRT.read_bytes()


# ---- your own certificate ------------------------------------------------------------------------

def _load_any(blobs: list[bytes], password: str | None):
    """Certificates and the private key out of PEM/DER/PKCS#12 data, in any mix."""
    certs: list[x509.Certificate] = []
    key = None
    pw = password.encode() if password else None
    for raw in blobs:
        if not raw.strip():
            continue
        if b"-----BEGIN" in raw:
            for m in re.finditer(rb"-----BEGIN ([A-Z0-9 ]+)-----.+?-----END \1-----", raw, re.S):
                kind, pem = m.group(1), m.group(0)
                if kind in (b"CERTIFICATE", b"TRUSTED CERTIFICATE"):
                    certs.append(x509.load_pem_x509_certificate(pem.replace(b"TRUSTED CERTIFICATE", b"CERTIFICATE")))
                elif b"PRIVATE KEY" in kind:
                    if key is not None:
                        raise CertError("More than one private key was given")
                    try:
                        key = serialization.load_pem_private_key(pem, pw)
                    except TypeError:
                        raise CertError("The private key is protected by a password: type it in the Password box")
                    except ValueError:
                        raise CertError("The private key can't be read" + (" (wrong password?)" if pw else ""))
            continue
        try:                                            # PKCS#12 (.pfx / .p12)
            k, c, extra = pkcs12.load_key_and_certificates(raw, pw)
            if k is not None:
                if key is not None:
                    raise CertError("More than one private key was given")
                key = k
            certs += ([c] if c else []) + list(extra or [])
            continue
        except ValueError as exc:
            pkcs_err = str(exc)
        try:
            certs.append(x509.load_der_x509_certificate(raw))
            continue
        except ValueError:
            pass
        try:
            if key is None:
                key = serialization.load_der_private_key(raw, pw)
                continue
        except (ValueError, TypeError):
            pass
        if "password" in pkcs_err.lower() or "mac" in pkcs_err.lower():
            raise CertError("The .pfx/.p12 file needs its password (or the password is wrong)")
        raise CertError("One of the files is not a certificate or key LENTA can read (PEM, CRT, KEY, PFX or P12)")
    if key is None:
        raise CertError("No private key was found. Add the key file (often privkey.pem or *.key)")
    if not certs:
        raise CertError("No certificate was found. Add the certificate file (often fullchain.pem, cert.pem or *.crt)")
    pub = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    leaf = next((c for c in certs if c.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo) == pub), None)
    if leaf is None:
        raise CertError("The private key doesn't belong to the certificate")
    if not isinstance(key, (rsa.RSAPrivateKey, ec.EllipticCurvePrivateKey)):
        raise CertError("Only RSA and EC keys are supported")
    chain, seen = [leaf], {leaf.fingerprint(hashes.SHA256())}
    cur = leaf                                          # leaf first, then each issuer that was given
    while True:
        nxt = next((c for c in certs if c.subject == cur.issuer and c.fingerprint(hashes.SHA256()) not in seen
                    and cur.issuer != cur.subject), None)
        if nxt is None:
            break
        chain.append(nxt)
        seen.add(nxt.fingerprint(hashes.SHA256()))
        cur = nxt
    return chain, key


def _check_loadable(crt: bytes, key: bytes) -> None:
    with tempfile.TemporaryDirectory() as d:
        c, k = Path(d, "c.pem"), Path(d, "k.pem")
        c.write_bytes(crt)
        k.write_bytes(key)
        os.chmod(k, 0o600)
        try:
            ssl.create_default_context(ssl.Purpose.CLIENT_AUTH).load_cert_chain(c, k)
        except ssl.SSLError as exc:
            raise CertError(f"The certificate can't be used for HTTPS: {exc.reason or exc}")


def save_own(blobs: list[bytes], password: str | None) -> dict:
    """Check uploaded certificate/key files and keep them for HTTPS."""
    chain, key = _load_any(blobs, password)
    leaf = chain[0]
    if leaf.not_valid_after_utc < _now():
        raise CertError(f"This certificate expired on {leaf.not_valid_after_utc:%d %b %Y}")
    crt = b"".join(c.public_bytes(serialization.Encoding.PEM) for c in chain)
    kb = _pem_key(key)
    _check_loadable(crt, kb)
    _write(OWN_KEY, kb, secret=True)
    _write(OWN_CRT, crt)
    return describe(OWN_CRT)


def check_paths(crt_path: str, key_path: str) -> None:
    """The administrator's certificate files on the server: readable by LENTA and usable for HTTPS."""
    for label, p in (("certificate", crt_path), ("key", key_path)):
        if not p:
            raise CertError(f"Type the full path of the {label} file")
        path = Path(p)
        if not path.is_absolute():
            raise CertError(f"The {label} path must start with /")
        if not path.exists():
            raise CertError(f"{p} doesn't exist (or LENTA's service account can't see it)")
        if not os.access(path, os.R_OK):
            raise CertError(f"LENTA's service account can't read {p}. Give it read access (see the guide)")
    chain, key = _load_any([Path(crt_path).read_bytes(), Path(key_path).read_bytes()], None)
    _check_loadable(b"".join(c.public_bytes(serialization.Encoding.PEM) for c in chain), _pem_key(key))


# ---- what HTTPS uses now -------------------------------------------------------------------------

def mode() -> str:
    m = get_setting("net_cert_mode") or "auto"
    return m if m in ("auto", "upload", "path") else "auto"


def active_files() -> tuple[Path, Path]:
    """(certificate, key) for the HTTPS listener; makes LENTA's own certificate when needed."""
    m = mode()
    if m == "upload":
        if not (OWN_CRT.exists() and OWN_KEY.exists()):
            raise CertError("No certificate has been uploaded yet")
        return OWN_CRT, OWN_KEY
    if m == "path":
        crt, key = get_setting("net_cert_path_crt"), get_setting("net_cert_path_key")
        check_paths(crt, key)
        return Path(crt), Path(key)
    make_auto()
    return AUTO_CRT, AUTO_KEY


def stamp(paths: tuple[Path, Path]) -> str:
    """Changes when the certificate files change (to notice renewals)."""
    parts = []
    for p in paths:
        try:
            st = p.stat()
            parts.append(f"{p}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            parts.append(f"{p}:-")
    return "|".join(parts)


def _names_in(cert: x509.Certificate) -> tuple[list[str], list[str]]:
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        return san.get_values_for_type(x509.DNSName), [str(i) for i in san.get_values_for_type(x509.IPAddress)]
    except x509.ExtensionNotFound:
        cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        return ([cn[0].value] if cn else []), []


def _cn(name: x509.Name) -> str:
    for oid in (NameOID.COMMON_NAME, NameOID.ORGANIZATION_NAME):
        a = name.get_attributes_for_oid(oid)
        if a:
            return str(a[0].value)
    return name.rfc4514_string()


def describe(path: Path) -> dict:
    """What the administrator sees about a certificate."""
    data = path.read_bytes()
    blocks = re.findall(rb"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", data, re.S)
    leaf = x509.load_pem_x509_certificate(blocks[0]) if blocks else x509.load_der_x509_certificate(data)
    dns, ips = _names_in(leaf)
    days = (leaf.not_valid_after_utc - _now()).days
    return {
        "subject": _cn(leaf.subject),
        "issuer": _cn(leaf.issuer),
        "self_made": _cn(leaf.issuer).startswith("LENTA local CA") or leaf.issuer == leaf.subject,
        "names": dns + ips,
        "valid_until": leaf.not_valid_after_utc.strftime("%Y-%m-%d"),
        "days_left": days,
        "expired": days < 0,
        "fingerprint": ":".join(f"{b:02X}" for b in leaf.fingerprint(hashes.SHA256())),
    }


def info() -> dict:
    """The certificate HTTPS uses now, or why there is none."""
    try:
        crt, _key = active_files()
        out = describe(crt)
        out["ok"] = True
    except CertError as exc:
        out = {"ok": False, "error": str(exc)}
    except Exception as exc:                            # unreadable files and the like
        out = {"ok": False, "error": f"The certificate can't be read: {exc}"}
    out["mode"] = mode()
    out["own_uploaded"] = OWN_CRT.exists() and OWN_KEY.exists()
    if out["own_uploaded"]:
        try:
            out["own"] = describe(OWN_CRT)
        except Exception:
            out["own"] = None
    return out


def ca_fingerprint() -> str:
    return ":".join(f"{b:02X}" for b in hashlib.sha256(
        x509.load_pem_x509_certificate(ca_pem()).public_bytes(serialization.Encoding.DER)).digest())
