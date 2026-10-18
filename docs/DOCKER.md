# LENTA in a container

The short way, on any machine with Docker:

```bash
mkdir lenta && cd lenta
curl -fsSLO https://raw.githubusercontent.com/vladsavekovv/lenta/main/compose.yaml
# edit the two media lines in compose.yaml to point at your own folders
docker compose up -d
```

Then open `http://<this machine>:8484` and make the first account. That account is the administrator.

Nothing else is required. The rest of this page is for the things that make it yours: your user id, your
graphics card, your TV tuner.

If you would rather not use containers at all, LENTA installs straight onto Ubuntu with
`sudo ./deploy/install.sh` — see [INSTALL.md](INSTALL.md). The two installations are the same server
with the same FFmpeg; neither is the "real" one.

---

## What goes where

| Inside the container | What it is | Give it |
|---|---|---|
| `/data` | database, artwork, downloaded subtitles, transcoding scratch space | a folder of your own, backed up |
| `/media/...` | your films, shows, music, books, photos | your media folders, **read-only** |
| port `8484` | the web interface and every client | a port on this machine |
| port `8443` | HTTPS, once you switch it on in Settings › Network | only if you want it |

Only `/data` is written to. Media folders are mounted read-only on purpose: a media server has no
business changing what it was asked to play.

The path you type when you add a library in LENTA is the path **inside** the container — `/media/films`,
not `/srv/media/films`. This catches everybody once.

## The one key you need

Posters, descriptions, cast and episode titles come from The Movie Database, and TMDB give keys to
people rather than to programs — a key is licensed to whoever made it and cannot be passed on, so
LENTA cannot ship one and neither can anybody else honestly. Making your own is free and takes about
a minute at [themoviedb.org/settings/api](https://www.themoviedb.org/settings/api).

Set it in `compose.yaml` before the first start and there is nothing to click afterwards:

```yaml
environment:
  LENTA_TMDB_KEY: "your-key-here"
```

It only ever fills an empty setting — once you have entered a key in Settings, that one is yours and
the environment never overwrites it. Leave it out entirely if you would rather paste the key in
Settings › Metadata later; everything except online metadata works without it, so your files still
scan, play and remember where you got to.

## Who it runs as

The server refuses to run as root, in a container exactly as on a host. Set `PUID` and `PGID` to the
account on your machine that owns the media:

```bash
id -u    # PUID
id -g    # PGID
```

On Synology and Unraid these are usually not 1000.

The container starts as root for one job — handing `/data` to that account — and then becomes it. The
server never runs as root; it refuses to. If you would rather root were not in the picture at all, start
the container as the account instead (`user: "1000:1000"` in `compose.yaml`) and make sure the data
folder already belongs to it; `PUID`/`PGID` are then ignored, because that line has already done their
job.

If LENTA cannot read a film or write its database, this is the first thing to check:

```bash
docker exec lenta grep ^Uid: /proc/1/status   # who the server is
ls -ln /srv/media/films       # who owns the media
```

## Hardware transcoding

LENTA installs no graphics drivers — not on a host, not here. It uses the driver the machine already
has, so all that is needed is to pass the card in.

**NVIDIA.** Install the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
on the host, then uncomment the `deploy:` block in `compose.yaml`. With plain `docker run` it is
`--gpus all`. To check it took:

```bash
docker exec lenta /opt/ffmpeg/ffmpeg -hide_banner -encoders | grep nvenc
```

Then open Settings › Transcoding: it should say **NVIDIA NVENC**, and Settings › About repeats it.

**Intel Quick Sync or AMD.** Pass the render node instead, and add the groups that own it:

```yaml
devices:
  - /dev/dri:/dev/dri
group_add:
  - "44"       # video   — getent group video render
  - "993"      # render
```

If neither is passed in, LENTA transcodes on the processor. It works; it just costs a great deal more
of the machine.

## Live TV from a tuner

A DVB-C, DVB-T or DVB-S stick plugged into this machine needs two things: the device, and membership of
the group that owns it.

```yaml
devices:
  - /dev/dvb:/dev/dvb
group_add:
  - "44"       # the group /dev/dvb/adapter0/frontend0 belongs to — usually video
```

Check the host sees it first — `ls /dev/dvb/` should list `adapter0` — then check the container does:

```bash
docker exec lenta ls -l /dev/dvb/adapter0
```

Then Settings › Live TV will find it, scan it and build the guide from the broadcast itself.

A frequency that finds a carrier and never locks is a signal problem, not a container problem. The
tuner check tells you which:

```bash
docker exec lenta python /app/deploy/tv-check.py --freq 530
```

## Updating

```bash
docker compose pull && docker compose up -d
```

Your data folder is untouched. The database is migrated on the first start of the new version, exactly
as it is on a host.

To pin a version instead of following `latest`, use `ghcr.io/vladsavekovv/lenta:1.12.0`. Tags are
published for the full version, the minor series (`:1.12`) and the major (`:1`); `:edge` is built from
every push to `main` and is for people who like surprises.

## Building it yourself

```bash
git clone https://github.com/vladsavekovv/lenta.git
cd lenta
docker compose up -d --build
```

The build downloads the same self-contained FFmpeg the bare-metal installer uses, builds the Samsung TV
package, and installs the Python dependencies into a virtual environment inside the image. It needs
nothing from your machine but Docker.

The image is `linux/amd64`. The FFmpeg build it carries is x86_64; an arm64 image is possible — the
Dockerfile already falls back to the distribution's FFmpeg there — but none has been published, because
nobody has watched anything on one yet.

## When something is wrong

```bash
docker logs lenta                 # what the server said
docker exec lenta grep ^Uid: /proc/1/status   # which account the server is running as
docker exec lenta ls -l /data     # can it write its own folder
curl -s localhost:8484/api/server # is it answering at all
```

Settings › About names the version, the machine, the FFmpeg build and whether transcoding found the
card — that page is the quickest answer to "what have I actually got running here".
