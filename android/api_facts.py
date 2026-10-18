#!/usr/bin/env python3
"""What the Android API really declares, for the names this app uses.

    # once, to refresh it (26 MB, and only needed for this):
    curl -sSLo /tmp/android34.jar \
      https://raw.githubusercontent.com/Sable/android-platforms/master/android-34/android.jar
    python3 android/api_facts.py /tmp/android34.jar

Writes tests/android-api.json, which tests/test_android.py reads.

Why this exists. The app is Dalvik instructions written by hand, and verify_code.py — the type checker
build_apk.py runs — deliberately trusts any name beginning with Landroid: it has no way to know whether
`WebSettings.setUseWideViewPort(boolean)` is real. So one mistyped name, or one parameter of the wrong
type, passes every check in the repository and then throws NoSuchMethodError on a television or a phone,
where there is no console and the only symptom is a screen that does nothing.

Worse is a method meant to override a framework one and given the wrong signature: nothing refuses it
and nothing warns, the method simply never runs. onDownloadStart with an int where the framework has a
long means downloads quietly do nothing at all.

So the facts are taken from a real android.jar, and only for the names the app mentions — 300 members
rather than 4,000, small enough to read. A name the app uses that is not in here is not an error in
itself; it means this file predates the code and wants regenerating, and the test says so.
"""
import json
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "tests" / "android-api.json"
sys.path.insert(0, str(HERE))

DESC = re.compile(r"descriptor: (\S+)")


def jname(t):
    """Landroid/webkit/WebView; -> android.webkit.WebView"""
    return t[1:-1].replace("/", ".")


def make_inspect(jar):
    @lru_cache(maxsize=None)
    def inspect(cls):
        """({(name, descriptor)}, [supertypes]) or None if the class is not in the jar."""
        r = subprocess.run(["javap", "-cp", jar, "-p", "-s", jname(cls)], capture_output=True, text=True)
        if r.returncode or "Error:" in r.stderr:
            return None
        members, supers = set(), []
        lines = r.stdout.splitlines()
        head = next((l for l in lines if " class " in l or " interface " in l), "")
        for pat in (r"\bextends ([\w.$, ]+?)(?: implements |\s*\{|$)", r"\bimplements ([\w.$, ]+?)(?:\s*\{|$)"):
            m = re.search(pat, head)
            if m:
                supers += [s.strip() for s in m.group(1).split(",") if s.strip()]
        name = None
        for line in lines:
            s = line.strip()
            d = DESC.search(s)
            if d:
                if name is not None:
                    members.add((name, d.group(1)))
                name = None
                continue
            mm = re.match(r".*?([\w$<>]+)\s*\(", s)          # a method declaration
            if mm:
                name = "<init>" if mm.group(1) == jname(cls).split(".")[-1] else mm.group(1)
            else:
                mm = re.match(r".*?([\w$]+);$", s)           # a field declaration
                name = mm.group(1) if mm else None
        return members, ["L" + s.replace(".", "/") + ";" for s in supers]
    return inspect


def names_used(app):
    """every member name the app mentions, as a method call, a field, or a method of its own"""
    used = set()
    for c in app:
        for m in c.methods:
            used.add(m.name)
            for insn in m.code:
                if insn[0].startswith("invoke-"):
                    rest = insn[2].split("->", 1)[1]
                    used.add(rest[:rest.index("(")])
                elif insn[0].startswith(("iget", "iput", "sget", "sput")):
                    ref = insn[-1]
                    if isinstance(ref, str) and "->" in ref:
                        used.add(ref.split("->", 1)[1].split(":", 1)[0])
    return used


def classes_touched(app, inspect):
    """every class the app names, plus everything they inherit from"""
    own = {c.name for c in app}
    seen = set()
    for c in app:
        seen.add(c.super)
        seen |= set(c.interfaces or [])
        for m in c.methods:
            for insn in m.code:
                if insn[0].startswith("invoke-"):
                    seen.add(insn[2].split("->", 1)[0])
                elif insn[0].startswith(("iget", "iput", "sget", "sput")):
                    ref = insn[-1]
                    if isinstance(ref, str) and "->" in ref:
                        seen.add(ref.split("->", 1)[0])
    out, stack = set(), list(seen - own)
    while stack:
        cur = stack.pop()
        if cur in out or cur in own:
            continue
        out.add(cur)
        got = inspect(cur)
        if got:
            stack += got[1]
    return out


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__.strip().splitlines()[0] + "\n\nUsage: api_facts.py /path/to/android.jar")
    jar = sys.argv[1]
    from app_code import VERSION, classes                                   # noqa: E402
    inspect = make_inspect(jar)
    app = classes()
    used = names_used(app)
    facts, supers = {}, {}
    for cls in sorted(classes_touched(app, inspect)):
        got = inspect(cls)
        if not got:
            continue
        members, sup = got
        # A method reads "name(params)return" and needs no separator; a field would read "SDK_INTI",
        # which is not something anything could look up again, so it gets a colon.
        kept = sorted((f"{n}{d}" if d.startswith("(") else f"{n}:{d}") for n, d in members if n in used)
        if kept:
            facts[cls] = kept
        supers[cls] = sup
    out = {
        "note": "Generated by android/api_facts.py from a real android.jar — do not edit.",
        "jar": Path(jar).name,
        "app_version": VERSION,
        "members": facts,
        "supers": supers,
    }
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    print(f"{OUT.relative_to(HERE.parent)} written — {sum(len(v) for v in facts.values())} members "
          f"of {len(facts)} classes, {len(supers)} classes in the inheritance map")


if __name__ == "__main__":
    main()
