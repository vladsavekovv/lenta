"""Per-user preferences (look, playback languages, subtitles, trailers, theme music) and the language list."""
import json
from fastapi import APIRouter, Depends, HTTPException

from . import auth, languages
from .db import db, get_setting

router = APIRouter(prefix="/api")

THEMES = ["projector", "noir", "abyss", "velvet", "aurora", "redline"]
SUBTITLE_MODES = ["off", "forced", "always", "foreign"]   # foreign = only when the audio isn't in my language
TRAILER_MODES = ["off", "button", "background", "background_sound"]
HOME_TRAILER_MODES = ["off", "muted", "sound"]
BANNER_INTERVALS = ["0", "30", "60", "120", "300", "600"]   # the big banner on Home, set apart from media pages

PREF_DEFAULTS = {
    "theme": "",                     # empty = server default
    "auto_tracks": "1",
    "audio_lang": "",
    "subtitle_mode": "forced",
    "subtitle_lang": "",
    "subtitle_autodownload": "1",
    "trailer_mode": "background",
    "home_trailer": "muted",
    "home_trailer_30": "0",          # Home banner trailers stop after 30 seconds
    "card_style": "wide",
    "episode_art": "show",           # episodes in rows: show (the show's artwork and logo) | still (the episode's own frame)
    "menu_default": "collapsed",     # side menu when LENTA opens: collapsed (icons) | open
    "hover_trailer": "muted",        # trailer inside the hover preview: off | muted | sound            # wide = Netflix-style pictures, poster = the original portrait posters
    "library_zoom": "4",             # 1 (small, many per row) ... 7 (large, few per row)
    "library_view": "grid",          # grid | detail | table
    "home_banner_libraries": "",     # comma-separated library ids; empty = every movie and TV library
    "home_row_libraries": "",        # Highest rated + genre rows on Home: library ids; empty = every movie and TV library
    "home_row_order": "",            # Home sections in the user's order: JSON list of row ids; empty = default order
    "home_sections": "",             # own Home sections and changes to the others: JSON (api_library._sections_config)
    "settings_layout": "",           # Settings page blocks: places, sizes, folded ones, change log (JSON); empty = original
    "admin_dashboard_layout": "",    # Server admin › Dashboard blocks (same form as settings_layout)
    "admin_settings_layout": "",     # Server admin › Settings blocks
    "home_banner_interval": "60",    # seconds before the banner moves to another title; 0 = never
    "intro_skip": "button",
    "trailer_skip": "0",             # seconds cut from the start of trailers (the green "approved for appropriate audiences" card)          # TV intros: off | button ("Skip intro") | auto (skipped by itself)
    "theme_music": "1",
    "theme_music_volume": "0.35",
}


def _validate(key: str, value) -> str:
    v = "" if value is None else str(value).strip()
    if key == "theme" and v and v not in THEMES:
        raise HTTPException(400, "Unknown theme")
    if key in ("audio_lang", "subtitle_lang") and v and v not in languages.CODES:
        raise HTTPException(400, "Unknown language")
    if key == "subtitle_mode" and v not in SUBTITLE_MODES:
        raise HTTPException(400, "Unknown subtitle mode")
    if key in ("home_banner_libraries", "home_row_libraries"):
        ids = [x.strip() for x in v.split(",") if x.strip()]
        if not all(x.isdigit() for x in ids):
            raise HTTPException(400, "Unknown library")
        v = ",".join(dict.fromkeys(ids))
    if key == "home_row_order" and v:
        try:
            ids = json.loads(v)
        except ValueError:
            raise HTTPException(400, "Unknown section order")
        if not isinstance(ids, list) or not all(isinstance(x, str) and 0 < len(x) <= 200 for x in ids) or len(ids) > 500:
            raise HTTPException(400, "Unknown section order")
        v = json.dumps(list(dict.fromkeys(ids)), ensure_ascii=False)
    if key == "home_sections" and v:
        try:
            cfg = json.loads(v)
        except ValueError:
            raise HTTPException(400, "Unknown section settings")
        if not isinstance(cfg, dict) or len(v) > 60000 or len(cfg.get("custom") or []) > 100:
            raise HTTPException(400, "Unknown section settings")
        v = json.dumps(cfg, ensure_ascii=False)
    if key in ("settings_layout", "admin_dashboard_layout", "admin_settings_layout") and v:
        try:
            lay = json.loads(v)
        except ValueError:
            raise HTTPException(400, "Unknown layout")
        if not isinstance(lay, dict) or len(v) > 40000:
            raise HTTPException(400, "Unknown layout")
        v = json.dumps(lay, ensure_ascii=False)
    if key == "home_banner_interval" and v not in BANNER_INTERVALS:
        raise HTTPException(400, "Unknown banner interval")
    if key == "hover_trailer" and v not in ("off", "muted", "sound"):
        raise HTTPException(400, "Unknown preview trailer setting")
    if key == "trailer_skip" and v not in ("0", "3", "5", "8"):
        raise HTTPException(400, "Unknown trailer start setting")
    if key == "intro_skip" and v not in ("off", "button", "auto"):
        raise HTTPException(400, "Unknown intro setting")
    if key == "menu_default" and v not in ("collapsed", "open"):
        raise HTTPException(400, "Unknown menu setting")
    if key == "episode_art" and v not in ("show", "still"):
        raise HTTPException(400, "Unknown episode picture setting")
    if key == "card_style" and v not in ("wide", "poster"):
        raise HTTPException(400, "Unknown picture style")
    if key == "library_zoom" and v not in [str(n) for n in range(1, 8)]:
        raise HTTPException(400, "Zoom must be 1 to 7")
    if key == "library_view" and v not in ("grid", "detail", "table"):
        raise HTTPException(400, "Unknown library view")
    if key == "home_trailer" and v not in HOME_TRAILER_MODES:
        raise HTTPException(400, "Unknown Home trailer setting")
    if key == "trailer_mode" and v not in TRAILER_MODES:
        raise HTTPException(400, "Unknown trailer option")
    if key in ("auto_tracks", "subtitle_autodownload", "theme_music", "home_trailer_30"):
        v = "1" if v in ("1", "true", "True", "on") else "0"
    if key == "theme_music_volume":
        try:
            v = f"{min(1.0, max(0.0, float(v))):.2f}"
        except ValueError:
            raise HTTPException(400, "Volume must be between 0 and 1")
    return v


def get_prefs(user_id: int) -> dict:
    prefs = dict(PREF_DEFAULTS)
    with db() as con:
        for r in con.execute("SELECT key, value FROM user_prefs WHERE user_id = ?", (user_id,)):
            if r["key"] in prefs:
                prefs[r["key"]] = r["value"]
    if prefs["theme"] not in THEMES:
        prefs["theme"] = get_setting("default_theme") or "projector"
    return prefs


def features() -> dict:
    return {
        "subtitles_online": bool(get_setting("opensubtitles_api_key")),
        "theme_music_online": get_setting("theme_music_online") == "1",
        "intro_detection": (get_setting("intro_detection") or "1") != "0",
        "trailers": bool(get_setting("tmdb_api_key")),
    }


@router.get("/me/prefs")
def read_prefs(user: dict = Depends(auth.current_user)):
    return {"prefs": get_prefs(user["id"]), "features": features()}


@router.put("/me/prefs")
def write_prefs(body: dict, user: dict = Depends(auth.current_user)):
    clean = {k: _validate(k, v) for k, v in body.items() if k in PREF_DEFAULTS}
    with db() as con:
        for k, v in clean.items():
            con.execute("INSERT INTO user_prefs(user_id, key, value) VALUES(?, ?, ?) "
                        "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value", (user["id"], k, v))
    return {"prefs": get_prefs(user["id"]), "features": features()}


@router.get("/languages")
def language_list():
    return languages.as_list()
