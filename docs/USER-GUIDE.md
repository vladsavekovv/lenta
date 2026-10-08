# LENTA User Guide

Everything LENTA can do once it is installed. New here? Start with the
[installation guide](INSTALL.md).

- [How LENTA works](#how-lenta-works)
- [Organising files](#organising-files)
- [Phones and tablets](#phones-and-tablets-the-lenta-app)
- [Libraries](#libraries-in-the-menu)
- [Looks](#looks-five-dark-themes)
- [Subtitles](#subtitles)
- [Trailers and theme music](#trailers-and-theme-music)
- [Metadata, artwork and the About panel](#metadata)
- [Users](#users)
- [API](#api)
- [Keyboard shortcuts](#keyboard-shortcuts)

## How LENTA works

**LENTA Media Server** (`server/lenta/`, Python 3.11+)

| Module | Job |
|---|---|
| `scanner.py` | Walks library folders, parses names (guessit), reads tags and EXIF, builds movie / show › season › episode / album › track / photo-album trees, removes what disappeared, runs on a schedule |
| `metadata.py` | TMDB lookups: posters, backdrops, title logos, cast, crew, ratings, age ratings, season and episode titles and stills |
| `probe.py` | ffprobe: codecs, resolution, HDR, audio and subtitle streams |
| `transcode.py` | Hardware detection (NVENC → Quick Sync → VA-API → x264), FFmpeg HLS sessions, throttling, idle clean-up |
| `api_playback.py` | Direct play / direct stream / convert decision, range streaming, HLS, subtitles, watch progress |
| `api_library.py` | Home rows, libraries, details, Continue watching and Next up, search, people, My list, artwork |
| `api_admin.py` | Dashboard, libraries, users and permissions, settings, metadata fixes, sessions, logs |
| `db.py` | SQLite schema (WAL) and runtime settings |

**LENTA Web Client** (`web/`, plain HTML/CSS/JS modules — nothing to build)

Profile picker sign-in, home billboard with rows, library grids with genre / watch-state / sort
filters, detail pages with seasons and cast, global search (titles, people, episodes, songs),
My list, a full player (seek anywhere, audio track and subtitle choice, quality, playback
stats, next-episode autoplay, keyboard shortcuts), a music bar that keeps playing while you
browse, a photo lightbox, and the admin dashboard. Fonts and hls.js are bundled, so it works
with no internet access.

### How playback is decided

| Mode | When | Cost |
|---|---|---|
| **Direct play** | Browser supports the container, video and first audio codec as-is (e.g. MP4 H.264/AAC) | none — file is streamed with byte ranges |
| **Direct stream** | Video is H.264 but container or audio isn't browser friendly (e.g. MKV with AC3/DTS) | tiny — video copied, audio → AAC, repackaged as HLS |
| **Convert** | HEVC/AV1 the browser can't decode, HDR, picture subtitles (PGS/VobSub), or a lower quality was chosen | GPU (NVENC) or CPU encode to H.264 |

HDR sources are tone-mapped to SDR. Text subtitles (SRT, ASS, embedded or sidecar, including
cp1251 Cyrillic files) are converted to WebVTT and drawn by the player; picture subtitles are
burned in by the server. FFmpeg is paused when it is more than ~3 minutes ahead of the viewer
and stopped 90 seconds after the viewer leaves.

## Organising files

```
Movies/
  Inception (2010)/Inception (2010).mkv
  Inception (2010)/Inception (2010).bg.srt        ← sidecar subtitles: name.<lang>[.forced].srt
  Inception (2010)/poster.jpg  fanart.jpg         ← optional own artwork (wins over TMDB)
TV/
  Breaking Bad/Season 01/Breaking.Bad.S01E01.mkv
  Breaking Bad/Specials/Breaking.Bad.S00E01.mkv
Music/
  Artist/Album (Year)/01 - Track.flac             ← tags are preferred; cover.jpg or embedded art
Photos/
  Rila 2024/IMG_0001.jpg                          ← each folder becomes an album
```

Two files that parse to the same movie become versions of one title (pick the version on the
detail page). Folders named `Extras`, `Trailers`, `Featurettes` and `sample` files are skipped.

Real-world names work too. LENTA reads the file name and each folder above it and uses the nearest one
that names a single film:

- collection folders (`The Hunger Games Collection (2012 - 2015)`, `Poltergeist Trilogy 1982,1986,1988`,
  anything with *Collection, Trilogy, Saga, Complete…* or a year range) never name a film;
- release-group file names without a title (`wanoes.avi`, `CD1.avi`, `VTS_01_1.VOB`) defer to their folder;
- `Part 1`, `Vol 2` and subtitles (`The Lord of the Rings - The Two Towers`) stay in the title.

Libraries scanned by an earlier version are re-read once after updating: films that were filed under a
collection folder's name are split into their own titles and matched again. Titles you fixed by hand
are left alone. Symbolic links are followed, but each folder is read once, so a link pointing back up
the tree can't stall a scan. If a scan can't read something (permissions, file names that aren't
UTF-8 on a CIFS mount), the reason is shown under the library in **Admin → Libraries**.

### New media is added by itself

LENTA watches the library folders, also on a NAS. Copy a film or episode into a library folder and it appears in
LENTA with its poster, details, trailer and the rest about a minute after the copy has finished: LENTA notices the
new folder, waits until its files stop growing (so a copy in progress is never read half-done), then scans only that
folder and fetches the metadata at once. Deleted folders disappear from the library the same way.

Network shares don't announce new files to Linux, so LENTA looks at the folders' modification times every minute
(folder times only, never file contents: light even for thousands of titles). **Admin › Settings › General ›
Watch library folders for new media** switches this off or changes how often it looks (30 seconds to 5 minutes);
the Dashboard shows how many folders are watched and whether a copy is in progress. The regular full scan (*Scan
libraries every*) still runs as a safety net.

**On demand from a script, Radarr or Sonarr:** ask LENTA to scan one folder right away (the title's folder or any
file in it). Sign in as an administrator once, keeping the session cookie, then call `scan-path`:

```bash
curl -s -c ~/.lenta-cookie -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "…"}' http://<server>:8484/api/auth/login
curl -s -b ~/.lenta-cookie -H "Content-Type: application/json" \
  -d '{"path": "/mnt/media/Movies/Inception (2010)"}' http://<server>:8484/api/admin/scan-path
```

## Phones and tablets (the LENTA app)

The same address works on phones and tablets, with a layout made for them:

- **Phones** get a bottom tab bar: Home, Search, Libraries, My list and your profile (Settings, Server
  admin, Change password, Sign out). Menus open as sheets from the bottom; swipe a sheet down to close it.
  Library pages show whole columns (the size slider picks 1–4 per row), admin tables become cards, and dialogs
  fill the screen. A phone on its side keeps the tab bar and uses the full width.
- **Tablets** use the icon menu. Long-press a library (or right-click it) for its options.
- **Player on touch screens**: tap to show or hide the controls, big ⟲10 / play / 10⟳ buttons in the middle,
  double-tap the left or right side to jump 10 s (keep tapping for 20, 30 …). Full screen turns the phone to
  landscape on Android. On iPhone the player already fills the screen (Safari has no full-screen mode for web pages).
- **Photos**: swipe for the next or previous picture, swipe down to close.

**Install it as an app.** Settings › *LENTA app* shows how on the device you're using:

- **iPhone / iPad** (Safari): Share › *Add to Home Screen*. LENTA opens full screen with its own icon.
- **Android / Chrome / Edge**: *Install the LENTA app* in Settings or in your profile menu.
  Browsers only offer a full install on a secure (https) address, so this needs LENTA behind https
  (see [Access from outside your home](INSTALL.md#access-from-outside-your-home); with Tailscale, `tailscale serve --bg 8484` gives you an https address). On a plain
  `http://192.168…` address you can still use the browser menu › *Add to Home screen* as a shortcut.

The installed app keeps a copy of its own pages, so it opens instantly and says clearly when the server
can't be reached. Your media is never stored on the phone.

### Android app (APK)

`web/download/lenta.apk` is a small Android app (Android 8 or newer) that opens your LENTA server full
screen, without a browser and without any browser settings:

1. On the phone, open LENTA in Chrome › profile › Settings › **LENTA app** › *Download the Android app (APK)*
   (or open `http://<server>:8484/download/lenta.apk`).
2. Open the downloaded file. Android asks once to allow installing apps from Chrome (*Settings › Install
   unknown apps*); allow it, go back and tap **Install**. Play Protect may warn that it doesn't know the
   developer: tap *More details › Install anyway* (it's your own app, not from the Play Store).
3. Open **LENTA**, type your server's address (for example `192.168.1.50:8484`) and tap **Connect**.

In the app: Back goes back in LENTA, full-screen video turns sideways and hides the system bars, the screen
stays on while a video plays, *Download original file* uses Android's downloads, artwork uploads use the
phone's file picker, and links to other sites (IMDb, Wikipedia) open in your browser. To point the app at
another server: profile › **Change server…**. If the server can't be reached the app says so, with *Try
again* and *Use a different address*.

**Rebuilding the APK** is described in [CONTRIBUTING.md](../CONTRIBUTING.md#rebuilding-the-android-app).

## TVs and the remote control

On a TV, LENTA works with the remote: arrows move between pictures and buttons, OK opens, Back goes back, and in
the player OK plays and pauses, ◀ ▶ jump 10 seconds and ▲ ▼ bring up the controls. LENTA switches to this mode by
itself in the LENTA Samsung TV app and in TV browsers; on any other browser add `?tv=1` to the address once
(`?tv=0` switches it off). Installing the Samsung TV app: [Samsung TV guide](SAMSUNG-TV.md).

## Libraries in the menu

Administrators can reorder libraries by dragging them in the side menu (or with **Move up / Move down** in
their ⋯ menu). The order is the same for everyone and also sets the order of the *Recently added* rows on Home.
The ⋯ next to each library (shown on hover) offers **Scan library files**, **Refresh all metadata** (fields you
locked stay as they are), **Fix missing metadata…** (the titles in that library without a match, with *Search
again* for just that library) and **Edit library…** (name and folders).

## Libraries: picture style, size and views

Movies and shows are shown as wide pictures with the name below (Netflix style) everywhere: Home,
libraries, search, My list. **Settings › Look › Pictures of movies and shows** switches back to the
original portrait posters. In a library, the **slider** next to the sort menu makes the pictures bigger
or smaller (fewer or more per row), and the **view menu** beside it switches between **Grid**, **Detail**
(picture, facts, genres and the start of the description) and **Table** (click a column heading to sort).
Both are remembered for you.

**My list** shows the titles you saved, as plain pictures (no preview or trailer when you point at them).
To tidy it, point at a picture and press the round button in its corner; once one is selected, a click on
any picture selects it too. The bar at the bottom offers *Select all*, *Clear* and **Remove from My list**.
Esc clears the selection.

**Pictures of TV episodes.** In Continue watching, Next up and the rows of new episodes, an episode shows its
show's picture with the show's title logo, like a movie (the season, number and name stay below it). **Settings ›
Look › Pictures of TV episodes** switches back to a picture from the episode itself.

## Looks: six dark themes

Each person picks a theme in **Settings** (rail › Settings). The administrator sets the default for the
sign-in screen and new users in Server admin › Settings.

| Theme | Feel |
|---|---|
| Projector | Plum night and an amber lamp, condensed marquee titles (default) |
| Noir | Graphite and silver, no colour, tight modern titles, sharp corners |
| Abyss | Deep water with one thread of cyan, thin wide titles, soft corners |
| Velvet | Oxblood curtains and crimson, classic picture-palace titles |
| Aurora | Midnight indigo with a violet-to-cyan glow, bold titles, round corners |
| Redline | The streaming-service look: near-black and signal red, the menu as a bar along the top, a full-width billboard, rows of wide pictures without names under them, white Play and grey More info buttons |

Media pages show the backdrop full screen. It drifts slowly, fades and blurs as you scroll, and the
details sit on translucent panels.

**The server's default theme** is used on the sign-in screen ("Who's watching?") and for users who haven't
picked a theme. An administrator sets it in Settings › Look (pick a theme, then *Use … on the sign-in screen*)
or in Server admin › Settings › Default theme. With Redline the sign-in screen has the streaming-service
look: a deep red background, the silver LENTA logo in the corner and large square profiles.

## The Settings page layout

The same works on **Server admin › Dashboard** and **Server admin › Settings** (the buttons are above the
blocks); each of the three pages keeps its own layout. The Dashboard keeps refreshing its figures without
moving or opening your blocks, and pauses while you reorder.

The blocks of Settings sit side by side (three columns on a wide screen, two on a laptop). Press **Reorder**
at the top right to place them however you like; the layout is free, not a table:

- **Move** a block by dragging its bar: put it anywhere — left, centre, right, next to another block. It snaps to the left and right edges and the centre of the page and to the edges of the
  other blocks (a red guide line shows the snap). Near the top or bottom of the window the page scrolls along.
- **Resize** a block by dragging its left or right edge (width), its bottom edge (height) or its corner (both).
  A block shorter than its content scrolls inside.
- **Quick buttons** on each block: *Left*, *Centre*, *Right*, *Full* (the whole width), *Fit* (as tall as its content).
- **Arrange all**: *Stack left*, *Stack centred*, *Stack right* (all blocks in one column on that side),
  *Columns* (the original arrangement) and *Tidy up* (closes the gaps by moving blocks up).
- Blocks never overlap: when a block would cover another, the other one moves down below it.
- There are never empty gaps: every block always sits right under the block above it, also after folding a
  block or when a block's content gets shorter.
- **Save** keeps the layout for you; **Cancel** or Esc leaves without saving.
- **Folding**: every block has an arrow on its title. Click it to fold the block to just its title (the blocks
  below move up), click again to open it. In Reorder mode, the **Starts open** tick on each block chooses
  whether it is open or folded when the Settings page opens; *Save* keeps that too.

Every change is recorded in the background with the layout (not shown on the page). **Default** puts back
the original layout at any time. Positions are
kept as parts of the page width, so a layout fits any window; phones always show the blocks in one column,
in the same order. Settings themselves are not affected. On old TV browsers the page keeps the plain layout.

## Subtitles

**Preferences** (Settings › Audio and subtitles, per person): automatically select tracks, preferred
audio language, subtitle mode (off / only forced / always / when the audio isn't in my language),
preferred subtitle language, and *download missing subtitles automatically when I press Play*.

**Online search** uses OpenSubtitles.com:

1. Create a free account at opensubtitles.com, then *API consumers* › create one to get an **API key**.
2. Server admin › Settings › Online subtitles: paste the key, add your username and password
   (optional, raises the daily download limit), press **Test OpenSubtitles**.

Then:
- **Media page:** the subtitles button lists the tracks the file has and searches online in your
  preferred language (any other language from the list).
- **Player:** Audio & subtitles › *Search online…*. A downloaded track switches on straight away.
- **Automatic:** when you press Play and no track exists in your subtitle language, LENTA fetches the best
  one. A subtitle made for your exact file (matched by file hash) wins; then the most downloaded.

Downloads are stored in `subtitles/` inside the data folder (your media folders stay read-only) and shared by all
users. Windows-1251 Cyrillic and other legacy encodings are detected automatically.

## Trailers and theme music

**Home banner trailers** play only for titles you choose: open a title's **Edit › General** and tick **Show the
trailer in the Home banner**. Every other title shows its picture in the banner. In **Settings › Home screen**,
**30 seconds limit** stops banner trailers after 30 seconds of playing; the picture and description come back with a
Replay button.

**Where trailers come from**, best first (Admin › Settings › Trailers):

1. **Your trailer files** next to the media, named like Plex and Jellyfin expect: `Movie Name-trailer.mp4`,
   `trailer.mp4`, `…-trailer2.mp4`, or any video in a `trailers` folder (for a show, in the show's folder).
   MP4, M4V, WebM or MOV so the browser can play them. LENTA never adds these files as titles.
2. **Apple TV trailers** for movies — HD video files found on Apple by title and year (no key). Titles shown in
   another language are also searched by their original title, and when your country's Apple store has no films the
   US store is used. Looked up slowly in the background (Apple allows about 20 searches a minute); **Test Apple
   trailers** in the same settings panel checks that your server can reach Apple.
3. **KinoCheck** — the official trailer for the title, picked by KinoCheck's editors, in 1080p or better (English;
   German too when your metadata language is German). Played from YouTube. Works without a key (1,000 lookups a day);
   a free KinoCheck API key in the same panel allows more. **Test KinoCheck** checks it.
4. **YouTube** trailers listed on TMDB.

**Skipping the green card.** Many older trailers open with the green "The following preview has been approved for
appropriate audiences" card. **Settings › Media pages › Start trailers after** (3, 5 or 8 seconds) starts every
trailer that far in: on media pages, in hover previews, in the Home banner and with the Trailer button. The previews
in Edit metadata › Trailers always start at the beginning.

**YouTube trailers that don't play in your country.** Uploaders can block a video in some countries or switch
off embedding ("Video unavailable — The uploader has not made this video available in your country"). When that
happens LENTA notices, switches to the next trailer that does play (right away, in the same spot), remembers the
blocked video for 60 days and skips it for every title. A trailer you picked by hand stays your choice: it is
marked *Doesn't play here* in Edit metadata › Trailers, and LENTA plays the next one until you pick another.

**Your own YouTube links.** In Edit metadata › Trailers, paste any YouTube link (`youtube.com/watch?v=…`,
`youtu.be/…`, `/shorts/…`) and press **Add link**. LENTA checks it with YouTube (it refuses removed or private
videos and ones the uploader doesn't allow on other sites), uses it straight away and keeps it with the title.
Your links come right after your trailer files: before Apple, KinoCheck and TMDB, and first in line when the
trailer in use doesn't play in your country. Add a few as backups if you like; **Remove link** takes one away.

Trailer files from 1 and 2 play directly in LENTA: no ads or YouTube logo, they start at once, and a hover
preview hands over to the title's page to the exact frame. Edit metadata › Trailers lists all of them
(marked *Your file*, *Apple TV*, *KinoCheck*, *YouTube*) so you can pick one per title. Titles indexed before this update
are looked at again once in the background.

**IMDb ratings** appear next to TMDB's on every title. They come from IMDb's free daily ratings file (IMDb
Non-Commercial Datasets, for personal use) — no key; switch off in Admin › Settings › About panel extras.


Each person chooses in Settings › Media pages:

- **Trailers:** off, a Trailer button, or playing in the background of the media page after a
  few seconds (Netflix style), either muted or with sound, with sound and stop controls. With sound,
  theme music steps aside while the trailer plays. Browsers only allow sound after you have clicked
  something on the page, so a media page opened straight from a bookmark starts the trailer muted. Where they come from is described above.
- **Home banner trailer** (Settings › Home screen, a separate setting): the featured title at the top
  of Home plays its trailer after a few seconds — off, muted, or with sound — with a sound button.
  Its sound fades as you scroll away (it pauses once the banner is mostly gone), and fades out over the
  last seconds of the 30 seconds limit; then the picture comes back.
  The same section chooses which movie and TV libraries the banner picks from, and how often it moves
  on to another random title (with that title's trailer): never, 30 seconds, 1 (default), 2, 5 or
  10 minutes. Titles don't repeat until all have been shown; it waits while you're scrolled down or in another tab.
- **Highest rated on this server** is a Top 10 row: the ten best-rated titles (7 and up), each with a big
  outlined number beside its poster.
- **Sections** (Settings › Home screen › *Sections*): every row on Home (Continue watching, My list, each
  *Recently added* row, Highest rated, each genre) is listed, and you can:
  - **move** one: drag it by its handle, or use the ▲ ▼ arrows (handy with a TV remote);
  - **edit** one (✎): rename it, choose which libraries fill it (any of your libraries), or delete it.
    Deleted sections are listed under *Deleted sections* with a *Restore* button; *Original settings*
    undoes a rename or library change;
  - **add your own** with *New section*: a name, the libraries, and what it shows: recently added,
    highest rated (a Top 10 row), shuffled (a new mix every day) or one genre. New sections start at the top.
  Press *Save changes* to keep it. A section that appears later, such as a new library or genre, is listed
  too and starts next to the section it follows by default. *Default order* puts the order back.
- **Theme music:** plays softly when you open a movie or show and fades out when you leave or press Play.
  Sources, in order:
  1. a file next to the media: `theme.mp3` (or .m4a .flac .ogg .opus .wav) in the movie folder or the
     show's top folder, or the first file in a `theme-music/` folder. This is the Plex/Jellyfin convention;
  2. if *Look up soundtrack previews online* is on (Server admin › Settings), the 30-second Apple Music
     preview of the soundtrack album's main theme, with a link to Apple Music.

**Trailer index.** LENTA looks up every movie's and show's trailer in the background and keeps it in its
database: a minute after the server starts, after every library scan and once a day (titles TMDB has no
trailer for are asked again after two weeks). Pages arrive with the trailer already known, so previews,
media pages and the Home banner start loading it at once instead of asking TMDB first; the browser also
connects to YouTube in advance. Progress shows on the admin Dashboard ("Trailers indexed"). What remains
is YouTube's own start-up, usually under a second on a good connection.

Episodes use their show's trailer and theme. Lookups are cached; titles without a result are retried
after two weeks.

## Metadata

Create a free account at themoviedb.org → Settings → API, then paste the API key (or the
read access token) in **Admin → Settings**. Set *Language* to `bg-BG` for Bulgarian titles
and descriptions where TMDB has them.

Each title is searched in many forms before giving up: the name as read, Roman numerals as numbers
(`IV` → `4`), without edition words (*Extended, Remastered, Director's Cut, KP Cut…*), the series part
and the subtitle part separately (`VI The Final Nightmare` finds *Freddy's Dead: The Final Nightmare*),
shortened (`Poltergeist III Were Back` → *Poltergeist III*), the names of its folders, and finally as a
TV series for fan edits of a series. Shows are also searched by the names in their episode files (a
misspelt folder like `Hallo 4` still finds *Halo 4: Forward Unto Dawn*), and as a film when TMDB lists a
miniseries or TV film that way. Loose forms only count when the year agrees and the words really
match. Only titles still unmatched after all that appear under **Admin → Metadata**, with the list of
searches tried, *Search again for all*, *Fix match* (search and pick) and *Edit*. Fixed titles are
locked against rescans.

### Artwork sources

**Admin → Settings → Artwork sources** works like Jellyfin's image fetchers: an ordered list per media type
(Movies, TV shows), each source ticked on or off and moved with the arrows. For each picture (poster,
background, logo) the first switched-on source that has one is used:

| Source | Gives | Needs |
|---|---|---|
| Local images | `poster.jpg`, `fanart.jpg`, `logo.png` … next to the files (or `Movie Name-poster.jpg`) | — |
| Embedded images | cover art stored inside MKV (`cover.jpg`, `cover_land.jpg`) and MP4 files | — |
| TheMovieDb | posters, backgrounds, logos | TMDB key |
| Fanart.tv | HD posters, backgrounds, clear logos | free project API key from fanart.tv (personal key optional) |
| The Open Movie Database | posters (full size) | the OMDb key from About panel extras |
| AniList | anime covers and banners (off by default) | — |
| Screen grabber | a frame from the video when no source has a picture | — |

The editor's Poster, Background and Logo tabs list the pictures of every switched-on source, labelled.
*Apply to every title now* picks every title's pictures again with the current order (in the background);
pictures you chose or uploaded yourself are locked and kept. Pictures from other sites are downloaded the first
time they are shown and cached.

### The About panel

A title's **About** panel shows, from TMDB: director or creators, writers, producers, music, cinematography,
editing, studio or network, country, original and spoken languages, status and episode counts for shows,
release and air dates, budget, box office, keywords, and links to IMDb, TMDB and the official site. Names are
links: they open a page with every title in your libraries that person worked on. When the film belongs to a
series (*Back to the Future Collection*), the other parts you own appear under **Part of …**.

Two optional extras, set in **Admin → Settings → About panel extras**:

- **Wikipedia summary** (on by default, no key). The article's opening paragraphs, found through the
  title's Wikidata id, in your metadata language when that Wikipedia has the article (bg-BG → Bulgarian),
  otherwise English. Shown with a link to the article (text under CC BY-SA).
- **OMDb ratings** (free key, 1,000 lookups a day, from omdbapi.com → API Key; click the activation link
  they e-mail). Adds IMDb rating and votes, Rotten Tomatoes, Metacritic and awards. *Test OMDb* checks the key.

Titles matched before this feature are filled in the background after the trailer index (a minute after the
server starts, after every scan and daily), slowly enough to stay inside every service's limits; the OMDb
part keeps 200 lookups a day free for pages you open. Wikipedia text is refreshed every two months and
ratings every two weeks. **Admin → Dashboard** shows the progress.

### Editing metadata by hand

Resting the pointer on a movie, show or episode for half a second opens a preview card (Netflix style).
After a moment it plays the title's trailer over the picture, with a sound button in the corner
(Settings › Look › **Trailer in hover previews**: off, muted or with sound). While it plays, the Home banner's
trailer pauses and continues when you leave the preview; with sound, theme music pauses too. The card has a larger picture, **Play**, **My list**, **Watched**, the genres, year, length, rating and quality.
Its **⌄** button opens the options: More info, Mark as watched and, for administrators, **Edit metadata…**,
**Refresh Metadata** and **Fix match…**. On touch screens, where there is no pointer to rest, administrators
get a pencil (edit) and ⋮ (Refresh Metadata) on each poster instead. The same actions are in the ⋯ menu on
a title's page.

The editor has General (title, sort title, original title, edition, release date, year, content rating,
rating, runtime, studio, tagline, summary), Tags (genres, directors, writers, creators), Poster,
Background, Logo (or Thumbnail for episodes), Trailers and Info. **Trailers** (movies and shows) lists your own trailer files, Apple TV trailers and
YouTube trailers from TMDB, each playable in place; the one you pick is used on the title's page, in hover previews and in
the Home banner, and is locked so updates keep it ("No trailer for this title" turns trailers off for it). Artwork can come from TMDB's alternatives, an
uploaded JPG/PNG/WebP, or a link. Every field has a lock: whatever you change is locked, and
**Refresh Metadata** fetches everything else again but keeps locked fields. Click a lock to unlock a
field and let TMDB fill it again. Refreshing a season or an episode refreshes its show.

## Users

**Admin → Users**: add viewers, limit them to specific libraries, or make them administrators.
Each user has their own watch progress, Continue watching, Next up and My list.

## API

Interactive API docs: `http://<server>:8484/api/docs`. Every endpoint accepts the session
cookie or `Authorization: Bearer <token>`. Stream URLs also accept `?token=` so external
players (VLC, mpv) can open them.

## Keyboard shortcuts

`\` collapses the menu to icons or expands it again (also the ☰ button at the top of the menu; remembered on each device). Collapsed, the page you are on is marked with a bar and names show on hover.

### In the player

**Mini player.** The **⌄** button at the top left of the player shrinks the video into a small window in the
bottom-right corner (on phones, a bar above the tab bar), like Plex. It keeps playing while you browse LENTA;
trailers, theme music and hover previews stay quiet meanwhile. Click the picture (or ⤢) to go back to full screen
where you are, ⏯ to pause, ✕ to stop. The next episode starts in the mini player too.
Clicking the title at the top of the player opens the title's page (a show's page at that episode) the same way:
the video goes on in the mini player.

**Skip intro.** LENTA finds the intro of every TV episode by itself: episodes of a season share their theme
tune, so it compares the sound of the first minutes of each episode with the others (an audio fingerprint, like
Jellyfin's Intro Skipper). The stretch they share, 15 seconds to 2½ minutes long, is the intro. While it plays the
player shows **Skip intro**. In **Settings › TV show intros** each person chooses: show the button (default), skip
intros automatically (with **Watch intro** to go back), or off. Detection runs in the background a few minutes after
the server starts and after every scan, reads only the first minutes of each episode, and takes a few seconds per
season; a season you start watching is analysed next. A season needs at least two episodes. **Admin › Settings ›
Intro detection** switches it off for everyone; the Dashboard shows how many episodes have an intro.

**Seek-bar previews.** Move the pointer along the time bar (or drag it on a phone) and a picture from that
moment of the video appears above it, like in Plex. LENTA makes these pictures in the background at low priority,
newest titles first: one every 10 seconds, a minute or two of work and a few MB per film, kept in `trickplay/` in
the data folder. A video you open before its turn is made next. **Admin › Settings › Seek-bar previews** switches
this off or changes the spacing (5–30 seconds); the Dashboard shows how many videos are done.


`Space`/`K` play/pause · `←`/`→` or `J`/`L` 10 s · `↑`/`↓` volume · `M` mute · `F` full screen ·
`C` subtitles on/off · `G`/`H` subtitles 0.05 s earlier/later (`Shift` for 0.5 s) · `N` next episode · `Esc` back

If a subtitle file was made for a different release and runs early or late, fix it with `G`/`H` or
with Timing in the subtitle menu. The setting is remembered for that file and subtitle.
