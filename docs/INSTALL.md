# Installing LENTA

This guide takes you from a fresh Ubuntu machine to watching your own movies in a browser, step by step.
It takes about 15 minutes. You don't need to know Python or Linux well, but you do need to be able to
open a terminal on the server and type commands.

> [!TIP]
> Commands that start with `sudo` need administrator rights on the server. Copy them exactly as written,
> one block at a time.

**Contents**

1. [Before you start](#1-before-you-start)
2. [Download LENTA](#2-download-lenta)
3. [Choose your options (optional)](#3-choose-your-options-optional)
4. [Run the installer](#4-run-the-installer)
5. [Let LENTA read your media](#5-let-lenta-read-your-media)
6. [Create the administrator account](#6-create-the-administrator-account)
7. [Connect to TMDB for posters and descriptions](#7-connect-to-tmdb-for-posters-and-descriptions)
8. [Add your libraries](#8-add-your-libraries)
9. [Optional extras](#9-optional-extras)
10. [Watch on phones, tablets and TVs - WORK IN PROGRESS](#10-watch-on-phones-tablets-and-tvs-work-in-progress)

Then: [GPU transcoding](#gpu-hardware-transcoding) ·
[Media on a NAS](#media-on-a-nas) ·
[Access from outside your home](#access-from-outside-your-home) ·
[Everyday commands](#everyday-commands) ·
[Updating](#updating) ·
[Backups](#backups-and-restoring) ·
[Moving the data folder](#moving-the-data-folder) ·
[Configuration file](#the-configuration-file) ·
[Uninstalling](#uninstalling) ·
[Troubleshooting](#troubleshooting) ·
[FAQ](#faq)

---

## 1. Before you start

### What you need

| | Minimum | Recommended |
|---|---|---|
| **Operating system** | Ubuntu 24.04 LTS or 26.04 LTS (server or desktop) | Ubuntu Server 26.04 LTS |
| **Processor** | 64-bit x86 (Intel / AMD), 2 cores | 4 cores or more |
| **Memory** | 2 GB | 4 GB or more |
| **Disk for LENTA's data** | 5 GB free | 20 GB+ on an SSD (artwork, caches, temporary video files while converting) |
| **Graphics card** | not needed | NVIDIA, Intel or AMD for fast video conversion ([details](#gpu-hardware-transcoding)) |
| **Network** | Internet during installation | Wired network to the server |

Your movies, shows, music and photos can be on the same machine, on another disk or on a NAS.
LENTA only **reads** them; it never changes, moves or deletes your media files.

> [!NOTE]
> **Raspberry Pi / ARM machines:** LENTA itself runs, but the installer's bundled FFmpeg is for x86 only.
> Install FFmpeg yourself first (`sudo apt install ffmpeg`) and add `FFMPEG_SOURCE=system` to the
> install command in [step 4](#4-run-the-installer).

> [!IMPORTANT]
> **Graphics drivers are your job, not the installer's.** LENTA never installs, removes or changes NVIDIA,
> Intel, AMD or Mesa drivers. If you want hardware video conversion, install and test your driver
> **before** installing LENTA (for NVIDIA, `nvidia-smi` should list your card). Without a working driver,
> LENTA simply converts video on the processor instead.

### Find out the server's address

You will need it to open LENTA from other devices. On the server run:

```bash
hostname -I
```

The first number, for example `192.168.1.50`, is the address. A fixed address is easier to live with:
reserve it in your router's DHCP settings ("static lease" / "address reservation").

### Know where your media is

Write down the folders that hold your media, for example:

```
/mnt/media/Movies
/mnt/media/TV Shows
/mnt/media/Music
```

If your media is on a NAS, set up the mount first: see [Media on a NAS](#media-on-a-nas).

---

## 2. Download LENTA

Pick **one** of the two ways.

**A. With git** (easiest to update later):

```bash
sudo apt update && sudo apt install -y git
git clone https://github.com/vladsavekovv/lenta.git
cd lenta
```

**B. As a download:** on the project's GitHub page open **Releases**, download `lenta.tar.gz`
(or **Code › Download ZIP**), copy it to the server, then:

```bash
tar xzf lenta.tar.gz        # or: unzip lenta-main.zip
cd lenta                    # or: cd lenta-main
```

You should now be inside a folder that contains `server`, `web` and `deploy`:

```bash
ls
# README.md  android  deploy  docs  server  web
```

---

## 3. Choose your options (optional)

**Most people can skip this step**: the defaults work. Each option is put in front of the install command
in the next step, for example `sudo DATA_DIR=/srv/lenta ./deploy/install.sh`.

| Option | Default | What it does |
|---|---|---|
| `DATA_DIR` | `/var/lib/lenta` | Where LENTA keeps its database, artwork, downloaded subtitles, caches and temporary video files. Pick a fast disk with free space. Any absolute path without spaces works. |
| `SERVICE_USER` | `lenta` | The Linux account LENTA runs as. It is created (with no password and no login) if it doesn't exist. It can never be `root`. |
| `LENTA_PORT` | `8484` | The network port. You'll open `http://<server-address>:<port>`. |
| `FFMPEG_SOURCE` | `bundled` | `bundled` downloads a self-contained FFmpeg into `/opt/lenta/ffmpeg`. `system` uses the `ffmpeg` already installed on the machine. |
| `FFMPEG_UPDATE` | – | `1` downloads the bundled FFmpeg again (for an update). |

The installer remembers your choices, so later updates keep them without typing them again.

---

## 4. Run the installer

From inside the `lenta` folder:

```bash
sudo ./deploy/install.sh
```

Or with options, for example:

```bash
sudo DATA_DIR=/srv/lenta LENTA_PORT=8080 ./deploy/install.sh
```

The installer:

1. installs Python and a few download tools from Ubuntu (`python3`, `python3-venv`, `rsync`, `curl`,
   `ca-certificates`, `xz-utils`), nothing else,
2. creates the service account (`lenta`) and adds it to the existing `video` and `render` groups
   so it can use your graphics card,
3. copies LENTA to `/opt/lenta` and its Python libraries into `/opt/lenta/venv`,
4. downloads FFmpeg into `/opt/lenta/ffmpeg` (about 100 MB),
5. writes the settings file `/etc/lenta/lenta.env`,
6. opens the port in the firewall if `ufw` is active,
7. starts LENTA as the `lenta` system service, which also starts at every boot.

At the end it prints the address to open, like:

```
LENTA is ready.  Open  http://192.168.1.50:8484  in a browser to finish setup.
```

> [!TIP]
> If something went wrong, the installer stops and says why. Fix it and run the same command again:
> running the installer twice is always safe.

---

## 5. Let LENTA read your media

LENTA runs as its own account (`lenta`), which can't see your files until you allow it. Give it
**read-only** access to each media folder (replace `/mnt/media` with your folder):

```bash
sudo apt install -y acl
sudo setfacl -R -m u:lenta:rX -m d:u:lenta:rX /mnt/media
```

- `-R` applies it to everything already there, `d:` to files you add later.
- `rX` means *read files and open folders*, never write.

Check that it worked; you should see your files listed:

```bash
sudo -u lenta ls "/mnt/media/Movies"
```

If you get **Permission denied**, a folder *above* your media blocks the account, typically your home
folder. Let it pass through (it still can't list or read anything else there):

```bash
sudo setfacl -m u:lenta:x /home/alice      # replace with the blocking folder
```

> [!NOTE]
> If you chose another `SERVICE_USER`, use that name instead of `lenta` in these commands.

---

## 6. Create the administrator account

On any computer on your network, open a browser and go to the address the installer printed:

```
http://192.168.1.50:8484
```

The first visit shows **Set up your media server**. Choose a server name, your user name and a password
(at least 6 characters). This first account is the **administrator**: it can add libraries, users and
change settings. Keep the password safe; if you lose it, see
[I forgot the administrator password](#i-forgot-the-administrator-password).

---

## 7. Connect to TMDB for posters and descriptions

LENTA gets posters, backgrounds, descriptions, cast and trailers from **The Movie Database (TMDB)**.
It's free for personal use:

1. Create an account at [themoviedb.org](https://www.themoviedb.org/signup) and confirm your e-mail.
2. Go to your avatar › **Settings › API** › **Create** › choose **Developer**, accept the terms and fill in
   the form (for "Application URL" anything such as `http://localhost` is fine; describe it as a
   personal media server).
3. Copy the **API Key** (or the longer **API Read Access Token**, both work).
4. In LENTA: profile › **Server admin › Settings**, paste the key in the **Metadata** panel and press **Save**.
   (Until you do, Server admin › Metadata shows *Online metadata is off*.)
5. Optionally set **Language** (for example `de-DE`, `fr-FR`, `bg-BG`) to get titles and descriptions
   in your language where TMDB has them.

Without a key LENTA still plays everything, but shows file names instead of posters.

---

## 8. Add your libraries

1. Profile › **Server admin › Libraries › Add library**.
2. Choose the type: **Movies**, **TV shows**, **Music** or **Photos**.
3. Give it a name and add one or more folders (type the path, for example `/mnt/media/Movies`).
4. Press **Save**. LENTA scans the folder straight away; posters appear as titles are matched.

The first scan of a large library can take a while (TMDB lookups for thousands of titles); you can
already browse and play while it runs. LENTA rescans on a schedule and you can start a scan from the
library's **⋯** menu at any time.

**How should files be named?** Mostly the way you already have them. LENTA understands release names,
collection folders and the Plex/Jellyfin layout. The recommended layout:

```
Movies/
  Inception (2010)/Inception (2010).mkv
TV Shows/
  Breaking Bad/Season 01/Breaking.Bad.S01E01.mkv
Music/
  Artist/Album (Year)/01 - Track.flac
Photos/
  Holiday 2024/IMG_0001.jpg
```

More in the [User Guide › Organising files](USER-GUIDE.md#organising-files). Titles LENTA couldn't match
show up in **Server admin › Metadata**, where you can fix them by hand.

---

## 9. Optional extras

All of these are free and set in **Server admin › Settings**. None is required.

| Extra | What you get | How to get the key |
|---|---|---|
| **OpenSubtitles** | Search and download subtitles from the player; automatic subtitles in your language | [opensubtitles.com](https://www.opensubtitles.com) › account › *API consumers* › create one. Paste the API key; your username and password raise the daily limit. Press **Test OpenSubtitles**. |
| **OMDb** | Rotten Tomatoes and Metacritic scores and awards in the About panel; extra posters | [omdbapi.com/apikey.aspx](https://www.omdbapi.com/apikey.aspx) › FREE. Click the activation link in the e-mail, then press **Test OMDb**. |
| **Fanart.tv** | HD posters, backgrounds and clear logos | [fanart.tv](https://fanart.tv) › sign up › *API* › request a **project API key**. Your *personal* key is optional (gives newer images faster). Press **Test**. |
| **IMDb ratings** | IMDb rating and votes on every title | Nothing to do: on by default, from IMDb's free daily file. |
| **Wikipedia** | A summary of the title in the About panel | Nothing to do: on by default. |
| **Apple TV trailers** | HD trailers that play without YouTube | Nothing to do: on by default. |
| **KinoCheck** | The official trailer of each title in 1080p or better (instead of whatever TMDB lists first) | Works without a key (1,000 lookups a day). For more, get a free key at [api.kinocheck.com](https://api.kinocheck.com), paste it in Settings › Trailers and press **Test KinoCheck**. |

> [!NOTE]
> Fanart.tv sometimes says *"Proxy or VPN detected"* even if you don't use one; this is about your
> internet provider's address, not your computer. Try signing up from your phone on mobile data.

### Trailers in the Home banner

> [!IMPORTANT]
> The big banner at the top of Home **plays no trailers until you allow them, title by title**. Until then it
> shows pictures only, which is how LENTA starts.

For each title you want: open its **Edit** dialog (the pencil on its picture, or **Server admin › Metadata** ›
pencil), tick **Show the trailer in the Home banner** at the bottom of the **General** tab and press
**Save Changes**. Details and screenshot: [User Guide › Trailers in the Home banner](USER-GUIDE.md#trailers-in-the-home-banner-tick-them-per-title).

---

## 10. Watch on phones, tablets and TVs (WORK IN PROGRESS!!)

- **Any device with a browser:** open `http://<server-address>:8484`. Phones get a layout made for touch.
- **iPhone / iPad:** in Safari, Share › **Add to Home Screen** for a full-screen app with its own icon.
- **Android app:** on the phone, open LENTA in Chrome › profile › **Settings › LENTA app › Download the
  Android app (APK)**, open the file and allow the install. Then type your server's address, e.g.
  `192.168.1.50:8484`, and tap **Connect**. Details in the
  [User Guide](USER-GUIDE.md#android-app-apk).
- **Samsung TV (2017 or newer):** open the LENTA address in the TV's Internet app, or install the LENTA TV app
  with one command: `sudo /opt/lenta/deploy/install-tv.sh <TV's IP>`. Step by step: [Samsung TV guide](SAMSUNG-TV.md).
- **Other smart TVs:** use the TV's browser (LENTA switches to remote-control mode), or cast a browser tab.

That's it, LENTA is installed. 🎬 The rest of this page is reference material for when you need it.

---

## GPU hardware transcoding

When a device can't play a file as it is (for example HEVC/4K in a browser, or picture subtitles), LENTA
converts it on the fly. A graphics card does that many times faster than the processor.

LENTA **detects** what works at start-up; it never installs drivers. What must already be on the machine:

| GPU | Must already be installed | Quick check |
|---|---|---|
| NVIDIA (NVENC) | The NVIDIA driver, including `libnvidia-encode` | `nvidia-smi` lists the card |
| Intel (Quick Sync / VA-API) | An Intel VA-API driver such as `intel-media-va-driver` | `vainfo` shows *VAEntrypointEncSlice* |
| AMD (VA-API) | Mesa's VA-API driver (`mesa-va-drivers`) | `vainfo` shows *VAEntrypointEncSlice* |

Then check in LENTA: **Server admin › Dashboard › Transcoding** shows the encoder in use, for example
**NVIDIA NVENC · h264_nvenc**. If it says *CPU (x264)*:

```bash
groups lenta                       # should include video and render
ls -l /dev/dri                     # Intel/AMD: renderD128 should exist
sudo systemctl restart lenta       # after installing or changing drivers
```

and press **Detect hardware again** in Server admin › Settings.

Good to know:

- With NVIDIA, decoding, resizing and encoding all stay on the GPU; the processor only handles audio.
- HDR video is tone-mapped to normal colours (on the processor unless your FFmpeg has `tonemap_cuda`,
  such as jellyfin-ffmpeg; point `LENTA_FFMPEG` at it in the [configuration file](#the-configuration-file)).
- Consumer GeForce cards allow a limited number of conversions at the same time (8 on current drivers).
  Set **Simultaneous conversions** in Settings to match.
- If the all-GPU path fails on a file, LENTA retries it with processor filters automatically.

---

## Media on a NAS

Mount the share on the server so it appears as a normal folder, then add that folder as a library.
Add **one** of these lines to `/etc/fstab` (`sudo nano /etc/fstab`) and create the folder first
(`sudo mkdir -p /mnt/media`).

**NFS** (Synology, QNAP, TrueNAS…), needs `sudo apt install nfs-common`:

```fstab
192.168.1.20:/volume1/media  /mnt/media  nfs  ro,_netdev,noatime,x-systemd.automount  0 0
```

**SMB / Windows share**, needs `sudo apt install cifs-utils`:

```fstab
//192.168.1.20/media  /mnt/media  cifs  ro,credentials=/etc/lenta/smb.cred,uid=lenta,iocharset=utf8,_netdev,x-systemd.automount  0 0
```

with the share's login in `/etc/lenta/smb.cred`:

```ini
username=media-reader
password=your-password
```

```bash
sudo chmod 600 /etc/lenta/smb.cred
sudo systemctl daemon-reload && sudo mount -a
sudo -u lenta ls /mnt/media        # check
```

`ro` mounts the share read-only, which is all LENTA needs. If the NAS is off during a scan, LENTA leaves the
library as it is instead of emptying it.

---

## Access from outside your home

LENTA speaks plain HTTP and is meant for your home network. **Don't** forward port 8484 on your router.
For access on the go, use one of these:

**Tailscale (easiest, free for personal use).** Install it on the server and on your phone/laptop
([tailscale.com/download](https://tailscale.com/download)), sign in on both, and open
`http://<server-tailscale-name>:8484`. For an https address (also needed for the full "Install app"
option in Chrome):

```bash
sudo tailscale serve --bg 8484
```

**WireGuard or your router's VPN** (UniFi Teleport, FRITZ!Box VPN…): connect to home, then use the
normal address.

**A reverse proxy with HTTPS** if you have a domain, for example [Caddy](https://caddyserver.com),
which gets certificates automatically:

```caddy
media.example.com {
    reverse_proxy 127.0.0.1:8484
}
```

Sign-in cookies switch to secure mode automatically when LENTA is reached over HTTPS.

---

## Everyday commands

```bash
sudo systemctl status lenta              # is it running?
sudo systemctl restart lenta             # restart (e.g. after editing /etc/lenta/lenta.env)
sudo systemctl stop lenta                # stop
sudo journalctl -u lenta -n 100          # last 100 log lines
sudo journalctl -u lenta -f              # follow the log live (Ctrl+C to quit)
sudo /opt/lenta/deploy/set-data-dir.sh   # where is the data folder?
sudo /opt/lenta/deploy/reset-password.sh # list accounts / reset a password
```

The admin **Dashboard** in LENTA shows the version, data folder, free space, active streams, transcoding
hardware and background jobs.

---

## Updating

Your database, users, watch history, settings and artwork are always kept.

**Installed with git:**

```bash
cd lenta
git pull
sudo ./deploy/install.sh
```

**Installed from a download:** download and unpack the new release, `cd` into it and run
`sudo ./deploy/install.sh`. You can delete the old unpacked folder afterwards; LENTA runs from `/opt/lenta`.

After an update, reload the page in your browsers (the LENTA app on phones updates itself).
To update FFmpeg as well: `sudo FFMPEG_UPDATE=1 ./deploy/install.sh`.

> [!TIP]
> Take a [backup](#backups-and-restoring) before big updates. It only takes a minute.

---

## Backups and restoring

Everything LENTA knows lives in the data folder (`/var/lib/lenta` unless you chose another).

| In the data folder | Back it up? |
|---|---|
| `lenta.db` (+ `lenta.db-wal`, `lenta.db-shm`) | **Yes**: users, watch history, metadata, settings, API keys |
| `images/` | **Yes**: artwork you uploaded |
| `subtitles/` | Optional: downloaded subtitles (can be downloaded again) |
| `cache/`, `transcode/`, `trickplay/`, `intros/` | No: rebuilt automatically (seek-bar pictures take a while to make again) |

**Back up** (stops LENTA for a few seconds so the database is consistent):

```bash
sudo systemctl stop lenta
sudo tar czf ~/lenta-backup-$(date +%F).tar.gz -C /var/lib/lenta lenta.db images subtitles
sudo systemctl start lenta
```

**Restore** on the same or a new machine: install LENTA as usual, then:

```bash
sudo systemctl stop lenta
sudo tar xzf ~/lenta-backup-2026-01-31.tar.gz -C /var/lib/lenta
sudo rm -f /var/lib/lenta/lenta.db-wal /var/lib/lenta/lenta.db-shm
sudo chown -R lenta: /var/lib/lenta
sudo systemctl start lenta
```

If your media is in different folders on the new machine, edit the libraries' folders in
Server admin › Libraries; watch history is kept for titles that are found again.

---

## Moving the data folder

To move LENTA's data to a bigger or faster disk at any time:

```bash
sudo /opt/lenta/deploy/set-data-dir.sh /mnt/ssd/lenta
```

The script stops LENTA, checks there is enough space, copies the data, updates the settings and starts
LENTA again. The old folder is kept, renamed `<folder>.moved-<date>`: delete it once all is well.
If the new disk is mounted at boot, make sure it is in `/etc/fstab`.

---

## The configuration file

`/etc/lenta/lenta.env` holds the few settings needed before LENTA starts. Everything else is in
**Server admin › Settings**. After editing it: `sudo systemctl restart lenta`.

| Variable | Default | Meaning |
|---|---|---|
| `LENTA_PORT` | `8484` | Network port |
| `LENTA_HOST` | `0.0.0.0` | Listen address (`127.0.0.1` = only reachable through a reverse proxy on the same machine) |
| `LENTA_DATA_DIR` | `/var/lib/lenta` | Data folder; change it with `set-data-dir.sh`, not by hand |
| `LENTA_TRANSCODE_DIR` | `<data>/transcode` | Temporary video segments while converting; an SSD helps |
| `LENTA_FFMPEG`, `LENTA_FFPROBE` | `/opt/lenta/ffmpeg/…` | FFmpeg programs; point them at another build (e.g. jellyfin-ffmpeg) if you prefer |

---

## Uninstalling

```bash
sudo /opt/lenta/deploy/uninstall.sh            # remove LENTA, keep the data folder
sudo /opt/lenta/deploy/uninstall.sh --purge    # also delete the data folder and the account it created
```

Your media files are never touched. Graphics drivers are never touched.

---

## Troubleshooting

Start with the log, it usually says exactly what is wrong:

```bash
sudo journalctl -u lenta -n 100 --no-pager
```

### The page doesn't open

1. Is LENTA running? `sudo systemctl status lenta` should say **active (running)**. If not, read the log.
2. Does it answer on the server itself? `curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8484/` should print `200`.
3. If it works on the server but not from other devices:
   - firewall: `sudo ufw status`; if active, `sudo ufw allow 8484/tcp`,
   - `LENTA_HOST` in `/etc/lenta/lenta.env` must be `0.0.0.0`,
   - you are using `http://` (not https) and the right address (`hostname -I`) and port.
4. *Address already in use* in the log: another program uses the port. Reinstall with another port:
   `sudo LENTA_PORT=8485 ./deploy/install.sh`.

### A library is empty or titles are missing

- Permissions: `sudo -u lenta ls "/path/to/library"` must list your files; if not, repeat
  [step 5](#5-let-lenta-read-your-media).
- **Server admin › Libraries** shows under each library what it couldn't read and why.
- Files named `sample`, and folders called `Extras`, `Featurettes` or `Trailers`, are skipped on purpose.
- On a NAS: is it mounted? `ls /mnt/media`.

### No posters, or wrong posters

- Is the TMDB key saved in Server admin › Settings?
- Titles LENTA couldn't match are in **Server admin › Metadata**: use **Fix match** to pick the right one.
- A wrong match on a title page: **⋯ › Fix match…**.
- The server's clock must be right (`timedatectl`); a wrong date breaks secure connections to TMDB.

### Videos buffer or stutter

- **Dashboard › Transcoding** shows each stream as *Direct play*, *Direct stream* or *Convert*. Converting
  on the processor (x264) is slow for 4K: set up the [GPU](#gpu-hardware-transcoding), or choose a lower
  quality in the player.
- Over Wi-Fi, 4K files can be more than the connection carries; try a lower quality.
- Put `LENTA_TRANSCODE_DIR` on an SSD if the data folder is on a slow disk.

### No trailer in the Home banner

The banner shows a title's picture instead of its trailer until **all** of these are true:

1. The title's **Edit › General › Show the trailer in the Home banner** box is ticked and saved. It is
   **unticked for every title** until you tick it ([how](USER-GUIDE.md#trailers-in-the-home-banner-tick-them-per-title)).
2. The title has a trailer: its media page shows a Trailer button or plays one.
3. **Settings › Home screen › Trailer in the big banner** is *muted* or *with sound*, not *Off*. Each person sets this
   for themselves.
4. The title's library is ticked under **Settings › Home screen › Pick titles from**.

If the trailer plays but silently: browsers allow sound only after you click something on the page. LENTA turns
the sound on by itself at your first click; the speaker button in the banner does it at once.

### No Apple TV trailers

- Press **Test Apple trailers** in Server admin › Settings › Trailers. It says whether the server can reach
  Apple and plays a sample.
- Apple only has trailers for **movies** (not TV shows), and not for every film. Titles without one keep
  their YouTube trailer, and your own trailer files always come first.
- The background index asks Apple slowly (about 20 searches a minute), so a large library takes a while.
  **Dashboard** shows *… from Apple* growing, and any Apple error in red.
- To pick one for a single title right away: **Edit metadata › Trailers** lists the Apple trailer, marked *Apple TV*.

### The graphics card isn't used

See [GPU hardware transcoding](#gpu-hardware-transcoding). Most often: the driver is missing its encoder part
(NVIDIA: `libnvidia-encode`), or LENTA wasn't restarted after installing the driver.

### The installer can't download FFmpeg

The server needs internet access to github.com during installation. Behind a strict firewall, install
FFmpeg from Ubuntu and use it instead:

```bash
sudo apt install -y ffmpeg
sudo FFMPEG_SOURCE=system ./deploy/install.sh
```

### I forgot the administrator password

On the server:

```bash
sudo /opt/lenta/deploy/reset-password.sh            # lists the accounts
sudo /opt/lenta/deploy/reset-password.sh alice      # sets a new password for alice
```

Add `--admin` after the name to also make that account an administrator. Other users' passwords can
also be changed by an administrator in **Server admin › Users**.

### The Android app says it can't reach the server

The phone must be on the same network (or on your VPN). Type the address with the port, e.g.
`192.168.1.50:8484`, and check the same address opens in the phone's Chrome. Then profile ›
**Change server…** in the app.

### Still stuck?

Open an [issue](https://github.com/vladsavekovv/lenta/issues) and include your Ubuntu version
(`lsb_release -d`), your LENTA version (Dashboard), your GPU, and the last 50 log lines
(`sudo journalctl -u lenta -n 50 --no-pager`). Remove anything private such as API keys first.

---

## FAQ

**Does LENTA cost anything?** No. It's in the public domain: free for everyone, for any use (see [LICENSE](../LICENSE)).
The online services it can connect to have their own terms; IMDb's ratings data, for example, is for personal,
non-commercial use only.

**Does it send my data anywhere?** No account, no tracking, no cloud. The server only contacts the
metadata services you enable (TMDB, IMDb's ratings file, Wikipedia, Apple, and the extras you add
keys for) to fetch information about your titles.

**Does it work without internet?** Yes, for playback on your home network. Metadata, trailers and
subtitle downloads need internet.

**Will it change my media files?** Never. LENTA only reads them. Downloaded subtitles and artwork go
into its own data folder.

**Windows, macOS, Docker?** The installer is for Ubuntu 24.04/26.04. Other Debian-based systems may work
but aren't tested. There is no Windows or macOS version and no official Docker image (yet).

**Can I use it alongside Plex, Jellyfin or Emby?** Yes. They can all read the same media folders; just
use a different port. LENTA reads Plex/Jellyfin-style artwork and trailer files next to your media.

**How many people can watch at once?** As many as your network allows for direct play. Conversions are
limited by your processor or graphics card; set *Simultaneous conversions* in Settings.

**Can I run it as my own user instead of `lenta`?** Yes: `sudo SERVICE_USER=alice ./deploy/install.sh`.
It just can't be `root`.
