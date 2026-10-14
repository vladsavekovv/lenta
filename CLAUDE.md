# LENTA – context for Claude Code

You are working with **Vlad (Vladimir Savekov)**: SAP BASIS Solution Architect (EMA) and SAP RISE TSM,
owner of TSG 2010 LTD, Gabrovo. **LENTA** is his self-hosted Plex/Netflix-style media server, written
with Claude over many sessions. This file is the handoff; read it before changing anything.

He also keeps an Obsidian vault (`Y:\vault\Vault`, index `00 Home.md`) whose `Homelab/` folder holds
`LENTA media server`, `LENTA on Samsung TVs`, `LENTA Live TV and tuner` and `LENTA working rules`.
Update the matching note at the end of a session – one note per topic, never a second one.

## The tree
| Part | Path |
|---|---|
| Server (FastAPI + SQLite, Python) | `server/lenta/` – `main.py`, `api_*.py`, `db.py`, `dvb.py`, `livestream.py`, `streaming.py`, `config.py` (holds `VERSION`) |
| Web client (plain ES modules, no framework) | `web/js/`, `web/css/`, `web/index.html` |
| Built client for 2017–2022 TVs (**generated – never edit**) | `web/legacy/app.js`, `web/legacy/app.css` |
| Legacy build (esbuild + Babel + core-js, target Chrome 47) | `tools/build-legacy.mjs` |
| Android app / signing key (**never package, never commit**) | `android/` / `android/lenta-release.pem` |
| Samsung TV app shell | `tizen/` |
| Tests – standalone scripts, not pytest | `tests/test_*.py` |
| Installer and tools run on the server | `deploy/` |
| Docs Vlad maintains himself | `README.md`, `docs/INSTALL.md`, `docs/SAMSUNG-TV.md`, `docs/USER-GUIDE.md` |

## Where it runs
- Proxmox VM **`svkplex`**, Ubuntu 26.04. Server on **192.168.1.5:8484** (also `plex.svkad.int:9443`).
- Installed under **`/opt/lenta`**; the tarball is unpacked in `/usr/src/lenta`.

## Running and testing here
```bash
bash /tmp/claude-0/intro/uplive.sh          # test server on :8597, data in /tmp/claude-0/intro/data
python3 tests/test_tv_remote.py             # each test is a script; run the ones you touched, then all
```
- Playwright signs in with `[data-user="admin"]` → `#lp` = `secret123` → `#login button.primary`.
- To stop the test server use `pkill -f 'runliv[e].py'` – the obvious form kills its own shell.
- `?tv=1` forces TV mode, `?legacy=1` forces the old-engine bundle, `?keys=1` shows the key watcher.
- The tests hard-code `/tmp/claude-0/intro/uplive.sh`. It is an environment dependency, not part of the
  repo; if it is missing, the Playwright sections cannot run.

## After any change to `web/js` or `web/css`
```bash
cd tools && npm run legacy                  # rebuild web/legacy/
```
`tests/test_legacy_build.py` fails if the bundle is older than its sources, so this is not optional.

## Releasing
1. Bump **`server/lenta/config.py`** `VERSION`, the badge in `README.md`, and add a `CHANGELOG.md` entry
   at the top (newest first, in Vlad's voice: what was wrong, not what was edited).
2. Rebuild the legacy bundle, run **every** test, then package from the parent of the repo:
```bash
tar czf out/lenta.tar.gz --exclude='*.pem' --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='.git' --exclude='node_modules' --exclude='tools/.fills.js' lenta
tar tzf out/lenta.tar.gz | grep -c '\.pem$'      # must print 0
```
3. Send it with SendUserFile. He installs with:
   `cd /usr/src/lenta && sudo tar xzf ~/lenta.tar.gz && cd lenta && sudo ./deploy/install.sh`
4. Publishing to GitHub (`vladsavekovv/lenta`) is **separate and he asks for it explicitly**:
   `cd ~/github/lenta && ./tools/github-update.sh ~/lenta.tar.gz "message"`
   Before packaging for a publish: `cd /tmp/pub && git fetch -q origin`, compare `origin/main` against
   `/home/claude/lenta/.github-base`, **merge his edits rather than overwriting them**, and write the
   new SHA back to `.github-base`. Commit as the GitHub noreply address already configured in that
   clone – never his Gmail.

## Rules that must not be broken
- **Never package or commit `android/lenta-release.pem`.** The APK must be signed with it
  (certificate sha256 begins `e6497fd83eb1fc80`).
- **The installer never installs NVIDIA or video drivers** – it assumes they are already there.
- The **service user and the data folder are not hardcoded** (user `lenta`, never `savek`).
- Licence **Unlicense** – public domain, free for everyone, no copyright claimed.
- Never print or repeat his API keys (TMDB, KinoCheck, OpenSubtitles).
- Never overwrite his own edits to `README.md` / `docs/`.

## Televisions – read before touching `web/js/tv.js` or `web/css/tv.css`
- Two clients: 2023+ sets get the normal one; **2017–2022 sets get `web/legacy/`**. The choice tests a
  feature (`CSS.supports('aspect-ratio', …)`), **never the user agent** – a test enforces that.
- Chromium 47 (his 2017 set) has **no CSS grid, no custom properties**, and none of
  `append`/`prepend`/`before`/`after`/`replaceWith`/`replaceChildren`/`toggleAttribute`, element
  `scrollTo`/`scrollBy`, or the settings object for `scrollIntoView`. The fill-ins live in
  `tools/build-legacy.mjs`; `tests/test_legacy_runtime.py` runs the real bundle with those APIs deleted.
- Because there are no custom properties, **`var()` falls back to the plain value the stylesheet was
  built with** – the *default* theme's. Anything that must look right in every theme needs a fixed value.
- **Every rule in `web/css/tv.css` must be scoped to `html.tv`** – it is loaded by every browser and is
  full of `!important`. A test enforces it.
- His remote sends Back as **keyCode 10009 / `XF86Back`** *and* as a Tizen `tizenhwkey` event: one press
  arrives twice. Arrows 37/38/39/40, OK 13, and a stray `keyCode 0` after Enter.
- Measure, do not guess: `tests/test_tv_remote.py` counts how many positions and styles one arrow press
  asks the engine for. One press should measure at most a fifth of the page.

## How he works
- He tests on real televisions and sends photographs. **A photograph of the key watcher beats any
  amount of reasoning** – when something on the TV does nothing, ask for that before theorising.
- Verified facts only. If something cannot be reproduced here, say so plainly and say what would
  settle it, rather than guessing at a fix.
- Every fix gets a test that **fails on the previous version** – check that it does, and say so.

## Pick up here (next session, in order)
1. **Publish to GitHub.** `origin/main` is at **1.8.17** plus four README edits Vlad made on github.com
   on 13 Oct; **1.9.0 → 1.10.13 are unpublished**. The package in his hands is built on the current
   remote (`.github-base` = `769ba2e`) with those edits already merged, so `tools/github-update.sh`
   will accept it. If more time passes, check the remote again before building another package –
   the script stops rather than overwrite anything he has changed on github.com.
2. **Two faults waiting on his photographs**, not on code: which Settings page leaves Save greyed, and
   the `play:` lines from a film that will not start on the 2017 set. Ask; do not guess a fix.
3. **The library grid is the one page the 1.10.12 work does not help.** Home and the guide are built
   from rows, so a press can rule out whole rows at once; a grid is a single block, so every card on it
   is still measured. If he says a library page feels slower than Home, that is why.
4. **Live TV**: cache channel PIDs to skip the PAT/PMT read; `DMX_OUT_TSDEMUX_TAP` to reach 5 cached
   channels on one multiplex. The frequencies that will not lock are a signal problem on his line, not
   code – see the vault note before offering a software fix.
5. Small: stop the `em28xx` driver polling an infrared receiver the card says it does not have; the
   README still wants a photograph of the TV app.

## State at this handoff (14 Oct 2026)
- Version **1.10.13**. All 18 test files pass.
- **GitHub**: `origin/main` = `769ba2e` = **1.8.17** plus four of his README edits (13 Oct).
  **1.9.0 → 1.10.13 are unpublished.** `.github-base` in the tree records that commit.
- Open: a Save button in Settings reported to stay greyed until you leave the page and return – not
  reproduced on any page, with mouse or remote, on either client; the page and control are unknown.
- Open: films that will not start on the 2017 set – the key watcher now prints `play:` lines naming the
  route taken and the picture's own error; that photograph is still wanted.
- Open, and not a software fault: several DVB-C frequencies find carrier but never lock →
  `Homelab/LENTA Live TV and tuner` in the vault.
