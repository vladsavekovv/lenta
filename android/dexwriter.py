"""A small DEX (Dalvik executable, version 035) writer.

Classes are described in Python with methods written in Dalvik assembly-like tuples. Enough of the
format for a WebView app: strings, types, protos, fields, methods, classes, code, method annotations.
Reference: https://source.android.com/docs/core/runtime/dex-format
"""
import hashlib
import struct
import zlib

NO_INDEX = 0xFFFFFFFF
ACC_PUBLIC, ACC_FINAL, ACC_VOLATILE, ACC_CONSTRUCTOR = 0x1, 0x10, 0x40, 0x10000


def uleb(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def mutf8(s: str) -> bytes:
    out = bytearray()
    for ch in s:
        c = ord(ch)
        if c == 0:
            out += b"\xc0\x80"
        elif c < 0x80:
            out.append(c)
        elif c < 0x800:
            out += bytes([0xC0 | (c >> 6), 0x80 | (c & 0x3F)])
        elif c < 0x10000:
            out += bytes([0xE0 | (c >> 12), 0x80 | ((c >> 6) & 0x3F), 0x80 | (c & 0x3F)])
        else:
            raise ValueError("characters outside the BMP are not supported")
    return bytes(out)


def shorty(desc: str) -> str:
    return "L" if desc[0] in "L[" else desc


def parse_proto(sig: str) -> tuple[str, list[str]]:
    """'(Ljava/lang/String;I)V' -> ('V', ['Ljava/lang/String;', 'I'])"""
    assert sig[0] == "("
    i, params = 1, []
    while sig[i] != ")":
        j = i
        while sig[j] == "[":
            j += 1
        if sig[j] == "L":
            j = sig.index(";", j)
        params.append(sig[i:j + 1])
        i = j + 1
    return sig[i + 1:], params


def param_words(params: list[str]) -> int:
    return sum(2 if p in ("J", "D") else 1 for p in params)


class Method:
    def __init__(self, name, sig, code, regs, flags=ACC_PUBLIC, annotations=()):
        self.name, self.sig, self.code, self.regs, self.flags = name, sig, code, regs, flags
        self.annotations = list(annotations)       # type descriptors of runtime-visible marker annotations


class Field:
    def __init__(self, name, type_, flags=ACC_PUBLIC):
        self.name, self.type, self.flags = name, type_, flags


class ClassDef:
    def __init__(self, name, super_="Ljava/lang/Object;", interfaces=(), fields=(), methods=(), flags=ACC_PUBLIC):
        self.name, self.super, self.interfaces = name, super_, list(interfaces)
        self.fields, self.methods, self.flags = list(fields), list(methods), flags


# ---- instruction assembly --------------------------------------------------------------------
# Instructions are tuples: ("op", args...). Labels are ("label", "name"). Branch targets are label names.
# Method/field references are strings "Lcls;->name(sig)ret" / "Lcls;->name:Type"; types are descriptors.

OPS = {
    "nop": (0x00, "10x"), "move-object": (0x07, "12x"), "move-result": (0x0A, "11x"), "move-result-object": (0x0C, "11x"),
    "return-void": (0x0E, "10x"), "return": (0x0F, "11x"), "return-object": (0x11, "11x"),
    "const/4": (0x12, "11n"), "const/16": (0x13, "21s"), "const": (0x14, "31i"), "const-string": (0x1A, "21c-s"),
    "check-cast": (0x1F, "21c-t"), "new-instance": (0x22, "21c-t"), "goto": (0x28, "10t"),
    "if-eq": (0x32, "22t"), "if-ne": (0x33, "22t"), "if-lt": (0x34, "22t"), "if-eqz": (0x38, "21t"), "if-nez": (0x39, "21t"),
    "sget": (0x60, "21c-f"),
    "iget": (0x52, "22c"), "iget-object": (0x54, "22c"), "iget-boolean": (0x55, "22c"),
    "iput": (0x59, "22c"), "iput-object": (0x5B, "22c"), "iput-boolean": (0x5C, "22c"),
    "invoke-virtual": (0x6E, "35c"), "invoke-super": (0x6F, "35c"), "invoke-direct": (0x70, "35c"),
    "invoke-static": (0x71, "35c"), "invoke-interface": (0x72, "35c"),
}
SIZES = {"10x": 1, "12x": 1, "11x": 1, "11n": 1, "10t": 1, "21s": 2, "21c-s": 2, "21c-t": 2, "21t": 2, "22t": 2,
         "22c": 2, "31i": 3, "35c": 3, "21c-f": 2}


def reg(r: str) -> int:
    assert r[0] == "v", r
    return int(r[1:])


class Dex:
    def __init__(self, classes: list[ClassDef]):
        self.classes = classes
        self.strings, self.types, self.protos, self.fields, self.methods = set(), set(), set(), set(), set()
        self._collect()

    # -- gather every string/type/proto/field/method used
    def _type(self, t):
        self.types.add(t)
        self.strings.add(t)

    def _proto(self, sig):
        ret, params = parse_proto(sig)
        self._type(ret)
        for p in params:
            self._type(p)
        self.strings.add(shorty(ret) + "".join(shorty(p) for p in params))
        self.protos.add(sig)

    def _method(self, ref):
        cls, rest = ref.split("->")
        name, sig = rest[:rest.index("(")], rest[rest.index("("):]
        self._type(cls)
        self.strings.add(name)
        self._proto(sig)
        self.methods.add((cls, name, sig))

    def _field(self, ref):
        cls, rest = ref.split("->")
        name, typ = rest.split(":")
        self._type(cls)
        self._type(typ)
        self.strings.add(name)
        self.fields.add((cls, name, typ))

    def _collect(self):
        for c in self.classes:
            self._type(c.name)
            self._type(c.super)
            for i in c.interfaces:
                self._type(i)
            for f in c.fields:
                self._field(f"{c.name}->{f.name}:{f.type}")
            for m in c.methods:
                self._method(f"{c.name}->{m.name}{m.sig}")
                for a in m.annotations:
                    self._type(a)
                for ins in m.code:
                    op = ins[0]
                    if op == "label":
                        continue
                    fmt = OPS[op][1]
                    if fmt == "21c-s":
                        self.strings.add(ins[2])
                    elif fmt == "21c-t":
                        self._type(ins[2])
                    elif fmt == "22c":
                        self._field(ins[3])
                    elif fmt == "21c-f":
                        self._field(ins[2])
                    elif fmt == "35c":
                        self._method(ins[2])

    # -- sorted index tables
    def _index(self):
        # string ids sorted by UTF-16 code units (all our strings are within the BMP)
        self.string_list = sorted(self.strings, key=lambda s: [ord(c) for c in s])
        self.sidx = {s: i for i, s in enumerate(self.string_list)}
        self.type_list = sorted(self.types, key=lambda t: self.sidx[t])
        self.tidx = {t: i for i, t in enumerate(self.type_list)}

        def proto_key(sig):
            ret, params = parse_proto(sig)
            return (self.tidx[ret], [self.tidx[p] for p in params])
        self.proto_list = sorted(self.protos, key=proto_key)
        self.pidx = {p: i for i, p in enumerate(self.proto_list)}
        self.field_list = sorted(self.fields, key=lambda f: (self.tidx[f[0]], self.sidx[f[1]], self.tidx[f[2]]))
        self.fidx = {f: i for i, f in enumerate(self.field_list)}
        self.method_list = sorted(self.methods, key=lambda m: (self.tidx[m[0]], self.sidx[m[1]], self.pidx[m[2]]))
        self.midx = {m: i for i, m in enumerate(self.method_list)}

    def _mref(self, ref):
        cls, rest = ref.split("->")
        return self.midx[(cls, rest[:rest.index("(")], rest[rest.index("("):])]

    def _fref(self, ref):
        cls, rest = ref.split("->")
        name, typ = rest.split(":")
        return self.fidx[(cls, name, typ)]

    # -- code assembly
    def assemble(self, m: Method) -> tuple[bytes, int, int]:
        labels, pc = {}, 0
        for ins in m.code:
            if ins[0] == "label":
                labels[ins[1]] = pc
            else:
                pc += SIZES[OPS[ins[0]][1]]
        units, outs, pc = [], 0, 0
        for ins in m.code:
            op = ins[0]
            if op == "label":
                continue
            code, fmt = OPS[op]
            a = ins[1:]
            if fmt == "10x":
                u = [code]
            elif fmt == "11x":
                u = [code | reg(a[0]) << 8]
            elif fmt == "12x":
                u = [code | reg(a[0]) << 8 | reg(a[1]) << 12]
            elif fmt == "11n":
                lit = a[1]
                assert -8 <= lit <= 7 and reg(a[0]) < 16
                u = [code | reg(a[0]) << 8 | (lit & 0xF) << 12]
            elif fmt == "21s":
                assert -32768 <= a[1] <= 32767
                u = [code | reg(a[0]) << 8, a[1] & 0xFFFF]
            elif fmt == "31i":
                v = a[1] & 0xFFFFFFFF
                u = [code | reg(a[0]) << 8, v & 0xFFFF, v >> 16]
            elif fmt == "21c-s":
                u = [code | reg(a[0]) << 8, self.sidx[a[1]]]
            elif fmt == "21c-t":
                u = [code | reg(a[0]) << 8, self.tidx[a[1]]]
            elif fmt == "21c-f":
                u = [code | reg(a[0]) << 8, self._fref(a[1])]
            elif fmt == "10t":
                off = labels[a[0]] - pc
                assert -128 <= off <= 127 and off != 0
                u = [code | (off & 0xFF) << 8]
            elif fmt == "21t":
                off = labels[a[1]] - pc
                u = [code | reg(a[0]) << 8, off & 0xFFFF]
            elif fmt == "22t":
                off = labels[a[2]] - pc
                assert reg(a[0]) < 16 and reg(a[1]) < 16
                u = [code | reg(a[0]) << 8 | reg(a[1]) << 12, off & 0xFFFF]
            elif fmt == "22c":
                assert reg(a[0]) < 16 and reg(a[1]) < 16
                u = [code | reg(a[0]) << 8 | reg(a[1]) << 12, self._fref(a[2])]
            elif fmt == "35c":
                regs = [reg(r) for r in a[0]]
                assert len(regs) <= 5 and all(r < 16 for r in regs), ins
                # the argument count must match the method's parameters (+ this unless static)
                ret, params = parse_proto(a[1][a[1].index("("):])
                need = param_words(params) + (0 if op == "invoke-static" else 1)
                assert len(regs) == need, f"{ins}: {len(regs)} registers for {need} argument words"
                outs = max(outs, len(regs))
                r = regs + [0] * (5 - len(regs))
                u = [code | r[4] << 8 | len(regs) << 12, self._mref(a[1]), r[0] | r[1] << 4 | r[2] << 8 | r[3] << 12]
            else:
                raise ValueError(fmt)
            units += u
            pc += len(u)
        _, params = parse_proto(m.sig)
        ins_size = param_words(params) + (0 if m.flags & 0x8 else 1)
        assert m.regs >= ins_size, f"{m.name}: registers {m.regs} < ins {ins_size}"
        assert m.code and m.code[-1][0] in ("return-void", "return", "return-object", "goto"), f"{m.name} falls off the end"
        return struct.pack(f"<{len(units)}H", *units), ins_size, outs

    # -- file layout
    def build(self) -> bytes:
        self._index()
        n_str, n_typ, n_pro, n_fld, n_met, n_cls = (len(self.string_list), len(self.type_list), len(self.proto_list),
                                                     len(self.field_list), len(self.method_list), len(self.classes))
        off = 0x70
        string_ids_off = off; off += 4 * n_str
        type_ids_off = off; off += 4 * n_typ
        proto_ids_off = off; off += 12 * n_pro
        field_ids_off = off; off += 8 * n_fld
        method_ids_off = off; off += 8 * n_met
        class_defs_off = off; off += 32 * n_cls
        data_off = off
        data = bytearray()
        map_items = []           # (type, count, offset)

        def here():
            return data_off + len(data)

        def align4():
            while (data_off + len(data)) % 4:
                data.append(0)

        # code items
        align4()
        code_offs, first = {}, here()
        count = 0
        for c in self.classes:
            for m in c.methods:
                align4()
                insns, ins_size, outs = self.assemble(m)
                code_offs[(c.name, m.name, m.sig)] = here()
                data.extend(struct.pack("<HHHHII", m.regs, ins_size, outs, 0, 0, len(insns) // 2))
                data.extend(insns)
                count += 1
        map_items.append((0x2001, count, first))

        # type lists (proto parameters, class interfaces), deduplicated
        align4()
        lists = set()
        for p in self.proto_list:
            _, params = parse_proto(p)
            if params:
                lists.add(tuple(params))
        for c in self.classes:
            if c.interfaces:
                lists.add(tuple(c.interfaces))
        tl_offs, first = {}, here()
        for tl in sorted(lists, key=lambda t: [self.tidx[x] for x in t]):
            align4()
            tl_offs[tl] = here()
            data.extend(struct.pack("<I", len(tl)) + struct.pack(f"<{len(tl)}H", *[self.tidx[x] for x in tl]))
        if lists:
            map_items.append((0x1001, len(lists), first))

        # string data
        str_offs, first = [], here()
        for s in self.string_list:
            str_offs.append(here())
            data.extend(uleb(len(s)) + mutf8(s) + b"\x00")
        map_items.append((0x2002, n_str, first))

        # annotations: one runtime-visible marker annotation per annotated method
        ann_types = sorted({a for c in self.classes for m in c.methods for a in m.annotations}, key=lambda t: self.tidx[t])
        ann_item_off, first = {}, here()
        for t in ann_types:
            ann_item_off[t] = here()
            data.extend(b"\x01" + uleb(self.tidx[t]) + uleb(0))      # visibility RUNTIME, type, no elements
        if ann_types:
            map_items.append((0x2004, len(ann_types), first))
        align4()
        set_off, first = {}, here()
        for key in sorted({tuple(m.annotations) for c in self.classes for m in c.methods if m.annotations}):
            align4()
            set_off[key] = here()
            entries = sorted(ann_item_off[t] for t in key)       # sorted by type index (same order here)
            data.extend(struct.pack("<I", len(key)) + struct.pack(f"<{len(key)}I", *entries))
        if set_off:
            map_items.append((0x1003, len(set_off), first))
        align4()
        dir_off, first = {}, here()
        for c in self.classes:
            annotated = sorted(((self.midx[(c.name, m.name, m.sig)], set_off[tuple(m.annotations)])
                                for m in c.methods if m.annotations))
            if not annotated:
                continue
            align4()
            dir_off[c.name] = here()
            data.extend(struct.pack("<IIII", 0, 0, len(annotated), 0))
            for midx, soff in annotated:
                data.extend(struct.pack("<II", midx, soff))
        if dir_off:
            map_items.append((0x2006, len(dir_off), first))

        # class data
        cd_off, first = {}, here()
        for c in self.classes:
            cd_off[c.name] = here()
            inst = sorted(c.fields, key=lambda f: self.fidx[(c.name, f.name, f.type)])
            direct = [m for m in c.methods if m.name == "<init>" or m.flags & 0x8]
            virtual = [m for m in c.methods if m not in direct]
            key = lambda m: self.midx[(c.name, m.name, m.sig)]
            direct.sort(key=key)
            virtual.sort(key=key)
            out = bytearray(uleb(0) + uleb(len(inst)) + uleb(len(direct)) + uleb(len(virtual)))
            prev = 0
            for f in inst:
                i = self.fidx[(c.name, f.name, f.type)]
                out += uleb(i - prev) + uleb(f.flags)
                prev = i
            for group in (direct, virtual):
                prev = 0
                for m in group:
                    i = key(m)
                    flags = m.flags | (ACC_CONSTRUCTOR if m.name == "<init>" else 0)
                    out += uleb(i - prev) + uleb(flags) + uleb(code_offs[(c.name, m.name, m.sig)])
                    prev = i
            data.extend(out)
        map_items.append((0x2000, n_cls, first))

        # map list
        align4()
        map_off = here()
        items = [(0x0000, 1, 0), (0x0001, n_str, string_ids_off), (0x0002, n_typ, type_ids_off),
                 (0x0003, n_pro, proto_ids_off)]
        if n_fld:
            items.append((0x0004, n_fld, field_ids_off))
        items += [(0x0005, n_met, method_ids_off), (0x0006, n_cls, class_defs_off)] + map_items + [(0x1000, 1, map_off)]
        items.sort(key=lambda x: x[2])
        data.extend(struct.pack("<I", len(items)))
        for t, n, o in items:
            data.extend(struct.pack("<HHII", t, 0, n, o))
        align4()

        # index sections
        idx = bytearray()
        idx += b"".join(struct.pack("<I", o) for o in str_offs)
        idx += b"".join(struct.pack("<I", self.sidx[t]) for t in self.type_list)
        for p in self.proto_list:
            ret, params = parse_proto(p)
            sh = shorty(ret) + "".join(shorty(x) for x in params)
            idx += struct.pack("<III", self.sidx[sh], self.tidx[ret], tl_offs[tuple(params)] if params else 0)
        for cls, name, typ in self.field_list:
            idx += struct.pack("<HHI", self.tidx[cls], self.tidx[typ], self.sidx[name])
        for cls, name, sig in self.method_list:
            idx += struct.pack("<HHI", self.tidx[cls], self.pidx[sig], self.sidx[name])
        for c in self.classes:
            idx += struct.pack("<IIIIIIII", self.tidx[c.name], c.flags, self.tidx[c.super],
                               tl_offs[tuple(c.interfaces)] if c.interfaces else 0, NO_INDEX,
                               dir_off.get(c.name, 0), cd_off[c.name], 0)
        assert 0x70 + len(idx) == data_off

        file_size = data_off + len(data)
        header = bytearray(b"dex\n035\x00" + b"\x00" * 24)
        header += struct.pack("<IIIIII", file_size, 0x70, 0x12345678, 0, 0, map_off)
        header += struct.pack("<IIIIIIIIIIIIII", n_str, string_ids_off, n_typ, type_ids_off, n_pro, proto_ids_off,
                              n_fld, field_ids_off if n_fld else 0, n_met, method_ids_off, n_cls, class_defs_off,
                              len(data), data_off)
        assert len(header) == 0x70
        blob = bytearray(header + idx + data)
        blob[12:32] = hashlib.sha1(blob[32:]).digest()
        blob[8:12] = struct.pack("<I", zlib.adler32(bytes(blob[12:])) & 0xFFFFFFFF)
        return bytes(blob)
