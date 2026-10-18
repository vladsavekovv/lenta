"""The LENTA Android app: the one part of LENTA nothing was checking.

The app is a WebView onto the server, and it is built without the Android SDK — `android/app_code.py`
is Dalvik instructions written out by hand, `android/dexwriter.py` turns them into classes.dex, and
`android/build_apk.py` writes the manifest, the resources and the signature itself. That works, and the
APK it produces is reproducible to the byte. The risk is not that it is badly made; it is that a mistake
in it is invisible from here. There is no console on a phone, no stack trace to read, and the symptom of
almost every fault below is the same: a screen that does nothing.

So this checks the things that cannot be seen by looking:

  1. the instructions type-check, the way Android's own verifier would
  2. every Android method and field the app names really exists, and every override really overrides
  3. the APK builds, and the one in web/download is exactly what the sources give
  4. the manifest says what the app needs it to say
  5. the signature is the one an update must carry
  6. the JavaScript bridge the web client talks to is really exposed
  7. the two things the app calls back into the page exist in the page
  8. the setup page makes sense of what someone types into it
  9. the status and navigation bars are the same colour as the page they frame

Numbers 2, 8 and 9 each caught something real the first time they were run.
"""
import hashlib, json, os, re, shutil, subprocess, sys, tempfile, zipfile
from pathlib import Path

ROOT = Path("/home/claude/lenta")
ANDROID = ROOT / "android"
APK = ROOT / "web" / "download" / "lenta.apk"
KEY = ANDROID / "lenta-release.pem"
FACTS = ROOT / "tests" / "android-api.json"
BASE = "http://127.0.0.1:8597"
os.environ.setdefault("LENTA_DATA_DIR", tempfile.mkdtemp())
sys.path.insert(0, str(ANDROID))

fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


from app_code import PKG, VERSION, classes                                  # noqa: E402
from dexwriter import parse_proto                                           # noqa: E402
import verify_code                                                          # noqa: E402

app = classes()

print("1. the instructions type-check, the way Android's verifier would")
problems = verify_code.check(app)
check(f"every method in {len(app)} classes checks out"
      f"{'' if not problems else ': ' + problems[0][:110]}", not problems)

print("\n2. every Android name the app uses exists, and every override overrides")
# verify_code trusts anything beginning with Landroid — it has no way to know whether a method is real.
# These facts come from a real android.jar via android/api_facts.py. A name that is not in them is not
# proof of a fault; it means the facts predate the code and want regenerating, and that is said as such.
facts = json.loads(FACTS.read_text())
MEMBERS, SUPERS = facts["members"], facts["supers"]
own = {c.name for c in app}


def declared(cls, name, seen=None):
    """every descriptor the class or anything it inherits from declares under this name"""
    seen = seen or set()
    if cls in seen:
        return set()
    seen.add(cls)
    out = {m[len(name):] for m in MEMBERS.get(cls, []) if m.startswith(name + "(")}
    for s in SUPERS.get(cls, []):
        out |= declared(s, name, seen)
    return out


refs, bad, unknown = set(), [], []
for c in app:
    for m in c.methods:
        for insn in m.code:
            if insn[0].startswith("invoke-"):
                refs.add(insn[2])
            elif insn[0].startswith(("iget", "iput", "sget", "sput")):
                r = insn[-1]
                if isinstance(r, str) and "->" in r:
                    refs.add(r)
own_methods = {f"{c.name}->{m.name}{m.sig}" for c in app for m in c.methods}
own_fields = {f"{c.name}->{f.name}:{f.type}" for c in app for f in c.fields}
for ref in sorted(refs):
    cls, rest = ref.split("->", 1)
    if "(" in rest:
        name, sig = rest[:rest.index("(")], rest[rest.index("("):]
        ret, params = parse_proto(sig)
        desc = "(" + "".join(params) + ")" + ret
    else:
        name, desc = rest.split(":", 1)
    if cls in own:
        pool = own_methods if "(" in rest else own_fields
        if ref not in pool and name != "<init>":
            bad.append(f"{ref} — the app does not define it")
        continue
    if "(" in rest:
        got = declared(cls, name)
    else:
        # a field: the facts record it as "name:type", a method as "name(params)return"
        got, seen = set(), [cls]
        while seen:
            cur = seen.pop()
            got |= {m[len(name) + 1:] for m in MEMBERS.get(cur, []) if m.startswith(name + ":")}
            seen += SUPERS.get(cur, [])
    if not got:
        unknown.append(ref)
    elif desc not in got:
        bad.append(f"{ref} — Android declares {name} as {', '.join(sorted(got))}")
check(f"{len(refs)} references, all of them real"
      f"{'' if not bad else ': ' + bad[0][:140]}", not bad)
check(f"and all of them known to tests/android-api.json"
      f"{'' if not unknown else f' — {len(unknown)} are not: regenerate it (android/api_facts.py), '
                                f'starting with {unknown[0]}'}", not unknown)

# An override with the wrong signature is refused by nothing and simply never runs.
bad_over = []
for c in app:
    for m in c.methods:
        if m.name == "<init>":
            continue
        ret, params = parse_proto(m.sig)
        desc = "(" + "".join(params) + ")" + ret
        found = set()
        for s in [c.super] + list(c.interfaces or []):
            found |= declared(s, m.name)
        if found and desc not in found:
            bad_over.append(f"{c.name}->{m.name}{m.sig} overrides nothing "
                            f"(the framework has {', '.join(sorted(found))})")
check(f"every method meant to override one really does"
      f"{'' if not bad_over else ': ' + bad_over[0][:140]}", not bad_over)

print("\n3. the APK builds, and the one being served is what the sources give")
if not KEY.exists():
    print("  skip  the signing key is not here, so nothing can be built or compared")
    built = None
else:
    out = Path(tempfile.mkdtemp()) / "lenta.apk"
    r = subprocess.run([sys.executable, str(ANDROID / "build_apk.py"), "--out", str(out)],
                       capture_output=True, text=True, timeout=600, cwd=str(ANDROID))
    check(f"it builds{'' if r.returncode == 0 else ': ' + (r.stderr or r.stdout).strip()[:160]}",
          r.returncode == 0 and out.exists())
    built = out if out.exists() else None
    check("web/download/lenta.apk is there for people to download", APK.exists())
    if built and APK.exists():
        a, b = hashlib.sha256(built.read_bytes()).hexdigest(), hashlib.sha256(APK.read_bytes()).hexdigest()
        # The build is reproducible — a fixed zip date, and a signature algorithm that is not random —
        # so these must match. When they do not, the APK on the server predates the app's own code and
        # everybody who downloads it gets the old one: run android/build_apk.py and copy it over.
        check(f"and it is exactly what today's sources build ({'same' if a == b else 'STALE — rebuild it'})",
              a == b)

print("\n4. the manifest says what the app needs it to say")
src = built or (APK if APK.exists() else None)
if not src:
    print("  skip  no APK to read")
else:
    z = zipfile.ZipFile(src)
    names = z.namelist()
    for want in ("AndroidManifest.xml", "classes.dex", "resources.arsc", "assets/setup.html"):
        check(f"it contains {want}", want in names)
    # resources.arsc must be stored uncompressed from Android 11 on, or the install is refused
    info = {i.filename: i for i in z.infolist()}
    check("resources.arsc is stored uncompressed (Android 11 refuses it otherwise)",
          info["resources.arsc"].compress_type == zipfile.ZIP_STORED)
    check("and the signing key is not inside the APK",
          not [n for n in names if n.endswith(".pem")])
    try:
        from loguru import logger
        logger.remove()
    except Exception:
        pass
    try:
        from androguard.core.apk import APK as AG
        ag = AG(str(src))
        xml = ag.get_android_manifest_axml().get_xml().decode()
    except ImportError:
        print("  skip  androguard is not installed here (pip install androguard)")
        ag = xml = None
    if ag:
        parts = [int(p) for p in VERSION.split(".")]
        want_code = str(parts[0] * 10000 + parts[1] * 100 + parts[2])
        check(f"the package is {PKG}", ag.get_package() == PKG)
        check(f"versionName is the app's own VERSION ({VERSION})", ag.get_androidversion_name() == VERSION)
        # Android offers an update only when this goes up, and it is not shown anywhere, so it is
        # worked out from VERSION rather than kept as a second constant somebody has to remember.
        check(f"versionCode follows from it ({want_code}), not a number kept by hand",
              str(ag.get_androidversion_code()) == want_code)
        check("it runs on Android 8 and up (minSdk 26)", int(ag.get_min_sdk_version()) == 26)
        check("and targets a current Android (34)", int(ag.get_target_sdk_version()) == 34)
        check("it asks for the internet and nothing it does not need",
              set(ag.get_permissions()) == {"android.permission.INTERNET", "android.permission.WAKE_LOCK"})
        check("one activity, and it is the launcher", ag.get_activities() == [PKG + ".MainActivity"]
              and ag.get_main_activity() == PKG + ".MainActivity")
        check("the launcher activity is exported, as Android 12 requires",
              'android:exported="true"' in xml)
        check("plain http is allowed, because a LENTA server on the house network has no certificate",
              'android:usesCleartextTraffic="true"' in xml)
        check("nothing is backed up to the cloud", 'android:allowBackup="false"' in xml)
        check("a video can go on in a small window", 'android:supportsPictureInPicture="true"' in xml)
        cc = re.search(r'configChanges="(0x[0-9a-fA-F]+)"', xml)
        bits = int(cc.group(1), 16) if cc else 0
        # Each of these would otherwise destroy and rebuild the activity, which reloads the page and
        # loses your place in a film. density is in the list because changing Display size counts.
        wanted = {"keyboard": 0x0010, "keyboardHidden": 0x0020, "navigation": 0x0040,
                  "orientation": 0x0080, "screenLayout": 0x0100, "uiMode": 0x0200,
                  "screenSize": 0x0400, "smallestScreenSize": 0x0800, "density": 0x1000,
                  "layoutDirection": 0x2000}
        missing = [n for n, b in wanted.items() if not bits & b]
        check(f"the activity handles every configuration change itself"
              f"{'' if not missing else ': not ' + ', '.join(missing)}", not missing)

print("\n5. the signature is the one an update has to carry")
if not KEY.exists() or not src:
    print("  skip  no key or no APK")
else:
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding
    pem = KEY.read_bytes()
    cert = x509.load_pem_x509_certificate(pem[pem.index(b"-----BEGIN CERTIFICATE-----"):])
    # Android installs an update only over an app signed with the same key. If this ever changes, the
    # only way for anyone to install the new version is to remove the old one and lose their settings.
    check("the certificate is the one LENTA has always been signed with",
          hashlib.sha256(cert.public_bytes(Encoding.DER)).hexdigest().startswith("e6497fd83eb1fc80"))
    import datetime
    years = (cert.not_valid_after_utc - datetime.datetime.now(datetime.timezone.utc)).days // 365
    check(f"and it does not expire for a long time yet ({years} years)", years > 10)

print("\n6. the bridge the web client talks to is really exposed")
if not src:
    print("  skip  no APK to read")
else:
    dex = zipfile.ZipFile(src).read("classes.dex")
    import struct

    def uleb(b, o):
        r = s = 0
        while True:
            x = b[o]; o += 1
            r |= (x & 0x7f) << s; s += 7
            if not x & 0x80:
                return r, o

    map_off, = struct.unpack_from("<I", dex, 0x34)
    n, = struct.unpack_from("<I", dex, map_off)
    sect = {}
    for i in range(n):
        t, _, size, off = struct.unpack_from("<HHII", dex, map_off + 4 + i * 12)
        sect[t] = (size, off)
    _, str_off = struct.unpack_from("<II", dex, 0x38)
    _, ty_off = struct.unpack_from("<II", dex, 0x40)
    _, m_off = struct.unpack_from("<II", dex, 0x58)

    def s_of(i):
        o, = struct.unpack_from("<I", dex, str_off + i * 4)
        ln, q = uleb(dex, o)
        return dex[q:q + ln].decode()

    def t_of(i):
        si, = struct.unpack_from("<I", dex, ty_off + i * 4)
        return s_of(si)

    def m_of(i):
        cls, _proto, name = struct.unpack_from("<HHI", dex, m_off + i * 8)
        return t_of(cls), s_of(name)

    # 0x2004 annotation_item, 0x2006 annotations_directory_item
    ok_ann = 0x2004 in sect and 0x2006 in sect
    annotated = set()
    if ok_ann:
        _, off = sect[0x2004]
        vis = dex[off]
        tidx, _ = uleb(dex, off + 1)
        # Without RUNTIME visibility the WebView ignores the annotation and the whole bridge is
        # silently unreachable from the page: no setup, no full screen, no change of server.
        check(f"the @JavascriptInterface annotation is RUNTIME-visible (visibility={vis})", vis == 1)
        check(f"and it is really that annotation ({t_of(tidx)})",
              t_of(tidx) == "Landroid/webkit/JavascriptInterface;")
        _, doff = sect[0x2006]
        _ca, _fn, mn, _pn = struct.unpack_from("<IIII", dex, doff)
        for i in range(mn):
            midx, _soff = struct.unpack_from("<II", dex, doff + 16 + i * 8)
            cls, name = m_of(midx)
            if cls.endswith("Bridge;"):
                annotated.add(name)
    else:
        check("the dex carries annotations at all", False)
    want = {"setServer", "server", "changeServer", "keepScreenOn", "audioPlaying", "setColors",
            "fullscreen", "version"}
    check(f"all {len(want)} bridge methods are annotated"
          f"{'' if annotated >= want else ': missing ' + ', '.join(sorted(want - annotated))}",
          annotated >= want)

# and the other direction: everything the page calls must be there
bridge = next(c for c in app if c.name.endswith("Bridge;"))
exposed = {m.name for m in bridge.methods if m.annotations}
called = set()
for f in list((ROOT / "web" / "js").glob("*.js")) + [ANDROID / "assets" / "setup.html"]:
    text = f.read_text()
    called |= set(re.findall(r"LentaApp(?:\?)?\.(\w+)", text))
    # app.js keeps a local alias, so the calls through it count too
    if "window.LentaApp" in text:
        for alias in re.findall(r"const (\w+) = window\.LentaApp", text):
            called |= set(re.findall(rf"\b{alias}(?:\?)?\.(\w+)", text))
called -= {"call", "apply", "bind"}
check(f"every method the page calls exists on the bridge ({len(called)} called)"
      f"{'' if called <= exposed else ': ' + ', '.join(sorted(called - exposed)) + ' do not'}",
      called <= exposed)

print("\n7. and the two things the app calls back into the page exist in the page")
player = (ROOT / "web" / "js" / "player.js").read_text()
css = "\n".join(p.read_text() for p in (ROOT / "web" / "css").glob("*.css"))
code = (ANDROID / "app_code.py").read_text()
# onStop sends this when the small window is closed; without it a film goes on playing unseen.
check("the page defines window.lentaPipClosed, which onStop calls",
      "lentaPipClosed" in code and "window.lentaPipClosed" in player)
# onPictureInPictureModeChanged adds this class; without the rules the small window shows the
# whole interface shrunk instead of just the picture.
check("and the stylesheet has rules for the 'pip' class the app adds",
      "'pip'" in code and "html.pip" in css)

print("\n8. the setup page makes sense of what someone types into it")
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("  skip  playwright is not installed here")
    sync_playwright = None

STUB = """(() => { window.LentaApp = { server: () => '', setServer: () => {}, changeServer: () => {},
                                       version: () => '%s' }; })();""" % VERSION
ADDRESSES = [
    ("192.168.1.50:8484", "http://192.168.1.50:8484"),
    ("192.168.1.50", "http://192.168.1.50:8484"),
    ("  192.168.1.50:8484  ", "http://192.168.1.50:8484"),
    ("192.168.1.50:8484///", "http://192.168.1.50:8484"),
    ("HTTP://192.168.1.50:8484", "http://192.168.1.50:8484"),
    ("plex.svkad.int", "http://plex.svkad.int:8484"),
    ("plex.svkad.int:9443", "http://plex.svkad.int:9443"),
    ("https://plex.svkad.int", "https://plex.svkad.int:8443"),
    ("https://plex.svkad.int:443", "https://plex.svkad.int"),
    ("[fe80::1]:8484", "http://[fe80::1]:8484"),
    ("192.168.1.50:8484/some/path", "http://192.168.1.50:8484"),
    ("http://user:pw@192.168.1.50:8484", "http://192.168.1.50:8484"),
    # Not addresses. These used to come out as a host named after the scheme — "ftp://192.168.1.50"
    # became http://ftp:8484 — which the app then saved and could not reach, so the person was told
    # the server was unreachable instead of that the address was wrong.
    ("", ""), ("   ", ""), ("not an address", ""), ("http://", ""),
    ("ftp://192.168.1.50", ""), ("file:///etc/passwd", ""), ("javascript:alert(1)", ""),
    ("192.168.1.50:notaport", ""),
]
if sync_playwright:
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--disable-dev-shm-usage"])
        page = b.new_page()
        page.add_init_script(STUB)
        page.goto((ANDROID / "assets" / "setup.html").as_uri())
        page.wait_for_timeout(200)
        wrong = []
        for typed, want in ADDRESSES:
            got = page.evaluate("(t) => normalise(t)", typed)
            if got != want:
                wrong.append(f"{typed!r} -> {got!r}, wanted {want!r}")
        check(f"all {len(ADDRESSES)} addresses come out right"
              f"{'' if not wrong else ': ' + wrong[0]}", not wrong)

        # and each way the app can open the page shows the right screen
        for label, qs, saved, want_offline in [
                ("first run", "", "", False),
                ("change the server", "?change=1", "http://192.168.1.50:8484", False),
                ("server unreachable", "?error=1", "http://192.168.1.50:8484", True),
                ("certificate refused", "?error=1&ssl=1", "https://plex.svkad.int:8443", True)]:
            p2 = b.new_page()
            p2.add_init_script("""(() => { window.LentaApp = { server: () => %r, setServer: () => {},
                                   changeServer: () => {}, version: () => '1' }; })();""" % saved)
            p2.goto((ANDROID / "assets" / "setup.html").as_uri() + qs)
            p2.wait_for_timeout(250)
            o = p2.evaluate("""() => ({ offline: !document.querySelector('#offline').hidden,
                                        ssl: !document.querySelector('#off-ssl').hidden,
                                        err: document.querySelector('#err').textContent })""")
            check(f"{label}: the {'offline' if want_offline else 'address'} screen, with nothing to"
                  f" apologise for", o["offline"] == want_offline and not o["err"])
            if "ssl" in qs:
                check("and the certificate is explained", o["ssl"])
            p2.close()
        b.close()

print("\n9. the status and navigation bars are the colour of the page they frame")
# In the app those two bars sit directly above and below the page, so a difference shows as a band of
# the wrong colour. The Netflix theme is the case that was wrong: html.nfx gives it a different
# background on a phone from the one it has on a tablet, and the colour was read before that was
# decided, so the app was told #141414 while the page went black.
if sync_playwright:
    import subprocess as sp
    sp.run(["bash", "/tmp/claude-0/intro/uplive.sh"], check=True, timeout=180)
    BARS = """(() => { window.__colors = [];
        window.LentaApp = { setColors: (a) => window.__colors.push('#' + (a >>> 0).toString(16).slice(2)),
          keepScreenOn: () => {}, audioPlaying: () => {}, fullscreen: () => {},
          changeServer: () => {}, version: () => '1', server: () => location.origin }; })();"""
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--disable-dev-shm-usage"])
        for label, w, h in [("a phone", 390, 844), ("a tablet", 1280, 900)]:
            page = b.new_page(viewport={"width": w, "height": h})
            page.add_init_script(BARS)
            page.goto(BASE, wait_until="load")
            page.wait_for_timeout(400)
            page.click('[data-user="admin"]')
            page.fill("#lp", "secret123")
            page.click("#login button.primary")
            page.wait_for_selector(".card", timeout=45000)
            page.wait_for_timeout(1200)
            off = []
            for theme in ("projector", "netflix", "hbo", "noir", "ocean", "crimson"):
                page.evaluate("window.__colors = []")
                page.evaluate("async (t) => { const m = await import('/js/app.js'); m.applyTheme(t); }", theme)
                page.wait_for_timeout(400)
                told = (page.evaluate("(window.__colors || []).slice(-1)[0] || ''") or "").lower().lstrip("#")
                real = (page.evaluate("getComputedStyle(document.documentElement)"
                                      ".getPropertyValue('--bg').trim()") or "").lower().lstrip("#")
                if len(real) == 3:
                    real = "".join(c * 2 for c in real)
                if told != real:
                    off.append(f"{theme}: told #{told or 'nothing'}, page is #{real}")
            check(f"on {label}, every theme's bars match the page"
                  f"{'' if not off else ': ' + '; '.join(off)}", not off)
            page.close()
        b.close()

print()
if fails:
    print(f"{len(fails)} failed:")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("the Android app checks out")
