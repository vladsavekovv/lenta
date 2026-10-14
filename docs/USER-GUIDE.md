# LENTA User Guide

Everything LENTA can do once it is installed. New here? Start with the
[installation guide](INSTALL.md).

- [How LENTA works](#how-lenta-works)
- [Organising files](#organising-files)
- [Audiobooks](#audiobooks)
- [Phones and tablets](#phones-and-tablets-the-lenta-app)
- [Live TV](#live-tv) · [the programme guide](#the-programme-guide) · [your own channels on Home](#your-own-channels-on-home)
- [Streaming services](#streaming-services)
- [Libraries](#libraries-in-the-menu)
- [Looks](#looks-seven-dark-themes)
- [Settings](#settings)
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
| `audiobooks.py` | Works out which files make one audiobook, reads chapters, and looks books up on Google Books and Open Library |
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
Audiobooks/
  Alien/01.Alien - Out of the Shadows.mp3         ← one file per book: the folder is the series
  J.R.R. Tolkien/The Hobbit/Chapter 01.mp3 …      ← a folder of chapter files is one book
  Frank Herbert/Dune.m4b                          ← one file per book, chapters inside
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

## Audiobooks

Make a library of the type **Audiobooks** (Settings › Libraries › Manage libraries › Add library). A library
that is already of the type **Music** can be switched: open it with **Edit library…**, pick **Audiobooks**
and save. Its files are read again as books. Listening progress and changes made by hand in that library
start over; your files are not touched. You can switch back to Music the same way.

**Which files make one book.** LENTA looks at every folder as a whole, so the usual layouts work without
renaming anything:

| Your folder | What LENTA makes |
|---|---|
| `Alien/01.Alien - Out of the Shadows.mp3`, `02.Alien - River of Pain.mp3` … | one book per file. The folder is the **series** and the leading number is the book's place in it: *Out of the Shadows*, Alien · Book 1 |
| `The Hobbit/Chapter 01.mp3`, `Chapter 02.mp3` … (names differ only by numbers and words like *chapter*, *part*, *track*, *disc*, *CD*) | one book; each file is a chapter |
| files that all carry the same album tag | one book named after the album tag |
| `Author/Book Title/…` | the folder above the book is the author |
| `Dune/Dune.m4b`, or a single file with a generic name (`audiobook.mp3`) | one book named after the folder |
| `Tolkien/The Hobbit.mp3` (a single file with a real name in a top folder) | the book is the file; the folder is the author |

Tags win over names: the album tag is the book's title, the album artist (or artist) tag is the author,
and the composer tag is the narrator. Chapters inside an `.m4b` (or any file longer than ten minutes)
are read and listed. The cover is `cover.jpg` / `folder.jpg` in the book's folder, a picture named like
the file, or the picture inside the file. LENTA plays `.m4b`, `.mp3`, `.m4a`, `.flac`, `.ogg`, `.opus`
and the other audio formats; the server converts what a browser can't play.

**Details from the internet.** After each scan LENTA looks every new book up on **Google Books** and
**Open Library**: description, author, year, genres and a cover (a cover in the book's folder or file is
kept). No API key is needed. A book that isn't found is listed in **Settings › Metadata › Fix matches**
with the other titles; use **Fix match** to search by hand — write `title by author` to narrow it down.
**Edit metadata** has the book fields (Author, Narrator, Series, Number in series) and offers the covers
found online; a field you change is locked, so **Refresh Metadata** keeps it.

**Listening.** A book's page shows the cover, the series, the author and narrator, the length, the
description and the chapters; below are the other books of the series and more by the author.
**Play** starts the book in the player bar at the bottom of the screen, which keeps going while you
browse: back 15 seconds, forward 30 seconds, previous and next chapter, the chapter list, speed
(0.75× to 2×, remembered) and the time left at that speed. Your place is saved on the server every few
seconds, so the book appears in **Continue watching** and carries on from the same spot on any device.
The **⋯** button marks a book finished or not finished.

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

## Live TV

Live TV comes from two places, and they share one channel list: a **USB TV tuner** plugged into the server,
and **playlists** (an m3u address or an Xtream Codes login). You set both up in *Settings › Live TV*, and you
watch them by adding a library of the type **Live TV** (Settings › Libraries › Add library — it asks for no
folders).

**The tuner needs nothing from you.** LENTA looks at the system's `/dev/dvb` every minute. When a tuner
appears it is identified — the stick's own name over USB, and the standards the chip reports (DVB-T, DVB-T2,
DVB-C, DVB-S/S2, ATSC) — and a tuner that has never been scanned is scanned once, quietly, in the background.
No driver is installed: nearly every USB tuner works with the drivers already in Ubuntu, and a few need a
firmware file in `/lib/firmware` that their maker provides.

**How a scan works.** LENTA tries the frequencies a broadcaster may legally use: UHF 21–60 and VHF 5–12 for an
aerial, the 8 MHz European raster from 114 to 858 MHz for cable, trying DVB-T2 wherever DVB-T doesn't lock and
the usual cable symbol rates. The first frequency that locks usually ends the sweep: the stream's own network
table (NIT) lists the network's other frequencies, so from there LENTA follows the network instead of trying
the rest of the band. From each stream it reads the service table (SDT) for the channel names and who provides
them, and the network table for **the provider's own name** — that is what shows as the provider, for example
*NURTS Digital* or your cable company — and for **the channel numbers the provider asks for**, so channel 1 is
where the broadcaster intends it to be. Radio services and scrambled channels are marked as such.
*Full scan (every frequency)* in the ⋮ menu sweeps the whole band instead, for when a network's table is wrong.

**Watching a channel.** Click it in the Live TV library. LENTA tunes a free input of the stick, takes the
whole multiplex off the adapter and picks that one channel out of it. A channel already broadcast in H.264
with AAC sound is passed straight through; the older MPEG-2 and MPEG-audio channels, which no browser will
play, are re-encoded on the way — on the graphics card if the server has one. What a channel is made of is
read from the stream's own programme table before anything starts, so nothing is re-encoded needlessly.

There is no seek bar, nothing to skip and no resume position, because a broadcast has none of those; a
**LIVE** badge sits where the clock would be. The mini player works as it does for a film.

**How many at once.** One input can only sit on one frequency, so a twin tuner like the WinTV-dualHD shows
**two different channels at once** — with any number of people watching each. Everyone on the same channel
shares one tuner. Asking for a third channel says which two are already on rather than failing quietly.

**A channel you leave is kept ready.** Starting a channel takes a few seconds — the tuner has to lock, and
the stream has to be read before anything can be sent on. Going back to one you were just watching should
not pay that again, so leaving a channel does not tear it down: it keeps running, and returning to it joins
what is already there. Up to five channels are kept this way, each for half an hour after the last person
leaves; a sixth pushes out whichever has been idle longest. None of this is ever at the expense of somebody
watching now — a channel being watched outranks every kept one, and a kept channel gives up its input the
moment a viewer needs it.

**Scanning and watching don't fight.** A scan retunes an input, so it never takes one somebody is watching:
it uses the stick's other input if one is free, and otherwise says who is watching. Watching is refused the
same way while that stick is mid-scan.

### The programme guide

**What is on comes from the broadcast**, not from a listings service: there is nothing to sign up for, no key
to paste and nothing to keep paying. Every transmission carries its own schedule alongside the picture, and
LENTA reads it.

**It costs nothing to collect.** The schedule on a multiplex describes *every* channel carried on it, not
just the one you are watching — so watching one channel quietly collects the guide for all of its
neighbours. Nothing extra is tuned and no extra bandwidth is used.

**The rest fills itself in.** Channels you never watch would otherwise never have a guide, so when an input
is spare LENTA tunes the multiplexes it has not heard from lately and reads theirs too. **A viewer always
outranks this**: the sweep gives an input back the instant somebody wants to watch, and will not start while
every input is busy. Both halves can be switched off separately in *Settings › Live TV* — *Collect while
watching* and *Tune spare inputs to collect the rest* — and that page says how much has been collected so far.

**Where you see it.** What is on now appears under every channel, in the library and on Home. The **Guide**
page — *What's on*, at the top of the Live TV library — shows the usual grid, with *Earlier*, *Now* and
*Later* to move through it and a line marking the present. The guide survives a rescan, and anything more
than six hours past is forgotten.

### Your own channels on Home

A Home section can be live channels instead of films. Make one in *Settings › Libraries › Home sections*,
choose the channels you actually watch, and they sit as a row among your films with what is on under each.
A channel that later disappears from the line-up takes itself out of the row.

**Channel artwork.** A broadcast carries no logo — only a name — so a logo has to be found by matching
that name against a collection of them. *Find channel logos*, in Settings › Live TV, does that: name your
country and press the button. LENTA asks the collection what it actually holds and matches your channels
against that, so it never guesses at addresses; a collection organised differently simply matches fewer
channels. Naming your country matters because many channels exist in several — it picks your BNT and your
bTV rather than someone else's. A different collection can be used instead, under *Use a different
collection*. Names are
compared forgivingly: *Star Channel HD* and *Star Channel* are the same channel, *24Kitchen* and
*24 Kitchen* likewise, and Cyrillic names are transliterated, so *СКАТ* finds a file called *skat*.

Anything unmatched gets **a monogram drawn from its own name** — initials on a coloured tile, the same
colours every time, in the name's own alphabet. Channels from a playlist keep the logo the playlist gave
them, and a logo you set by hand is never overwritten.

**Satellite** is detected but not yet scanned: which satellite you receive depends on where the dish points,
and the LNB and any DiSEqC switch have to be told. That page is coming next.

**Playlists.** *Add a playlist* takes an m3u address or an Xtream Codes login. LENTA reads the channel names,
logos, groups and channel numbers the playlist provides; a group or name that mentions radio, and audio-only
addresses, are marked as radio.

**A scan holds the tuner** while it runs — nothing else can use that input meanwhile. **Stop** next to the
tuner ends it at once and keeps whatever it found. *Scan a newly found tuner by itself* switches off the
automatic first scan, which is worth doing while you test the tuner from the command line.

**If a scan finds nothing**, check the tuner at the operating-system level, outside LENTA:

```bash
sudo /opt/lenta/deploy/tv-check.py              # look at everything, then sweep every band the tuner supports
sudo /opt/lenta/deploy/tv-check.py --freq 522   # try one frequency you know is used (MHz)
sudo /opt/lenta/deploy/tv-check.py --band cable # only the cable band
sudo /opt/lenta/deploy/tv-check.py --band aerial # only the aerial band
sudo -u lenta /opt/lenta/deploy/tv-check.py     # as the service account, to prove its access
```

It uses the very code LENTA uses, and prints: what the system shows (`/dev/dvb` and who may open it), how the
stick is attached (including whether its socket is a USB 3 one — those radiate noise across the band a tuner
listens to, so a tuner that receives nothing is worth moving to a USB 2 socket), what the
driver said while loading (a missing firmware file shows up here), what each tuner reports about itself, and
then every frequency it tries with the lock, signal strength and quality — and, where it locks, the channels
that frequency carries. It ends with what to do about whatever it found.

**The channel list.** Everything found is in one list in *Settings › Live TV*, in the provider's order. **Hide**
keeps a channel out of the Live TV library without deleting it. Playing a channel comes in the next step.

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
  Browsers only offer a full install on a trusted https address: open LENTA's HTTPS address
  (`https://<server>:8443`) after installing LENTA's certificate on the device ([how](INSTALL.md#install-lentas-certificate-on-your-devices)),
  or use your own certificate. With Tailscale, `tailscale serve --bg 8484` also gives you one. On a plain
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

In the app: videos open in full screen the moment they start, sideways, with the system bars hidden and the
picture using the whole screen, the strip beside the camera included (swipe in from an edge to see the bars for a
moment); Back goes back in LENTA; the screen stays on while a video plays; pressing Home while a video plays keeps it going in a small picture-in-picture window
(close that window to stop it); music and audiobooks keep playing with the screen off, the status and navigation
bars take your theme's colour, *Download original file* uses Android's downloads, artwork uploads use the
phone's file picker, and links to other sites (IMDb, Wikipedia) open in your browser.

**HTTPS:** type the https address (for example `https://192.168.1.50`, port 8443 when you leave it out). The app
accepts LENTA's own certificate for your server without installing anything on the phone; certificates from
Let's Encrypt or another authority work as in any browser. If a certificate is refused (expired, for another name)
the app says so. When you move LENTA to another port or to https in *Settings › Network*, the app follows and
remembers the new address. (Downloads over https with LENTA's own certificate need the certificate installed on
the phone, or use the http address.)

**Updating the app:** download and install the new APK the same way; it installs over the old one and keeps
your server and sign-in. To point the app at
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
libraries, search, My list. **Settings › Appearance › Pictures of movies and shows** switches back to the
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

## Streaming services

LENTA holds your own media. This holds none of it: it is a shop window onto the services you already pay
for. Tick them in *Settings › Streaming services* and Home gains a row for each — *Popular on Netflix*,
*Popular series on Max* — showing what that service is carrying **in your country**. Clicking a title
opens the service in a new tab. Nothing is played in LENTA, nothing is downloaded, and nothing of theirs
is stored beyond a title, the picture TMDB publishes, and a link.

**It needs no new account or key.** The data comes through the TMDB key you already use for everything
else, and the country comes from *Settings › Metadata › TMDB and language*. Getting that country wrong is
not a small mistake — a service carries different things in different places, and the wrong one offers
titles you cannot actually watch.

**The list of services is not ours.** It is what TMDB says is available where you are, in the order
JustWatch ranks them locally, so a household in Bulgaria is offered Nova Play and one in the United
States is offered Hulu, without LENTA knowing anything about either.

**Anything you already own is left out**, matched on TMDB number so it works whatever your files are
called. Being sent to Netflix for a film sitting on your own disk is the one thing a media server should
never do. You can switch this off if you would rather see everything.

**The rows say *Popular on*, not *Top 10*.** TMDB knows its own popularity across its whole catalogue,
which LENTA narrows to that service and your country. The services' own published charts are not
something TMDB can see, so LENTA does not print a position it would be making up.

**The rows can be narrowed to the genres you watch.** Fifteen of them in *Settings › Streaming
services* — tick Action, Horror, Science Fiction or whichever, and the rows show those rather than
whatever is most popular overall; tick none and nothing is narrowed. The numbers behind them are fetched
from TMDB so they match what it holds. TMDB files series differently from films: it keeps one *Sci-Fi &
Fantasy* where films have two, one *Action & Adventure* likewise, and no Horror, Romance or Thriller for
series at all. Those three are marked *films only* — picking one narrows your films and leaves your
series as they were.

**Languages you would rather not see can be left out.** What a service carries where you live is not all
for you — not bad, just not what you watch. *Settings › Streaming services* lists the languages that
actually turned up in your rows, with how many titles of each, and you tick the ones to drop. The list is
built from what you were shown rather than decided in advance, so it stays useful as the rows change.
While any are ticked LENTA fetches a second page from each service, so the rows still fill up.

**The tiles are drawn like your own.** If your artwork style is wide pictures, so are these; if it is
posters, they are posters.

**The picture on a tile can be changed**, like any title you own. The pencil in the corner shows every
poster and backdrop TMDB holds for that title — click one — and takes a link or a file as well, for when
none of them is right. Wide and upright are chosen separately, since a row can be drawn either way and a
wide picture in a poster's place is cropped to a strip. What you pick is kept when the rows refresh. TMDB's choice is sometimes the wrong one, or just an ugly one. What you pick is kept
separately from the titles themselves, so it survives the nightly refresh.

These rows are ordinary Home sections: rename, reorder or hide them in *Settings › Home screen*. They are
left off televisions, where there is no second tab for them to open.

**Clicking a title opens it on the service.** Netflix addresses a film by a number —
`netflix.com/watch/81757047` — and where that number can be found, the card uses it: one click and the
film is playing, with a series opening on its own page so you choose the episode. Netflix does not
publish those numbers and TMDB does not carry them; **Wikidata** does, as a property volunteers fill in,
and LENTA looks it up once per title and keeps it. Coverage is good for well-known titles and thin
elsewhere, so anything without a number opens the service's search for its exact name instead — still
inside Netflix, on the right result, never on a page about the film somewhere else.

**The data is JustWatch's**, supplied through TMDB on the condition that it is credited to them. That
credit is on the *Settings › Streaming services* page and in `THIRD-PARTY-NOTICES.md`. It is a condition
of being allowed the data at all, so keep it somewhere a person can find it.

## Looks: seven dark themes

Each person picks a theme in **Settings** (rail › Settings). The administrator sets the default for the
sign-in screen and new users in Settings › General.

| Theme | Feel |
|---|---|
| Projector | Plum night and an amber lamp, condensed marquee titles (default) |
| Noir | Graphite and silver, no colour, tight modern titles, sharp corners |
| Abyss | Deep water with one thread of cyan, thin wide titles, soft corners |
| Velvet | Oxblood curtains and crimson, classic picture-palace titles |
| Aurora | Midnight indigo with a violet-to-cyan glow, bold titles, round corners |
| Netflix | The streaming-service look: near-black and signal red, the menu as a bar along the top, a full-width billboard, rows of wide pictures without names under them, white Play and grey More info buttons |
| HBO Max | The look of Warner's service: a pure black page with a dark indigo glow, light (not bold) lettering, one blue accent, 8px corners and no shadows, and the menu as a bar along the top |

The **HBO Max** theme puts the menu along the top, like the Netflix theme: the page shows through it until you
scroll and then it turns black, and the page you are on is white with a blue line under it. The billboard fades
into black with a blue beam behind the title, pictures have 8px corners and a thin white edge when you point at
them, and *Recently added* is a quiet outlined label in the corner instead of a red flag. On phones the bar at
the bottom is flat black with a blue mark on the page you are on.

On a phone held upright, Netflix looks like the Netflix app: a header with the page name and your libraries as chips, a framed poster billboard with **Play** and **My List**, posters in every row, *Continue Watching for <you>* with a play circle on each poster (hold a poster for its menu), and a floating tab bar (Home, Libraries, Search, My LENTA). My List and Settings are under My LENTA. While a video plays full screen, spread two fingers to fill the screen (the edges are cut off) and pinch them to see the whole picture again.

Media pages show the backdrop full screen. It drifts slowly, fades and blurs as you scroll, and the
details sit on translucent panels. On a television none of that moving and blurring happens: a TV has a
fraction of the graphics power and needs all of it for the picture, so the backdrop is simply dimmed.

**The server's default theme** is used on the sign-in screen ("Who's watching?") and for users who haven't
picked a theme. An administrator sets it in Settings › Appearance (pick a theme, then *Use … on the sign-in screen*)
or in Settings › General › Default theme. With Netflix the sign-in screen has the streaming-service
look: a deep red background, the silver LENTA logo in the corner and large square profiles.

## Settings

Everything is under one **Settings** button (the gear at the bottom of the side menu, or at the top right with
the Netflix theme). The page has a menu on the left, like Jellyfin's: group headings, items, and items with an
arrow that open more. Everyone sees **My settings**; administrators also see the server pages. On a phone the
menu folds into a button at the top that shows where you are. Settings remembers the last page you opened.

| Group | Page | What's there |
|---|---|---|
| **My settings** | Appearance | Theme, side menu, picture styles, hover trailers |
| | Home screen | Banner trailer, which titles it picks, Home sections |
| | Playback › Audio and subtitles · TV show intros · Media pages | Languages and subtitle mode; Skip intro; trailers and theme music on media pages |
| | LENTA app · Account | Installing the app and the TV app; your password |
| **Server** | Dashboard | Playing now, library size, scans, transcoding, storage, server |
| | General | Server name, default theme, profile pictures on the sign-in screen |
| | Users · Network | Accounts and library access; HTTP and HTTPS ([HTTPS guide](INSTALL.md#https)) |
| **Libraries** | Libraries › Manage libraries · Scanning | Add and edit libraries; scan schedule and watching folders for new media |
| | Metadata › Fix matches · TMDB and language · Artwork sources · About panel extras | Titles without metadata; TMDB key and language; where pictures come from; IMDb, Wikipedia, OMDb |
| **Media** | Transcoding · Trailers and theme music · Subtitles · Previews and intros | Hardware conversion; trailer sources and theme music; OpenSubtitles; seek-bar previews and intro detection |
| **Advanced** | Logs | The server log |

My settings keep your unsaved changes while you move between their pages, and one **Save** saves them
all. On the administrator pages, **Save** saves that page. **Save** sits at the top right of every settings page and, as soon as something is changed, also at the bottom right together with **Discard**, which puts the page back as it was. **Reorder** appears only on pages with more than one block. Old bookmarks to *Server admin* pages open the
matching Settings page.

### Arranging the blocks of a page

Every Settings page made of blocks (all of them except Users, Manage libraries, Fix matches and Logs) can be
arranged with **Reorder**, at the top right; each page keeps its own arrangement. The Dashboard keeps refreshing
its figures without moving or opening your blocks, and pauses while you reorder. The layout is free, not a table:

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
  whether it is open or folded when the page opens; *Save* keeps that too.

Every change is recorded in the background with the layout (not shown on the page). **Default** puts back
the original layout at any time. Positions are
kept as parts of the page width, so a layout fits any window; phones always show the blocks in one column,
in the same order. Settings themselves are not affected. On old TV browsers the page keeps the plain layout.

## Subtitles

**Preferences** (Settings › Playback › Audio and subtitles, per person): automatically select tracks, preferred
audio language, subtitle mode (off / only forced / always / when the audio isn't in my language),
preferred subtitle language, and *download missing subtitles automatically when I press Play*.

**Online search** uses OpenSubtitles.com:

1. Create a free account at opensubtitles.com, then *API consumers* › create one to get an **API key**.
2. Settings › Subtitles: paste the key, add your username and password
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

### Trailers in the Home banner: tick them per title

> [!IMPORTANT]
> **No title plays its trailer in the Home banner until you allow it.** Out of the box the box below is unticked
> for every title, so the banner shows only pictures. This is on purpose: you decide which trailers greet everyone
> on Home (no wrong matches, no spoilers).

To let a title play its trailer in the banner (administrator only):

1. Open the title's **Edit** dialog: the pencil on its picture (hover it), the **⋯** menu on its page ›
   **Edit metadata…**, or **Settings › Metadata › Fix matches** › pencil.
2. On the **General** tab, scroll to the bottom and tick **Show the trailer in the Home banner**.
3. Press **Save Changes**.

<p align="center"><img src="images/edit-banner-trailer.jpg" alt="Edit dialog, General tab: Show the trailer in the Home banner" width="640"></p>

A banner trailer then plays when all of these are true:

- the title has the box ticked (above), and a trailer was found for it (its media page shows a Trailer);
- **Settings › Home screen › Trailer in the big banner** is *muted* or *with sound*, not *Off* (each person sets this);
- the title's library is ticked under **Settings › Home screen › Pick titles from**.

Titles with the box ticked are picked for the banner first; the others follow, showing their picture only.
**30 seconds limit** (Settings › Home screen) stops banner trailers after 30 seconds of playing; the picture and
description come back with a Replay button.

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
appropriate audiences" card. **Settings › Playback › Media pages › Start trailers after** (3, 5 or 8 seconds) starts every
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


Each person chooses in Settings › Playback › Media pages:

- **Trailers:** off, a Trailer button, or playing in the background of the media page after a
  few seconds (Netflix style), either muted or with sound, with sound and stop controls. With sound,
  theme music steps aside while the trailer plays. Browsers only allow sound after you have clicked
  something on the page, so a media page opened straight from a bookmark starts the trailer muted. Where they come from is described above.
- **Home banner trailer** (Settings › Home screen, a separate setting): the featured title at the top
  of Home plays its trailer after a few seconds — off, muted, or with sound — with a sound button.
  Only titles whose **Show the trailer in the Home banner** box is ticked play one
  (see [Trailers in the Home banner](#trailers-in-the-home-banner-tick-them-per-title)).
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
  Press *Save* to keep it. A section that appears later, such as a new library or genre, is listed
  too and starts next to the section it follows by default. *Default order* puts the order back.
- **Theme music:** plays softly when you open a movie or show and fades out when you leave or press Play.
  Sources, in order:
  1. a file next to the media: `theme.mp3` (or .m4a .flac .ogg .opus .wav) in the movie folder or the
     show's top folder, or the first file in a `theme-music/` folder. This is the Plex/Jellyfin convention;
  2. if *Look up soundtrack previews online* is on (Settings › Trailers and theme music), the 30-second Apple Music
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
(Settings › Appearance › **Trailer in hover previews**: off, muted or with sound). While it plays, the Home banner's
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

**Player bar.** The **⌄** button at the top left of the player shrinks the video into the player bar at the
bottom of the screen, like Plex; the video keeps playing in the bar's picture while you browse LENTA. While something plays in the
bar, the Home banner's trailer and the trailers in hover previews still play, but muted (pause the bar and their
sound comes back); hover previews keep working. In **Continue watching**, clicking the picture of a hover preview
carries on with that title from where you stopped, replacing what plays in the bar. Music and audiobooks play in the same bar, which looks and
works the same for everything:

- **along the top**, the time bar across the whole width: click or drag to move; pointing at it shows the time
  (and, for videos, a preview picture);
- **left**, the picture, the title, the episode / artist / chapter and the time (`19:09 / 42:58`). Click the
  picture to go back to the full player (a video) or to open the album or book; click the title to open its page;
- **centre**, previous, back 10 s (15 s for books), play/pause, forward 30 s, next (next episode, song or chapter)
  and stop;
- **right**, **⋮** (more: full screen, the title's page, playback settings, play queue, speed…), repeat and
  shuffle (music), settings, the play queue or chapters, the speaker (mute) and the volume — the thin upright bar
  at the far right: click or drag it, or turn the mouse wheel over it.

**Playback settings** (the settings button while a video plays) opens one panel with a line for each setting:
quality, playback speed, audio stream, subtitles, subtitle colour, position and size, subtitle offset (timing) and
**Auto play** (the next episode starts by itself). Click an orange value to change it. **Play queue** (the list
button, for TV episodes) shows the episode playing and the ones after it; click one to play it.

Buttons that don't apply are greyed out, so the bar keeps the same layout. The full-screen player uses the
same bar as its controls (shown when you move the mouse or tap), with a slim band on top: **⌄** to minimise on
the left, playback info and full screen on the right. One thing plays at a time: starting
music or a book closes a minimised video, and a video pauses the music. The next episode starts in the bar too.
On phones the bar sits above the tab bar with the picture, the title, play/pause, stop and **⋮**.
Clicking the title at the top of the player opens the title's page (a show's page at that episode) the same way:
the video goes on in the player bar.

**Skip intro.** LENTA finds the intro of every TV episode by itself: episodes of a season share their theme
tune, so it compares the sound of the first minutes of each episode with the others (an audio fingerprint, like
Jellyfin's Intro Skipper). The stretch they share, 15 seconds to 2½ minutes long, is the intro. While it plays the
player shows **Skip intro**. In **Settings › Playback › TV show intros** each person chooses: show the button (default), skip
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
