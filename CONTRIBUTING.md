# Contributing to LENTA

Thanks for helping! LENTA is a hobby project, so please be patient and kind in issues and reviews.

LENTA is in the public domain ([the Unlicense](LICENSE)). By sending a change you agree that it is released into
the public domain on the same terms, and that it is your own work or already in the public domain. Code, images or
fonts under other licenses can only come in if their license allows it; list them in
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) with their license file.

## Reporting a bug

Open an issue with the **Bug report** template and include:

- Ubuntu version (`lsb_release -d`) and LENTA version (Server admin › Dashboard)
- GPU and driver, if the problem is about playback or conversion
- Browser or device, if the problem is in the web client or app
- What you did, what you expected, what happened
- The last 50 log lines: `sudo journalctl -u lenta -n 50 --no-pager`

**Remove API keys, passwords and private paths** before posting logs or screenshots.

## Running from source

You need Python 3.11+ and FFmpeg (`sudo apt install python3-venv ffmpeg`).

```bash
cd server
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
LENTA_DATA_DIR=./dev-data python -m lenta        # http://localhost:8484
```

The server serves the web client from `../web`, so changes to HTML, CSS and JavaScript only need a page
reload. Python changes need a restart. Interactive API docs are at `http://localhost:8484/api/docs`.

`LENTA_DEV_VP9=1` makes the server stream VP9/Opus instead of H.264, for testing in browser builds without
H.264 (such as Playwright's Chromium). Don't use it in production.

## Project layout

```
server/lenta/        the media server (FastAPI + SQLite)
  scanner.py           walks libraries, parses names, builds the title tree
  metadata.py          TMDB matching and details
  artwork.py           artwork sources (local, embedded, TMDB, Fanart.tv, OMDb, AniList, screen grabs)
  about.py, imdb.py    About panel extras: Wikipedia, OMDb, IMDb ratings
  trailers.py, trailer_sources.py    trailer index: local files, Apple TV, YouTube
  transcode.py         hardware detection and FFmpeg HLS sessions
  api_*.py             HTTP API
  db.py                schema, migrations, settings
web/                 the web client (ES modules, no build step)
  js/                  app.js (shell), views.js (pages), player.js, admin.js, settings.js, …
  css/                 app.css, mobile.css
android/             the Android app, built without the Android SDK
deploy/              install, update, uninstall, data-folder and password-reset scripts, systemd unit
docs/                user documentation
```

## Guidelines

- **Never install or change GPU drivers** from any script. LENTA uses what is on the machine.
- **Never write into media folders.** Downloads (subtitles, artwork) go into the data folder.
- **Never hard-code** the data folder, the service account or the port; they come from the environment.
- Keep the web client build-free: plain modules, no frameworks, no CDN at runtime.
- Database changes need a migration in `db.py` that works on existing installs.
- Keep the installer safe to run again: every step must cope with an existing install.
- Update the docs in `docs/` when you change something a user would notice.

## Sending a change

1. Fork the repository and create a branch: `git checkout -b fix-subtitle-timing`.
2. Make the change and test it on a real library if you can (and on a phone for UI changes).
3. Open a pull request describing what changed and how you tested it. Screenshots help for UI changes.

## Rebuilding the version for older TVs

`web/legacy/` holds LENTA's web client rewritten for older web engines (Samsung TVs 2017-2022, Chromium 47-87).
`web/index.html` loads it by itself on such engines; add `?legacy=1` to the address to try it in a normal browser.
It is generated from `web/js` and `web/css`, so **after changing those, rebuild it** (needs Node.js 18+):

```bash
cd tools
npm install
npm run legacy
```

The build (`tools/build-legacy.mjs`) bundles the JavaScript with esbuild, rewrites it for Chromium 47 with Babel and
adds missing functions from core-js (plus `KeyboardEvent.key`). The CSS gets fallbacks: plain sizes for `clamp()`,
`min()` and `max()`, comma colours, the fonts as ordinary fonts, flex `gap` as margins, a small script for
`aspect-ratio`, and (`tools/css-old.mjs`) plain values for every `var()` from the default theme and a flex layout
for every CSS grid. The page marks what the engine lacks with classes on `<html>`: `no-vars`, `no-grid`,
`no-flexgap`, `no-sticky`.

## Rebuilding the Android app

The APK in `web/download/lenta.apk` is built from `android/` with pure Python, no Android SDK:

```bash
sudo apt install python3-cryptography
python3 android/build_apk.py --out web/download/lenta.apk
```

It is signed with `android/lenta-release.pem`. **That key is private and is not in this repository.**

- If the key file is missing, the first build creates a new key and certificate there automatically.
  Back it up somewhere safe: you need the same key for every later version of *your* APK.
- Android only installs an update over an existing app if both are signed with the same key. An APK signed
  with your key can't update one signed with the project's key: uninstall the app first.
- Never commit a `.pem` file. `.gitignore` already excludes it.
