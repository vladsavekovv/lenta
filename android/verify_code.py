#!/usr/bin/env python3
"""A small type checker for app_code.py, run by build_apk.py before it writes the APK.

Android's verifier (ART) refuses a whole class when one instruction uses a register of the wrong type, and the
app then closes at start. This walks every method like the verifier does — registers hold types, branches
merge them — and checks each instruction: the object of a field or method is of the right class, arguments
match the method's parameters, results are moved from the right kind of call, nothing is used before it is set.
"""
import sys

from dexwriter import parse_proto

# Supertypes of the classes the app uses (just enough to judge "is a").
SUPER = {
    "Lapp/lenta/MainActivity;": "Landroid/app/Activity;",
    "Lapp/lenta/Client;": "Landroid/webkit/WebViewClient;",
    "Lapp/lenta/Chrome;": "Landroid/webkit/WebChromeClient;",
    "Lapp/lenta/Bridge;": "Ljava/lang/Object;",
    "Lapp/lenta/Ui;": "Ljava/lang/Object;",
    "Landroid/app/Activity;": "Landroid/view/ContextThemeWrapper;",
    "Landroid/view/ContextThemeWrapper;": "Landroid/content/ContextWrapper;",
    "Landroid/content/ContextWrapper;": "Landroid/content/Context;",
    "Landroid/webkit/WebView;": "Landroid/widget/AbsoluteLayout;",
    "Landroid/widget/AbsoluteLayout;": "Landroid/view/ViewGroup;",
    "Landroid/view/ViewGroup;": "Landroid/view/View;",
    "Landroid/widget/FrameLayout$LayoutParams;": "Landroid/view/ViewGroup$MarginLayoutParams;",
    "Landroid/view/ViewGroup$MarginLayoutParams;": "Landroid/view/ViewGroup$LayoutParams;",
}
INTERFACES = {
    "Lapp/lenta/MainActivity;": {"Landroid/webkit/DownloadListener;"},
    "Lapp/lenta/Ui;": {"Ljava/lang/Runnable;"},
    "Ljava/lang/String;": {"Ljava/lang/CharSequence;"},
}
PRIM_INT = {"I", "Z", "B", "S", "C"}


def is_a(t, want):
    if t == want or want == "Ljava/lang/Object;" and t.startswith(("L", "[")):
        return True
    seen = t
    while seen in SUPER or seen in INTERFACES:
        if want in INTERFACES.get(seen, ()):
            return True
        seen = SUPER.get(seen)
        if seen == want:
            return True
        if seen is None:
            break
    return False


def check(classes):
    errors = []
    fields = {f"{c.name}->{f.name}:{f.type}" for c in classes for f in c.fields}
    for c in classes:
        for m in c.methods:
            errors += check_method(c, m, fields)
    return errors


def check_method(c, m, fields):
    errs = []
    where = f"{c.name}->{m.name}{m.sig}"
    ret, params = parse_proto(m.sig)
    static = False
    ins = (0 if static else 1) + sum(2 if p in ("J", "D") else 1 for p in params)
    start = {}
    r = m.regs - ins
    if not static:
        start[r] = c.name if m.name != "<init>" else "uninit-this:" + c.name
        r += 1
    for p in params:
        start[r] = "int" if p in PRIM_INT else ("wide" if p in ("J", "D") else p)
        r += 2 if p in ("J", "D") else 1
    code = m.code
    labels = {ins_[1]: i for i, ins_ in enumerate(code) if ins_[0] == "label"}
    states = {0: start}
    work = [0]
    visits = {}

    def reg(x):
        return int(x[1:])

    def need(state, x, want, i, what):
        t = state.get(reg(x))
        if t is None:
            errs.append(f"{where} #{i} {what}: {x} not set")
        elif want in PRIM_INT or want == "int":
            if t != "int":
                errs.append(f"{where} #{i} {what}: {x} is {t}, needs a number/boolean")
        else:
            if t == "int" and state.get(("zero", reg(x))):
                return                                      # const 0 = null
            if t == "int" or t.startswith("uninit") or not is_a(t, want) and not t.startswith("res:"):
                if not (t.startswith("L") and want.startswith("L") and t not in SUPER and t not in INTERFACES):
                    errs.append(f"{where} #{i} {what}: {x} is {t}, needs {want}")

    while work:
        i = work.pop()
        st = dict(states[i])
        visits[i] = visits.get(i, 0) + 1
        if visits[i] > 50:
            continue
        last_ret = None
        while i < len(code):
            insn = code[i]
            op = insn[0]
            nxt = [i + 1]
            if op == "label":
                pass
            elif op in ("const/4", "const/16", "const"):
                st[reg(insn[1])] = "int"
                st[("zero", reg(insn[1]))] = insn[2] == 0
            elif op == "const-string":
                st[reg(insn[1])] = "Ljava/lang/String;"
            elif op == "new-instance":
                st[reg(insn[1])] = "uninit:" + insn[2]
            elif op == "check-cast":
                need(st, insn[1], "Ljava/lang/Object;", i, "check-cast")
                st[reg(insn[1])] = insn[2]
            elif op in ("iget", "iget-boolean", "iget-object", "iput", "iput-boolean", "iput-object"):
                val, obj, f = insn[1], insn[2], insn[3]
                if f not in fields and not f.startswith("Landroid"):
                    errs.append(f"{where} #{i}: unknown field {f}")
                cls, rest = f.split("->")
                ftype = rest.split(":", 1)[1]
                need(st, obj, cls, i, op)
                if op.startswith("iget"):
                    st[reg(val)] = "int" if ftype in PRIM_INT else ftype
                    st.pop(("zero", reg(val)), None)
                else:
                    need(st, val, ftype, i, op + " value")
            elif op == "sget":
                st[reg(insn[1])] = "int"
            elif op.startswith("invoke-"):
                args, ref = insn[1], insn[2]
                cls, rest = ref.split("->")
                name = rest[:rest.index("(")]
                r_t, ps = parse_proto(rest[rest.index("("):])
                k = 0
                if op != "invoke-static":
                    t = st.get(reg(args[0]))
                    if name == "<init>":
                        if t is None or not (t.startswith("uninit") or op == "invoke-direct"):
                            errs.append(f"{where} #{i}: <init> on {t}")
                        # the object is now initialised
                        if t and t.startswith("uninit:"):
                            for rr, tt in list(st.items()):
                                if tt == t:
                                    st[rr] = t[7:]
                        elif t and t.startswith("uninit-this:"):
                            for rr, tt in list(st.items()):
                                if tt == t:
                                    st[rr] = t[12:]
                    else:
                        need(st, args[0], cls, i, f"{name} on")
                    k = 1
                for p in ps:
                    need(st, args[k], p, i, f"{name} argument")
                    k += 1
                last_ret = None if r_t == "V" else ("int" if r_t in PRIM_INT else r_t)
            elif op in ("move-result", "move-result-object"):
                if code[i - 1][0].startswith("invoke-") and last_ret:
                    if (op == "move-result") != (last_ret == "int"):
                        errs.append(f"{where} #{i}: {op} after a call returning {last_ret}")
                    st[reg(insn[1])] = last_ret
                    st.pop(("zero", reg(insn[1])), None)
                else:
                    errs.append(f"{where} #{i}: {op} without a call returning a value before it")
            elif op == "move-object":
                st[reg(insn[1])] = st.get(reg(insn[2]))
            elif op in ("if-eqz", "if-nez"):
                if st.get(reg(insn[1])) is None:
                    errs.append(f"{where} #{i}: {insn[1]} not set")
                nxt.append(labels[insn[2]])
            elif op in ("if-eq", "if-ne", "if-lt"):
                need(st, insn[1], "I", i, op); need(st, insn[2], "I", i, op)
                nxt.append(labels[insn[3]])
            elif op == "goto":
                nxt = [labels[insn[1]]]
            elif op == "return-void":
                if ret != "V":
                    errs.append(f"{where} #{i}: return-void in a method returning {ret}")
                nxt = []
            elif op in ("return", "return-object"):
                need(st, insn[1], ret, i, op)
                nxt = []
            elif op == "nop":
                pass
            else:
                errs.append(f"{where} #{i}: unchecked op {op}")
            if op != "label" and not op.startswith("invoke-"):
                last_ret = last_ret if op in ("label",) else None
            if len(nxt) != 1 or nxt[0] != i + 1:
                for n in nxt:
                    if n >= len(code):
                        continue
                    if n not in states:
                        states[n] = dict(st); work.append(n)
                    else:
                        merged = {k2: v for k2, v in states[n].items() if st.get(k2) == v}
                        if merged != states[n]:
                            states[n] = merged; work.append(n)
                break
            i += 1
    return errs


if __name__ == "__main__":
    from app_code import classes
    problems = check(classes())
    print("\n".join(problems) or "app_code: all methods check out")
    sys.exit(1 if problems else 0)
