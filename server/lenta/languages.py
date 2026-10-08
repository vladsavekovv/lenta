"""Languages: one table mapping the codes found in media files (ISO 639-2) and subtitle sites
(ISO 639-1, OpenSubtitles variants such as pt-br) to a single short code and display names."""

# code, english name, native name, other codes seen in files
LANGUAGES = [
    ("en", "English", "English", ["eng"]),
    ("bg", "Bulgarian", "български език", ["bul"]),
    ("ru", "Russian", "русский", ["rus"]),
    ("uk", "Ukrainian", "українська", ["ukr"]),
    ("sr", "Serbian", "српски", ["srp", "scc"]),
    ("mk", "Macedonian", "македонски", ["mac", "mkd"]),
    ("hr", "Croatian", "hrvatski", ["hrv", "scr"]),
    ("sl", "Slovenian", "slovenščina", ["slv"]),
    ("ro", "Romanian", "română", ["rum", "ron"]),
    ("el", "Greek", "Ελληνικά", ["gre", "ell"]),
    ("tr", "Turkish", "Türkçe", ["tur"]),
    ("de", "German", "Deutsch", ["ger", "deu"]),
    ("fr", "French", "français", ["fre", "fra"]),
    ("es", "Spanish", "español", ["spa"]),
    ("it", "Italian", "italiano", ["ita"]),
    ("pt", "Portuguese", "português", ["por", "pt-pt"]),
    ("pt-br", "Portuguese (Brazil)", "português (Brasil)", ["pob"]),
    ("nl", "Dutch", "Nederlands", ["dut", "nld"]),
    ("pl", "Polish", "polski", ["pol"]),
    ("cs", "Czech", "čeština", ["cze", "ces"]),
    ("sk", "Slovak", "slovenčina", ["slo", "slk"]),
    ("hu", "Hungarian", "magyar", ["hun"]),
    ("sv", "Swedish", "svenska", ["swe"]),
    ("no", "Norwegian", "norsk", ["nor", "nob", "nno", "nb"]),
    ("da", "Danish", "dansk", ["dan"]),
    ("fi", "Finnish", "suomi", ["fin"]),
    ("ar", "Arabic", "العربية", ["ara"]),
    ("he", "Hebrew", "עברית", ["heb"]),
    ("hi", "Hindi", "हिन्दी", ["hin"]),
    ("ja", "Japanese", "日本語", ["jpn"]),
    ("ko", "Korean", "한국어", ["kor"]),
    ("zh-cn", "Chinese (simplified)", "简体中文", ["chi", "zho", "zh", "zhs"]),
    ("zh-tw", "Chinese (traditional)", "繁體中文", ["zht"]),
]

_ALIASES = {}
for code, _, _, others in LANGUAGES:
    _ALIASES[code] = code
    for o in others:
        _ALIASES[o] = code


def normalize(code: str | None) -> str:
    """'bul', 'BG', 'bg' -> 'bg'.  Unknown -> the lowercased input; missing -> 'und'."""
    if not code:
        return "und"
    c = code.strip().lower().replace("_", "-")
    return _ALIASES.get(c, _ALIASES.get(c.split("-")[0], c))


def same(a: str | None, b: str | None) -> bool:
    """Same language, treating pt / pt-br and zh-cn / zh-tw as close enough."""
    na, nb = normalize(a), normalize(b)
    return na == nb or na.split("-")[0] == nb.split("-")[0]


def as_list() -> list[dict]:
    return [{"code": c, "name": n, "native": nat} for c, n, nat, _ in LANGUAGES]


CODES = {c for c, *_ in LANGUAGES}
