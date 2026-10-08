#!/usr/bin/env python3
"""Build the LENTA Android app (lenta.apk) without the Android SDK.

    python3 build_apk.py [--key lenta-release.pem] [--out lenta.apk]

Needs Python 3.9+ and the `cryptography` package (Ubuntu: python3-cryptography). The first build creates a
signing key (lenta-release.pem) next to this script; keep it, because Android only installs an update
over the app if it is signed with the same key.

The APK is written from scratch: binary AndroidManifest.xml, resources.arsc with the launcher icon,
classes.dex (dexwriter.py + app_code.py), assets/setup.html, then an APK Signature Scheme v2 signature.
"""
import argparse
import datetime
import hashlib
import io
import os
import struct
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from dexwriter import Dex                       # noqa: E402
from app_code import PKG, VERSION, classes      # noqa: E402

VERSION_CODE = 2
MIN_SDK, TARGET_SDK = 26, 34
ANDROID_NS = "http://schemas.android.com/apk/res/android"

# framework attribute ids (android.R.attr), from Android's public.xml
ATTR = {"theme": 0x01010000, "label": 0x01010001, "icon": 0x01010002, "name": 0x01010003, "exported": 0x01010010,
        "launchMode": 0x0101001d, "screenOrientation": 0x0101001e, "configChanges": 0x0101001f,
        "drawable": 0x01010199, "minSdkVersion": 0x0101020c, "versionCode": 0x0101021b, "versionName": 0x0101021c,
        "windowSoftInputMode": 0x0101022b, "targetSdkVersion": 0x01010270, "allowBackup": 0x01010280,
        "hardwareAccelerated": 0x010102d3, "supportsRtl": 0x010103af, "usesCleartextTraffic": 0x010104ec,
        "roundIcon": 0x0101052c}
THEME_MATERIAL_NO_ACTION_BAR = 0x0103022e      # @android:style/Theme.Material.NoActionBar
# our resources (resources.arsc below)
R_COLOR_BG, R_DRAWABLE_FG, R_MIPMAP_ICON = 0x7f010000, 0x7f020000, 0x7f030000

T_REF, T_STRING, T_INT_DEC, T_INT_HEX, T_BOOL, T_COLOR_ARGB8 = 0x01, 0x03, 0x10, 0x11, 0x12, 0x1c


# ---- binary XML (AXML) -----------------------------------------------------------------------

def string_pool(strings: list[str]) -> bytes:
    """ResStringPool, UTF-16"""
    offsets, data = [], bytearray()
    for s in strings:
        offsets.append(len(data))
        enc = s.encode("utf-16-le")
        n = len(enc) // 2
        assert n < 0x8000
        data += struct.pack("<H", n) + enc + b"\x00\x00"
    while len(data) % 4:
        data.append(0)
    header_size = 28
    strings_start = header_size + 4 * len(strings)
    body = b"".join(struct.pack("<I", o) for o in offsets) + bytes(data)
    size = header_size + len(body)
    return struct.pack("<HHIIIIII", 0x0001, header_size, size, len(strings), 0, 0, strings_start, 0) + body


class Axml:
    """Builds a compiled XML file. Elements: (tag, [(name, value)], [children]); android: attributes are
    named 'android:x' and get their framework resource id; values are (type, data) or a str."""

    def __init__(self, root):
        self.root = root

    def build(self) -> bytes:
        attr_names, other = [], []
        def walk(el):
            tag, attrs, kids = el
            for n, v in attrs:
                if n.startswith("android:"):
                    if n[8:] not in attr_names:
                        attr_names.append(n[8:])
                elif n not in other:
                    other.append(n)
                if isinstance(v, str) and v not in other:
                    other.append(v)
            if tag not in other:
                other.append(tag)
            for k in kids:
                walk(k)
        walk(self.root)
        attr_names.sort(key=lambda n: ATTR[n])
        pool = attr_names + [s for s in ["android", ANDROID_NS] + other if s not in attr_names]
        seen, uniq = set(), []
        for s in pool:
            if s not in seen:
                seen.add(s)
                uniq.append(s)
        pool = uniq
        idx = {s: i for i, s in enumerate(pool)}
        ns = idx[ANDROID_NS]

        chunks = [string_pool(pool)]
        resmap = b"".join(struct.pack("<I", ATTR[n]) for n in attr_names)
        chunks.append(struct.pack("<HHI", 0x0180, 8, 8 + len(resmap)) + resmap)
        node = lambda typ, size: struct.pack("<HHIII", typ, 16, size, 1, 0xFFFFFFFF)
        chunks.append(node(0x0100, 24) + struct.pack("<II", idx["android"], ns))

        def emit(el):
            tag, attrs, kids = el
            items = []
            for n, v in attrs:
                if n.startswith("android:"):
                    a_ns, a_name, rid = ns, idx[n[8:]], ATTR[n[8:]]
                else:
                    a_ns, a_name, rid = 0xFFFFFFFF, idx[n], 0xFFFFFFFF
                if isinstance(v, str):
                    raw, typ, data = idx[v], T_STRING, idx[v]
                else:
                    raw, (typ, data) = 0xFFFFFFFF, v
                items.append((rid, a_ns, a_name, raw, typ, data & 0xFFFFFFFF))
            items.sort(key=lambda x: x[0])               # by resource id, like aapt2
            body = struct.pack("<IIHHHHHH", 0xFFFFFFFF, idx[tag], 20, 20, len(items), 0, 0, 0)
            for rid, a_ns, a_name, raw, typ, data in items:
                body += struct.pack("<IIIHBBI", a_ns, a_name, raw, 8, 0, typ, data)
            chunks.append(node(0x0102, 16 + len(body)) + body)
            for k in kids:
                emit(k)
            chunks.append(node(0x0103, 24) + struct.pack("<II", 0xFFFFFFFF, idx[tag]))
        emit(self.root)
        chunks.append(node(0x0101, 24) + struct.pack("<II", idx["android"], ns))
        body = b"".join(chunks)
        return struct.pack("<HHI", 0x0003, 8, 8 + len(body)) + body


def manifest() -> bytes:
    config_changes = 0x0010 | 0x0020 | 0x0040 | 0x0080 | 0x0100 | 0x0200 | 0x0400 | 0x0800 | 0x2000
    root = ("manifest", [("android:versionCode", (T_INT_DEC, VERSION_CODE)), ("android:versionName", VERSION),
                         ("package", PKG)], [
        ("uses-sdk", [("android:minSdkVersion", (T_INT_DEC, MIN_SDK)), ("android:targetSdkVersion", (T_INT_DEC, TARGET_SDK))], []),
        ("uses-permission", [("android:name", "android.permission.INTERNET")], []),
        ("application", [("android:label", "LENTA"), ("android:icon", (T_REF, R_MIPMAP_ICON)),
                         ("android:roundIcon", (T_REF, R_MIPMAP_ICON)),
                         ("android:theme", (T_REF, THEME_MATERIAL_NO_ACTION_BAR)),
                         ("android:allowBackup", (T_BOOL, 0)), ("android:hardwareAccelerated", (T_BOOL, 0xFFFFFFFF)),
                         ("android:supportsRtl", (T_BOOL, 0xFFFFFFFF)),
                         ("android:usesCleartextTraffic", (T_BOOL, 0xFFFFFFFF))], [
            ("activity", [("android:name", PKG + ".MainActivity"), ("android:exported", (T_BOOL, 0xFFFFFFFF)),
                          ("android:launchMode", (T_INT_DEC, 2)),                 # singleTask
                          ("android:configChanges", (T_INT_HEX, config_changes)),  # rotating keeps the page
                          ("android:windowSoftInputMode", (T_INT_HEX, 0x10))], [   # adjustResize
                ("intent-filter", [], [
                    ("action", [("android:name", "android.intent.action.MAIN")], []),
                    ("category", [("android:name", "android.intent.category.LAUNCHER")], []),
                ]),
            ]),
        ]),
    ])
    return Axml(root).build()


def adaptive_icon() -> bytes:
    return Axml(("adaptive-icon", [], [
        ("background", [("android:drawable", (T_REF, R_COLOR_BG))], []),
        ("foreground", [("android:drawable", (T_REF, R_DRAWABLE_FG))], []),
    ])).build()


# ---- resources.arsc --------------------------------------------------------------------------

def res_config(density=0) -> bytes:
    cfg = bytearray(64)
    struct.pack_into("<I", cfg, 0, 64)
    struct.pack_into("<H", cfg, 14, density)
    return bytes(cfg)


def res_type(type_id, entries, density=0) -> bytes:
    """entries: list of (key_index, value_type, data) for entry 0..n-1"""
    cfg = res_config(density)
    header_size = 20 + len(cfg)
    offsets, body = [], bytearray()
    for key, typ, data in entries:
        offsets.append(len(body))
        body += struct.pack("<HHI", 8, 0, key) + struct.pack("<HBBI", 8, 0, typ, data & 0xFFFFFFFF)
    entries_start = header_size + 4 * len(entries)
    size = entries_start + len(body)
    return (struct.pack("<HHIBBHII", 0x0201, header_size, size, type_id, 0, 0, len(entries), entries_start) + cfg
            + b"".join(struct.pack("<I", o) for o in offsets) + bytes(body))


def res_spec(type_id, flags) -> bytes:
    return struct.pack("<HHIBBHI", 0x0202, 16, 16 + 4 * len(flags), type_id, 0, 0, len(flags)) + \
        b"".join(struct.pack("<I", f) for f in flags)


def resources(fg_path, icon_path) -> bytes:
    values = string_pool([fg_path, icon_path])
    types = string_pool(["color", "drawable", "mipmap"])
    keys = string_pool(["bg", "fg", "ic_launcher"])
    name = PKG.encode("utf-16-le")[:254].ljust(256, b"\x00")
    header_size = 288
    chunks = types + keys + \
        res_spec(1, [0]) + res_type(1, [(0, T_COLOR_ARGB8, 0xFF0A0A0C)]) + \
        res_spec(2, [0x0100]) + res_type(2, [(1, T_STRING, 0)], density=640) + \
        res_spec(3, [0]) + res_type(3, [(2, T_STRING, 1)])
    pkg = struct.pack("<HHII", 0x0200, header_size, header_size + len(chunks), 0x7f) + name + \
        struct.pack("<IIIII", header_size, 3, header_size + len(types), 3, 0) + chunks
    body = values + pkg
    return struct.pack("<HHII", 0x0002, 12, 12 + len(body), 1) + body


# ---- zip with aligned stored entries ------------------------------------------------------------

def build_zip(files: list[tuple[str, bytes, bool]]) -> bytes:
    """files: (name, data, compress). Stored entries start on a 4-byte boundary (zipalign)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data, compress in files:
            zi = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            zi.create_system = 0
            zi.external_attr = 0
            zi.compress_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
            if not compress:
                offset = buf.tell() + 30 + len(name.encode())
                zi.extra = b"\x00" * ((4 - offset % 4) % 4)
            z.writestr(zi, data)
    return buf.getvalue()


# ---- APK Signature Scheme v2 --------------------------------------------------------------------

def _lp(b: bytes) -> bytes:
    return struct.pack("<I", len(b)) + b


def _chunk_digest(parts: list[bytes]) -> bytes:
    digests = []
    for part in parts:
        for i in range(0, len(part), 1 << 20):
            chunk = part[i:i + (1 << 20)]
            digests.append(hashlib.sha256(b"\xa5" + struct.pack("<I", len(chunk)) + chunk).digest())
    return hashlib.sha256(b"\x5a" + struct.pack("<I", len(digests)) + b"".join(digests)).digest()


def sign_v2(apk: bytes, key, cert_der: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    eocd = apk.rindex(b"PK\x05\x06")
    cd_off, = struct.unpack_from("<I", apk, eocd + 16)
    entries, cd, eocd_bytes = apk[:cd_off], apk[cd_off:eocd], apk[eocd:]
    alg = 0x0103                                   # RSASSA-PKCS1-v1_5 with SHA2-256
    digest = _chunk_digest([entries, cd, eocd_bytes])
    signed = _lp(_lp(struct.pack("<I", alg) + _lp(digest))) + _lp(_lp(cert_der)) + _lp(b"")
    sig = key.sign(signed, padding.PKCS1v15(), hashes.SHA256())
    pub = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    signer = _lp(signed) + _lp(_lp(struct.pack("<I", alg) + _lp(sig))) + _lp(pub)
    value = _lp(_lp(signer))
    pair = struct.pack("<Q", 4 + len(value)) + struct.pack("<I", 0x7109871a) + value
    size = len(pair) + 8 + 16
    block = struct.pack("<Q", size) + pair + struct.pack("<Q", size) + b"APK Sig Block 42"
    new_eocd = bytearray(eocd_bytes)
    struct.pack_into("<I", new_eocd, 16, cd_off + len(block))
    return entries + block + cd + bytes(new_eocd)


def load_or_create_key(path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    if os.path.exists(path):
        pem = open(path, "rb").read()
        key = serialization.load_pem_private_key(pem, None)
        cert = x509.load_pem_x509_certificate(pem[pem.index(b"-----BEGIN CERTIFICATE-----"):])
    else:
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "LENTA Media Server"),
                          x509.NameAttribute(NameOID.ORGANIZATION_NAME, "LENTA")])
        now = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now)
                .not_valid_after(now + datetime.timedelta(days=365 * 40)).sign(key, hashes.SHA256()))
        with open(path, "wb") as f:
            os.chmod(path, 0o600)
            f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
            f.write(cert.public_bytes(serialization.Encoding.PEM))
        print(f"Created signing key {path} — keep it: updates must be signed with the same key.")
    return key, cert.public_bytes(serialization.Encoding.DER)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--key", default=os.path.join(HERE, "lenta-release.pem"))
    ap.add_argument("--out", default=os.path.join(HERE, "lenta.apk"))
    args = ap.parse_args()
    read = lambda p: open(os.path.join(HERE, p), "rb").read()
    fg, icon = "res/drawable-xxxhdpi-v4/ic_launcher_foreground.png", "res/mipmap-anydpi-v26/ic_launcher.xml"
    files = [
        ("AndroidManifest.xml", manifest(), True),
        ("classes.dex", Dex(classes()).build(), True),
        ("resources.arsc", resources(fg, icon), False),
        (fg, read("res/ic_launcher_foreground.png"), False),
        (icon, adaptive_icon(), True),
        ("assets/setup.html", read("assets/setup.html"), True),
        ("assets/lenta-logo.png", read("assets/lenta-logo.png"), False),
    ]
    key, cert = load_or_create_key(args.key)
    apk = sign_v2(build_zip(files), key, cert)
    with open(args.out, "wb") as f:
        f.write(apk)
    print(f"Built {args.out} ({len(apk) // 1024} KB) — LENTA {VERSION} for Android {MIN_SDK - 18}+ ")


if __name__ == "__main__":
    main()
