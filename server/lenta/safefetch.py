"""Outbound HTTP that cannot be aimed back at this machine.

LENTA fetches from the internet in a dozen places: posters from TMDB, channel logos from a GitHub
index, artwork from Fanart and AniList, book covers, an IPTV playlist. Some of those addresses are
typed by the administrator; **others arrive inside a third party's answer** — the logo index names the
logo URLs, a metadata provider names its own image URLs — and those were being fetched with no check at
all. A server that will fetch any address a stranger names is a request forwarder sitting inside the
network, and the two things that makes possible are:

  * reaching what only this machine can reach — `http://127.0.0.1:…`, another box on the LAN, a cloud
    instance's metadata service on 169.254.169.254;
  * reading local files, because `urllib.request`'s default opener handles **`file://` and `ftp://`**.
    `logos.py` used that default opener, so an index entry of `file:///etc/passwd` was a file read.

So every outbound fetch goes through here, and there are two policies, because the honest answer differs
by who chose the address:

  `allow_private=False` (the default) — for an address that came from **someone else's response**. Those
  have no business naming a private address, ever.

  `allow_private=True` — for an address the **administrator typed himself**. He may legitimately point
  LENTA at a playlist on his own NAS, so refusing private addresses outright would break a real setup.
  Loopback, link-local and the cloud metadata address are still refused: nothing a person would type by
  hand lives there, and they are the addresses worth reaching.

Two limits this does not claim to beat. **DNS rebinding**: the name is resolved and every address it
answers with is checked, but a resolver that answers differently a moment later would not be caught —
closing that means connecting to the checked address while still presenting the original host name, and
that is more machinery than this is worth. **A redirect** to a private address is refused, because
redirects are followed by hand here rather than by the library, and each hop is checked.
"""
import ipaddress
import socket
import urllib.error
import urllib.request

# Only these two schemes get a handler at all. Even if a caller forgets the guard, `file:` and `ftp:`
# cannot be fetched through this opener — the handler for them is simply not installed.
_opener = urllib.request.build_opener(urllib.request.HTTPHandler, urllib.request.HTTPSHandler)

TIMEOUT = 15
MAX_BYTES = 8_000_000
MAX_REDIRECTS = 4


class Blocked(ValueError):
    """The address is not one LENTA will fetch. The message says why, in words a person can read."""


def _addresses(host: str) -> list[ipaddress._BaseAddress]:
    """Every address the name answers with. A literal address resolves to itself."""
    try:
        return [ipaddress.ip_address(host.strip("[]"))]
    except ValueError:
        pass
    try:
        info = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as ex:
        raise Blocked(f"could not look up {host}: {ex.strerror or ex}") from ex
    return [ipaddress.ip_address(sockaddr[0]) for *_, sockaddr in info]


def _refuse(ip, allow_private: bool) -> str | None:
    """Why this address is refused, or None. The order is the order a person would explain it in."""
    if ip.is_loopback:
        return "it points back at the server itself"
    if ip.is_link_local:                    # 169.254.0.0/16 — a cloud instance's credentials live here
        return "it is a link-local address"
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return "it is not a reachable address"
    if not allow_private and ip.is_private:
        return "it is a private address, and this link came from somewhere else rather than from you"
    return None


def check(url: str, *, allow_private: bool = False) -> None:
    """Raise Blocked unless this is an address LENTA is willing to fetch."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise Blocked(f"only http and https are fetched, not {parsed.scheme or 'that'}")
    if not parsed.hostname:
        raise Blocked("there is no host in that address")
    for ip in _addresses(parsed.hostname):
        why = _refuse(ip, allow_private)
        if why:
            raise Blocked(f"{parsed.hostname} resolves to {ip}, and {why}")


def get(url: str, *, allow_private: bool = False, limit: int = MAX_BYTES, timeout: int = TIMEOUT,
        headers: dict | None = None) -> tuple[bytes, str]:
    """Fetch and return (body, content-type). Raises Blocked, or urllib's own errors unchanged.

    The body is read to `limit` and no further, so a server that answers forever cannot exhaust memory —
    that was its own fault in three places before this existed. Redirects are followed here rather than
    by urllib, so that **each hop is checked**: a public address answering with a redirect to
    127.0.0.1 is the usual way round a guard that only looks at the address it was given.
    """
    seen = url
    for _ in range(MAX_REDIRECTS + 1):
        check(seen, allow_private=allow_private)
        req = urllib.request.Request(seen, headers={"User-Agent": "LENTA", "Accept": "*/*", **(headers or {})})
        with _opener.open(req, timeout=timeout) as r:
            if r.status in (301, 302, 303, 307, 308) and r.headers.get("location"):
                seen = urllib.parse.urljoin(seen, r.headers["location"])
                continue
            return r.read(limit), (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    raise Blocked("that address redirects too many times")


# urllib.parse is used above; imported last so the module docstring stays the first thing in the file.
import urllib.parse                                                        # noqa: E402
