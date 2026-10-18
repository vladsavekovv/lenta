"""Every route, and what it demands of the caller.

LENTA had 31 test files and **not one of them checked that a viewer is refused an administrator's
endpoint**, or that a stranger is refused anything at all. The checks themselves were right — the admin
routers carry `dependencies=[Depends(auth.admin_user)]` at the router, which is the sound way to do it,
because a new endpoint in that file inherits the guard instead of needing to remember it. But nothing
would have noticed a router declared without it, and that is exactly the mistake that gets made.

So this test does not look for today's bugs. It walks **every route FastAPI has registered** and asks
what it demands, then measures that against three rules:

  1. A route is either in the short list of **deliberately public** ones below, or it requires a signed-in
     user. There is no third option, and the public list is written out here so that adding to it is a
     visible decision rather than an omission.
  2. Everything under **`/api/admin`** requires an administrator specifically, not merely a signed-in
     user. A viewer must not be able to change settings, scan a library, read the log, see a filesystem
     path or touch another account.
  3. Anything that **changes state** — POST, PUT, PATCH, DELETE — requires a signed-in user without
     exception, including the handful of public GETs.

Run it and it prints the whole route table with what each one asks for, so the shape of the server's
access control is readable in one screen rather than inferred from eleven files.
"""
import os
import sys
import tempfile

os.environ["LENTA_DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, "/home/claude/lenta/server")
from lenta import auth, db                                                 # noqa: E402
from lenta.main import app                                                 # noqa: E402

db.init_db()
fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


# Routes that answer without a signed-in user, each for a stated reason. Anything not on this list must
# require one. Adding a line here is a decision about who may reach the endpoint, so write the reason.
PUBLIC = {
    "/api/server": "the sign-in screen must know the server's name before anyone has signed in",
    "/api/setup": "creates the first account; it refuses with 409 once any account exists",
    "/api/auth/login": "signing in",
    "/api/auth/logout": "signing out must work even with a token the server no longer accepts",
    "/": "the web client's own page",
    "/api/https/lenta-ca.crt": "the local CA certificate — a public key, and fetching it is the point",
}
# Deliberately NOT on that list, and each one was public until 1.19.0:
#   /api/openapi.json and /api/docs handed any stranger on the network the complete API surface —
#     every path, parameter and schema. Not a hole on its own; it is the map an attacker would
#     otherwise have to draw. They are off unless LENTA_API_DOCS=1 is set.
#   /api/languages needed no guard and had none. Nothing asks for it before signing in.


def demands(route) -> set:
    """Every dependency callable this route ends up running, router-level ones included."""
    found = set()

    def walk(dep):
        if dep.call is not None:
            found.add(dep.call)
        for sub in dep.dependencies:
            walk(sub)

    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return found
    for sub in dependant.dependencies:
        walk(sub)
    return found


def endpoints(routes):
    """Every APIRoute in the app. FastAPI keeps an included router as one object in `app.routes`
    rather than flattening it, so walk into each one; a router's own `dependencies=[...]` are already
    merged into each of its routes' dependants, which is what makes a router-level guard inheritable."""
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from endpoints(inner.routes)
        elif hasattr(route, "dependant") and getattr(route, "path", None) is not None:
            yield route
        elif getattr(route, "path", None) is not None and getattr(route, "methods", None):
            yield route                             # a plain Starlette Route (FastAPI's own docs pages)


rows = []
for route in endpoints(app.routes):
    rows.append((route.path, sorted(getattr(route, "methods", []) or []), demands(route)))

print(f"1. the route table ({len(rows)} endpoints)")
for path, methods, needs in sorted(rows):
    who = ("admin" if auth.admin_user in needs else
           "signed in" if auth.current_user in needs else
           "PUBLIC" if path in PUBLIC else "NOBODY — unguarded")
    print(f"       {','.join(methods):22} {path:46} {who}")

check("the app actually registered its routes (or the rest of this proves nothing)", len(rows) > 40)

print("\n2. nothing is reachable without signing in unless it is on the public list")
for path, methods, needs in sorted(rows):
    if auth.current_user in needs or auth.admin_user in needs:
        continue
    check(f"{','.join(methods)} {path} is deliberately public — {PUBLIC.get(path, 'NO REASON GIVEN')}",
          path in PUBLIC)

print("\n3. every public entry is still in use, so the list cannot quietly rot")
live = {path for path, _, _ in rows}
for path in sorted(PUBLIC):
    check(f"{path} is a real route", path in live)

print("\n4. /api/admin requires an administrator, not merely a signed-in user")
admin_paths = [(p, m, n) for p, m, n in rows if p.startswith("/api/admin")]
check("there are admin routes to check", len(admin_paths) > 10)
for path, methods, needs in sorted(admin_paths):
    check(f"{','.join(methods)} {path} demands an administrator", auth.admin_user in needs)

print("\n5. and nothing changes state without a signed-in user")
WRITES = {"POST", "PUT", "PATCH", "DELETE"}
for path, methods, needs in sorted(rows):
    if not (WRITES & set(methods)):
        continue
    ok = auth.current_user in needs or auth.admin_user in needs
    if not ok and path in ("/api/setup", "/api/auth/login", "/api/auth/logout"):
        continue                    # the three that cannot require one, by definition
    check(f"{','.join(sorted(WRITES & set(methods)))} {path} requires a signed-in user", ok)

print("\n6. the guard is on the router, not repeated on each endpoint")
# Why it matters: a router-level dependency is inherited by an endpoint added later, so the next
# admin endpoint someone writes is guarded whether or not they remember to guard it.
import re                                                                   # noqa: E402
from pathlib import Path                                                    # noqa: E402
for name in ("api_admin", "api_editor", "api_livetv", "api_network", "api_streaming"):
    src = Path(f"/home/claude/lenta/server/lenta/{name}.py").read_text()
    decl = re.search(r"router = APIRouter\((.*?)\)\n", src, re.S)
    check(f"{name}: its admin router carries the guard itself",
          bool(decl) and "Depends(auth.admin_user)" in decl.group(1))

print()
if fails:
    print(f"{len(fails)} failed:")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("all good")
