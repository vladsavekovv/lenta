"""The Android app opens a "play this title" link from another app on the phone.

Before app 1.2.0 the app had only a launcher entry, so another app — a voice assistant asked to "play
Dune" — could only send the phone's browser to the film. Now the app takes

    action android.intent.action.VIEW,  data lenta://play/<item id>[?t=<seconds>]

and opens <the saved server>/#/play/<id>[?t=<s>] in its own window, which the web client already
routes to the player and the app already turns full screen.

The code that does it is Dalvik instructions written by hand, and a fault in it on the phone looks like
nothing at all happening. So rather than read it, this runs it: `MainActivity.link` is interpreted
instruction by instruction against stand-ins for Intent, Uri, SharedPreferences and WebView that behave
as Android's do, for every kind of link worth worrying about. Then it checks the manifest really
routes that link to the activity, and that the web client really understands the address it is given.

  1. link() opens what it should, and nothing it should not
  2. onCreate and onNewIntent both go through it (a cold start, and the app already open)
  3. the manifest's filter says the same thing link() does, and a web page cannot use it
  4. the web client routes the address link() builds
"""
import os, re, sys, tempfile, zipfile
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path("/home/claude/lenta")
ANDROID = ROOT / "android"
APK = ROOT / "web" / "download" / "lenta.apk"
os.environ.setdefault("LENTA_DATA_DIR", tempfile.mkdtemp())
sys.path.insert(0, str(ANDROID))

fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


import app_code                                                             # noqa: E402

app = app_code.classes()
MA = app_code.MA
SCHEME = getattr(app_code, "LINK_SCHEME", "lenta")
main = next(c for c in app if c.name == MA)
methods = {m.name: m for m in main.methods}


# ---- stand-ins that behave as Android's do, for the calls link() makes ----------------------------

class Uri:
    """android.net.Uri.parse for a hierarchical URI: the parts come back decoded, as Android gives them"""
    def __init__(self, s):
        self.s = s
        u = urlsplit(s)
        self.scheme = s.split(":", 1)[0] if ":" in s else None
        # as written: urlsplit's .hostname lower-cases it, Android's getHost does not
        self.host = u.netloc.rsplit("@", 1)[-1].split(":")[0] or None if u.netloc else None
        self.path = unquote(u.path) if u.path else None
        self.query = parse_qs(u.query, keep_blank_values=True)

    def getScheme(self): return self.scheme
    def getHost(self): return self.host
    def getPath(self): return self.path
    def getQueryParameter(self, k): return self.query[k][0] if k in self.query else None


class Intent:
    def __init__(self, data): self.data = None if data is None else Uri(data)
    def getData(self): return self.data


class Prefs:
    def __init__(self, server): self.server = server
    def getString(self, k, d): return self.server if k == "server" else d


class Web:
    def __init__(self): self.loaded = []
    def loadUrl(self, u): self.loaded.append(u)


class Activity:
    def __init__(self, server):
        self.fields = {app_code.F_WEB: Web(), app_code.F_HOST: None}
        self.prefs = Prefs(server)
        self.hidden_custom = 0


def java_matches(s, rx):
    return re.fullmatch(rx, s) is not None          # String.matches is a whole-string match


def call(owner, name, args):
    """the effect of one framework call link() makes"""
    me = args[0] if args else None
    if name == "getData": return me.getData()
    if name in ("getScheme", "getHost", "getPath"): return getattr(me, name)()
    if name == "getQueryParameter": return me.getQueryParameter(args[1])
    if name == "equalsIgnoreCase": return int(args[1] is not None and me.lower() == args[1].lower())
    if name == "matches": return int(java_matches(me, args[1]))
    if name == "concat": return me + args[1]
    if name == "parse": return Uri(args[0])
    if name == "getSharedPreferences": return me.prefs
    if name == "getString": return me.getString(args[1], args[2])
    if name == "loadUrl": return me.loadUrl(args[1])
    if name == "hideCustom": me.hidden_custom += 1; return None
    raise AssertionError(f"link() calls {owner}->{name}, which this test does not know: teach it")


def run(method, this, *params):
    """interpret one of the app's methods; parameters go in the last registers, as on Dalvik"""
    code = method.code
    labels = {ins[1]: i for i, ins in enumerate(code) if ins[0] == "label"}
    reg = {}
    first = method.regs - 1 - len(params)
    reg[f"v{first}"] = this
    for i, p in enumerate(params):
        reg[f"v{first + 1 + i}"] = p
    result, pc, steps = None, 0, 0
    while True:
        steps += 1
        assert steps < 10000, "link() does not finish"
        ins = code[pc]; op = ins[0]; pc += 1
        if op == "label":
            continue
        if op in ("const/4", "const/16", "const"):
            reg[ins[1]] = ins[2]
        elif op == "const-string":
            reg[ins[1]] = ins[2]
        elif op == "if-eqz":
            if reg[ins[1]] in (None, 0, False): pc = labels[ins[2]]
        elif op == "if-nez":
            if reg[ins[1]] not in (None, 0, False): pc = labels[ins[2]]
        elif op.startswith("invoke-"):
            ref = ins[2]
            owner, rest = ref.split("->", 1)
            result = call(owner, rest[:rest.index("(")], [reg[r] for r in ins[1]])
        elif op in ("move-result", "move-result-object"):
            reg[ins[1]] = result
        elif op == "iget-object":
            reg[ins[1]] = reg[ins[2]].fields[ins[3]]
        elif op == "iput-object":
            reg[ins[2]].fields[ins[3]] = reg[ins[1]]
        elif op == "return":
            return reg[ins[1]]
        elif op == "return-void":
            return None
        else:
            raise AssertionError(f"link() uses {op}, which this test does not interpret: teach it")


SERVER = "http://192.168.1.5:8484"
print("1. link() opens what it should, and nothing it should not")
if "link" not in methods:
    check("MainActivity has a link() that handles a \"play this\" link", False)
else:
    CASES = [
        # (link, saved server, what the WebView should load — None for "left alone")
        ("lenta://play/123", SERVER, SERVER + "/#/play/123"),
        ("lenta://play/123?t=754", SERVER, SERVER + "/#/play/123?t=754"),
        ("lenta://play/123?t=0", SERVER, SERVER + "/#/play/123?t=0"),
        ("LENTA://PLAY/123", SERVER, SERVER + "/#/play/123"),           # a scheme is case-insensitive
        ("lenta://play/live/7", SERVER, SERVER + "/#/play/live/7"),
        ("lenta://play/123", "https://plex.svkad.int:8443", "https://plex.svkad.int:8443/#/play/123"),
        # a t that is not a number of seconds is dropped, and the title opens where it was left
        ("lenta://play/123?t=abc", SERVER, SERVER + "/#/play/123"),
        ("lenta://play/123?t=-5", SERVER, SERVER + "/#/play/123"),
        ("lenta://play/123?t=", SERVER, SERVER + "/#/play/123"),
        ("lenta://play/123?t=1e9", SERVER, SERVER + "/#/play/123"),
        ("lenta://play/123?t=60%26restart%3D1", SERVER, SERVER + "/#/play/123"),
        # not a link to a title: the app does what it always did
        ("lenta://play/", SERVER, None),
        ("lenta://play/abc", SERVER, None),
        ("lenta://play/12/34", SERVER, None),
        ("lenta://play/123/../../api/users", SERVER, None),
        ("lenta://play/12%2F34", SERVER, None),
        ("lenta://play/123%3Fx", SERVER, None),
        ("lenta://item/123", SERVER, None),
        ("lenta://evil.example/play/123", SERVER, None),
        ("http://192.168.1.5:8484/#/play/123", SERVER, None),           # the link never names the server
        ("https://evil.example/#/play/123", SERVER, None),
        ("javascript://play/123", SERVER, None),
        (None, SERVER, None),                                          # the launcher: no data at all
        # no server saved yet: link() declines and the setup page opens, as on any first run
        ("lenta://play/123", None, None),
    ]
    wrong = []
    for link, server, want in CASES:
        act = Activity(server)
        handled = run(methods["link"], act, Intent(link))
        loaded = act.fields[app_code.F_WEB].loaded
        got = loaded[-1] if loaded else None
        if got != want or bool(handled) != (want is not None) or len(loaded) > 1:
            wrong.append(f"{link!r} with server {server!r}: loaded {got!r}, handled={handled}; wanted {want!r}")
        if want and act.fields[app_code.F_HOST] != Uri(server).getHost():
            wrong.append(f"{link!r}: the saved server's host was not remembered, so its pages would open "
                         f"in the browser")
    check(f"all {len(CASES)} links do the right thing{'' if not wrong else ': ' + wrong[0]}", not wrong)
    act = Activity(SERVER)
    run(methods["link"], act, Intent("lenta://play/123"))
    check("a trailer that was full screen gives way to the film", act.hidden_custom == 1)
    act = Activity(SERVER)
    run(methods["link"], act, None)
    check("and no intent at all is no link, not a crash", act.fields[app_code.F_WEB].loaded == [])

print("\n2. onCreate and onNewIntent both go through it")
create = [i for i in methods["onCreate"].code if i[0].startswith("invoke-")]
names = [i[2].split("->", 1)[1] for i in create]
li = next((k for k, n in enumerate(names) if n.startswith("link(")), None)
si = next((k for k, n in enumerate(names) if n.startswith("start(")), None)
check("a cold start from a link plays it (onCreate calls link before start)",
      li is not None and si is not None and li < si and names[li - 1].startswith("getIntent("))
branch = [i for i in methods["onCreate"].code if i[0] in ("if-nez", "if-eqz")]
check("and only falls back to the start page when link declines", any(b[0] == "if-nez" for b in branch))
ni = methods.get("onNewIntent")
calls = [i[2] for i in ni.code if i[0].startswith("invoke-")] if ni else []
check("the app already open plays it in the same window (onNewIntent → link; singleTask)",
      bool(ni) and any("->onNewIntent(" in c for c in calls) and any("->link(" in c for c in calls))
check("and keeps the new intent as the current one (setIntent)", any("->setIntent(" in c for c in calls))

print("\n3. the manifest routes the link to the app, and only from another app")
if not APK.exists():
    print("  skip  no APK to read")
else:
    try:
        from loguru import logger
        logger.remove()
    except Exception:
        pass
    try:
        from androguard.core.apk import APK as AG
        xml = AG(str(APK)).get_android_manifest_axml().get_xml().decode()
    except ImportError:
        print("  skip  androguard is not installed here (pip install androguard)")
        xml = None
    if xml:
        filters = re.findall(r"<intent-filter>(.*?)</intent-filter>", xml, re.S)
        view = [f for f in filters if '"android.intent.action.VIEW"' in f]
        check("there is a VIEW filter on the activity", len(view) == 1)
        f = view[0] if view else ""
        check(f"for {SCHEME}://play — the scheme and host link() accepts",
              f'android:scheme="{SCHEME}"' in f and 'android:host="play"' in f)
        check("in the DEFAULT category, which startActivity needs", '"android.intent.category.DEFAULT"' in f)
        # BROWSABLE would let any web page open a film on the phone by putting the link on a page.
        check("and not BROWSABLE: a web page cannot send it", "BROWSABLE" not in f)
        check("no http or https filter: the app does not claim web links", not re.search(
            r'android:scheme="https?"', xml))
        check("the activity is still singleTask (2), so a second link reaches onNewIntent",
              'android:launchMode="2"' in xml or "android:launchMode=\"singleTask\"" in xml)

print("\n4. the web client routes the address link() builds")
appjs = (ROOT / "web" / "js" / "app.js").read_text()
check("#/play/<id> opens the player", r"path.match(/^\/play\/(\d+)$/)" in appjs)
check("#/play/live/<channel> opens the channel", r"path.match(/^\/play\/live\/(\d+)$/)" in appjs)
check("and ?t= is where it starts", "start: query.get('t')" in appjs)
clientcode = [i for c in app if c.name == app_code.CL for m in c.methods if m.name == "doUpdateVisitedHistory"
              for i in m.code]
check("and the app goes full screen for it (#/play/ in the address)",
      ("const-string", "v1", "#/play/") in clientcode)

print()
if fails:
    print(f"{len(fails)} failed:")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("a \"play this\" link opens in the app")
