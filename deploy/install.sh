#!/usr/bin/env bash
# LENTA installer / updater for Ubuntu 24.04 and 26.04 LTS.
# Run from the unpacked lenta folder:   sudo ./deploy/install.sh
# Running it again updates the code and keeps your database, settings and artwork.
#
# GPU and video drivers (NVIDIA, Intel, AMD, Mesa) are never installed, removed or changed.
# LENTA assumes the drivers you use are already in place and only uses what it finds.
#
# FFmpeg comes from a self-contained static build placed in /opt/lenta/ffmpeg (no system
# packages, so nothing driver-related is pulled in through apt). It loads your existing
# NVIDIA / VA-API / Quick Sync drivers at runtime.
#   sudo FFMPEG_SOURCE=system ./deploy/install.sh   use the ffmpeg already on this machine instead
#   sudo FFMPEG_UPDATE=1 ./deploy/install.sh        download the static build again
#
# Service account (the non-root user the server runs as):
#   sudo SERVICE_USER=lenta ./deploy/install.sh   any name; created as a no-login system user
#                                                   if it doesn't exist, used as-is if it does
#   Default: the account chosen on the previous install, otherwise "lenta".
#
# Data folder (database, artwork, downloaded subtitles, caches, transcoding scratch space):
#   sudo DATA_DIR=/srv/lenta ./deploy/install.sh   any absolute path, e.g. a folder on another disk
#   Default: the folder used by the previous install, otherwise /var/lib/lenta.
#   Giving a different DATA_DIR on an update moves the existing data there (the old folder is kept
#   as a backup). To move it later without reinstalling:  sudo /opt/lenta/deploy/set-data-dir.sh /new/path
set -euo pipefail

PREFIX=/opt/lenta
CONF=/etc/lenta
PORT="${LENTA_PORT:-8484}"
FFMPEG_MODE="${FFMPEG_SOURCE:-bundled}"
FFMPEG_URL="${FFMPEG_URL:-https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.1-latest-linux64-gpl-8.1.tar.xz}"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_CONF="$CONF/install.conf"

if [[ $EUID -ne 0 ]]; then
  echo "Run this with sudo: sudo $0" >&2
  exit 1
fi

# shellcheck source=datadir.sh
source "$SRC/deploy/datadir.sh"
REQUESTED_DATA="${DATA_DIR:-}"
OLD_DATA_DIR="$(current_data_dir)"          # the folder in use now (empty on a first install)
DATA="$(check_data_dir "${REQUESTED_DATA:-${OLD_DATA_DIR:-$DEFAULT_DATA}}")" || exit 1

# ---- one-time migration from SAVEK (this project's former name) ------------------------
# Moves data, settings and the downloaded FFmpeg; watch history, users and artwork are kept.
OLD_PREFIX=/opt/savek OLD_DATA=/var/lib/savek OLD_CONF=/etc/savek
if [[ -d $OLD_DATA || -d $OLD_CONF || -f /etc/systemd/system/savek.service ]]; then
  echo "==> Migrating the existing SAVEK install to LENTA"
  systemctl disable --now savek >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/savek.service
  systemctl daemon-reload || true
  if [[ -d $OLD_DATA && ! -e $DATA/lenta.db ]]; then
    mkdir -p "$(dirname "$DATA")"
    if [[ -d $DATA ]]; then rmdir "$DATA" 2>/dev/null || { echo "$DATA is not empty; move $OLD_DATA there yourself." >&2; exit 1; }; fi
    mv "$OLD_DATA" "$DATA"
    for ext in "" -wal -shm; do
      if [[ -f "$DATA/savek.db$ext" ]]; then mv "$DATA/savek.db$ext" "$DATA/lenta.db$ext"; fi
    done
  fi
  if [[ -d $OLD_CONF && ! -e $CONF ]]; then
    mv "$OLD_CONF" "$CONF"
    if [[ -f "$CONF/savek.env" ]]; then
      sed -e 's/^SAVEK_/LENTA_/' -e "s|/var/lib/savek|$DATA|g" -e 's|/opt/savek|/opt/lenta|g' \
          -e 's/SAVEK/LENTA/g' -e 's/restart savek/restart lenta/' "$CONF/savek.env" > "$CONF/lenta.env"
      rm -f "$CONF/savek.env"
      OLD_DATA_DIR="$(current_data_dir)"
    fi
  fi
  if [[ -x $OLD_PREFIX/ffmpeg/ffmpeg && ! -e $PREFIX/ffmpeg ]]; then
    mkdir -p "$PREFIX"
    cp -a "$OLD_PREFIX/ffmpeg" "$PREFIX/ffmpeg"
  fi
  rm -rf "$OLD_PREFIX"
fi

PREV_USER=""
CREATED_USERS=""   # every account this installer has created, so uninstall can remove them
if [[ -f "$INSTALL_CONF" ]]; then
  PREV_USER="$(sed -n 's/^SERVICE_USER=//p' "$INSTALL_CONF")"
  CREATED_USERS="$(sed -n 's/^CREATED_USERS=//p' "$INSTALL_CONF")"
fi
SERVICE_USER="${SERVICE_USER:-${PREV_USER:-lenta}}"

if [[ ! "$SERVICE_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]; then
  echo "SERVICE_USER '$SERVICE_USER' is not a valid Linux user name." >&2
  exit 1
fi
if [[ "$SERVICE_USER" == "root" ]] || { id "$SERVICE_USER" >/dev/null 2>&1 && [[ "$(id -u "$SERVICE_USER")" -eq 0 ]]; }; then
  echo "LENTA must not run as root. Choose another SERVICE_USER." >&2
  exit 1
fi
if [[ "$(uname -m)" != "x86_64" && "$FFMPEG_MODE" == "bundled" ]]; then
  echo "The bundled FFmpeg is for x86_64. Install ffmpeg yourself and run: sudo FFMPEG_SOURCE=system $0" >&2
  exit 1
fi

echo "==> Checking system packages (Python and download tools only)"
PKGS=(python3 python3-venv rsync curl ca-certificates xz-utils)
MISSING=()
for p in "${PKGS[@]}"; do dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed" || MISSING+=("$p"); done
if [[ ${#MISSING[@]} -gt 0 ]]; then
  echo "    installing: ${MISSING[*]}"
  if fuser /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock >/dev/null 2>&1; then
    echo "    (waiting for another package installation or automatic update to finish...)"
  fi
  # never ask questions (needrestart), wait at most 10 minutes for another apt to finish
  export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a NEEDRESTART_SUSPEND=1
  apt-get -o DPkg::Lock::Timeout=600 update -qq || echo "    (package lists could not all be refreshed; continuing)"
  apt-get -o DPkg::Lock::Timeout=600 install -y -qq --no-install-recommends "${MISSING[@]}" >/dev/null
else
  echo "    all there"
fi

if id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "==> Running as existing user '$SERVICE_USER'"
else
  echo "==> Creating service user '$SERVICE_USER' (system account, no login)"
  useradd --system --user-group --home-dir "$DATA" --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
  CREATED_USERS="${CREATED_USERS:+$CREATED_USERS }$SERVICE_USER"
fi
SERVICE_GROUP="$(id -gn "$SERVICE_USER")"
# GPU device access (/dev/dri, /dev/nvidia*) through the groups your drivers already set up.
for g in video render; do
  if getent group "$g" >/dev/null; then usermod -aG "$g" "$SERVICE_USER"; fi
done

echo "==> Copying LENTA to $PREFIX"
mkdir -p "$PREFIX" "$CONF"
rsync -a --delete --exclude '__pycache__' "$SRC/server/" "$PREFIX/server/"
rsync -a --delete "$SRC/web/" "$PREFIX/web/"
rsync -a --delete "$SRC/deploy/" "$PREFIX/deploy/"
chmod 755 "$PREFIX"/deploy/*.sh
cp "$SRC/README.md" "$PREFIX/README.md"
if [[ -d "$SRC/docs" ]]; then rsync -a --delete "$SRC/docs/" "$PREFIX/docs/"; fi
# Samsung TV app: its files, a ready package and its installer, downloadable from the server
if [[ -d "$SRC/tizen" ]]; then
  rsync -a --delete "$SRC/tizen/" "$PREFIX/tizen/"
  mkdir -p "$PREFIX/web/download"
  python3 - "$PREFIX/tizen" "$PREFIX/web/download/lenta-tizen.wgt" <<'PY'
import os, sys, zipfile
src, out = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for root, _, files in os.walk(src):
        for f in files:
            p = os.path.join(root, f)
            z.write(p, os.path.relpath(p, src))
PY
  install -m 644 "$SRC/deploy/install-tv.sh" "$PREFIX/web/download/install-tv.sh"
fi

if [[ "$FFMPEG_MODE" == "system" ]]; then
  FFMPEG_BIN="$(command -v ffmpeg || true)"
  FFPROBE_BIN="$(command -v ffprobe || true)"
  if [[ -z "$FFMPEG_BIN" || -z "$FFPROBE_BIN" ]]; then
    echo "FFMPEG_SOURCE=system was set but ffmpeg/ffprobe are not installed." >&2
    exit 1
  fi
  echo "==> Using the system FFmpeg: $FFMPEG_BIN"
else
  FFMPEG_BIN="$PREFIX/ffmpeg/ffmpeg"
  FFPROBE_BIN="$PREFIX/ffmpeg/ffprobe"
  if [[ ! -x "$FFMPEG_BIN" || "${FFMPEG_UPDATE:-0}" == "1" ]]; then
    echo "==> Downloading static FFmpeg build"
    TMP="$(mktemp -d)"
    trap 'rm -rf "$TMP"' EXIT
    curl -fsSL --retry 3 -o "$TMP/ffmpeg.tar.xz" "$FFMPEG_URL"
    tar xJf "$TMP/ffmpeg.tar.xz" -C "$TMP"
    mkdir -p "$PREFIX/ffmpeg"
    install -m 755 "$TMP"/ffmpeg-*/bin/ffmpeg "$TMP"/ffmpeg-*/bin/ffprobe "$PREFIX/ffmpeg/"
    install -m 644 "$TMP"/ffmpeg-*/LICENSE.txt "$PREFIX/ffmpeg/LICENSE.txt"
  fi
  echo "==> FFmpeg: $("$FFMPEG_BIN" -version | head -1 | cut -d' ' -f1-3)"
fi

echo "==> Installing Python dependencies"
if [[ ! -x "$PREFIX/venv/bin/python" ]]; then
  python3 -m venv "$PREFIX/venv"
fi
"$PREFIX/venv/bin/pip" install -q --upgrade pip
"$PREFIX/venv/bin/pip" install -q -r "$PREFIX/server/requirements.txt"

if [[ ! -f "$CONF/lenta.env" ]]; then
  cat > "$CONF/lenta.env" <<EOF
# LENTA Media Server configuration. Restart after changes: sudo systemctl restart lenta
LENTA_PORT=$PORT
LENTA_HOST=0.0.0.0
LENTA_DATA_DIR=$DATA
LENTA_WEB_DIR=$PREFIX/web
LENTA_FFMPEG=$FFMPEG_BIN
LENTA_FFPROBE=$FFPROBE_BIN
# Put transcoding scratch space on a fast disk (SSD or tmpfs) if you like:
# LENTA_TRANSCODE_DIR=/mnt/fast-ssd/lenta-transcode
EOF
else
  # Keep the user's settings, but point FFmpeg at the binaries chosen on this run.
  sed -i "/^LENTA_FFMPEG=/d; /^LENTA_FFPROBE=/d" "$CONF/lenta.env"
  printf 'LENTA_FFMPEG=%s\nLENTA_FFPROBE=%s\n' "$FFMPEG_BIN" "$FFPROBE_BIN" >> "$CONF/lenta.env"
fi

# A different data folder than last time: move the existing data across.
if [[ -n "$OLD_DATA_DIR" && "$(realpath -m "$OLD_DATA_DIR")" != "$DATA" ]]; then
  echo "==> Moving the data folder from $OLD_DATA_DIR to $DATA"
  if [[ "$DATA" == "$(realpath -m "$OLD_DATA_DIR")"/* || "$(realpath -m "$OLD_DATA_DIR")" == "$DATA"/* ]]; then
    echo "The new data folder can't be inside the old one (or the other way round)." >&2; exit 1
  fi
  systemctl stop lenta 2>/dev/null || true
  move_data_dir "$OLD_DATA_DIR" "$DATA"
  set_data_dir_in_env "$DATA" "$(realpath -m "$OLD_DATA_DIR")"
fi
set_data_dir_in_env "$DATA" ""
for u in $CREATED_USERS; do
  if [[ "$u" == "$SERVICE_USER" ]]; then usermod -d "$DATA" "$u" 2>/dev/null || true; fi
done

cat > "$INSTALL_CONF" <<CONF_EOF
# Written by deploy/install.sh. Read by later updates and by uninstall.sh.
SERVICE_USER=$SERVICE_USER
CREATED_USERS=$CREATED_USERS
CONF_EOF
prepare_data_dir "$DATA" || exit 1
render_unit "$SRC/deploy/lenta.service" "$DATA"
systemctl daemon-reload
systemctl enable lenta >/dev/null
systemctl restart lenta

if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  echo "==> Opening port $PORT in the firewall"
  ufw allow "$PORT/tcp" >/dev/null
fi

sleep 3
PORT_NOW="$(grep -E '^LENTA_PORT=' "$CONF/lenta.env" | cut -d= -f2)"
IP="$(hostname -I | awk '{print $1}')"
if systemctl is-active --quiet lenta; then
  echo
  echo "LENTA is running as user '$SERVICE_USER', with its data in $DATA."
  echo "LENTA is ready.  Open  http://$IP:${PORT_NOW:-8484}  in a browser to finish setup."
  echo "Give '$SERVICE_USER' read access to your media, e.g.:  sudo setfacl -R -m u:$SERVICE_USER:rX -m d:u:$SERVICE_USER:rX /mnt/media"
  ENC="$(journalctl -u lenta -n 50 --no-pager 2>/dev/null | grep -o 'Transcoding with .*' | tail -1 || true)"
  if [[ -n "$ENC" ]]; then echo "$ENC"; fi
else
  echo "LENTA did not start. See: sudo journalctl -u lenta -n 50" >&2
  exit 1
fi
