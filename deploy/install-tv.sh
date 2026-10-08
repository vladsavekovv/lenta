#!/usr/bin/env bash
# Installs the LENTA app on a Samsung TV (Tizen, 2017 or newer) over your home network.
#
#   sudo ./deploy/install-tv.sh 192.168.1.50                 # the TV's IP address
#   sudo ./deploy/install-tv.sh 192.168.1.50 --server http://192.168.1.20:8484
#   sudo ./deploy/install-tv.sh 192.168.1.50 --samsung-cert /path/to/folder   # TVs that want a Samsung certificate
#
# Or from any Linux computer on the network, straight from your LENTA server:
#   curl -fsSL http://<lenta-server>:8484/download/install-tv.sh | sudo bash -s -- <tv-ip>
#
# Before running it, switch the TV to Developer Mode with this computer's IP address as "Host PC IP"
# (see docs/SAMSUNG-TV.md). Samsung's tools come in a Docker container (vitalets/tizen-webos-sdk), so nothing
# from Samsung is installed on this computer; Docker itself is installed if it is missing.
set -euo pipefail

IMAGE="vitalets/tizen-webos-sdk"
APP_ID="LentaTV001.LENTA"
TV="" ; SERVER="" ; CERTS=""
SELF="./deploy/install-tv.sh"; [[ -f "${BASH_SOURCE[0]:-}" ]] && SELF="$0"

usage() { echo "Usage: sudo $SELF <tv-ip> [--server http://<lenta-server>:8484] [--samsung-cert <folder>]"; exit "${1:-0}"; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --server) SERVER="${2:-}"; shift 2 ;;
    --samsung-cert) CERTS="${2:-}"; shift 2 ;;
    -h|--help) usage 0 ;;
    -*) echo "Unknown option: $1"; usage 1 ;;
    *) TV="$1"; shift ;;
  esac
done
[[ -n "$TV" ]] || { echo "Which TV? Give its IP address, e.g.:  sudo $SELF 192.168.1.50"; exit 1; }
TV="${TV%:26101}"
[[ $EUID -eq 0 ]] || { echo "Please run with sudo (Docker needs it)."; exit 1; }

say()  { printf '\n\033[1;33m==>\033[0m %s\n' "$*"; }
fail() { printf '\n\033[1;31mProblem:\033[0m %s\n' "$*"; exit 1; }

# this computer's address on the way to the TV (the TV's "Host PC IP")
HOSTIP="$(ip -4 route get "$TV" 2>/dev/null | grep -oP 'src \K[0-9.]+' || true)"
[[ -n "$HOSTIP" ]] || HOSTIP="$(hostname -I 2>/dev/null | awk '{print $1}')"

# ---- the LENTA server the app should open ------------------------------------------------------------------
if [[ -z "$SERVER" ]]; then
  PORT="8484"
  if [[ -f /etc/lenta/lenta.env ]]; then PORT="$(grep -oP '^LENTA_PORT=\K[0-9]+' /etc/lenta/lenta.env 2>/dev/null || echo 8484)"; fi
  SERVER="http://$HOSTIP:$PORT"
fi
SERVER="${SERVER%/}"
[[ "$SERVER" =~ ^https?:// ]] || SERVER="http://$SERVER"
say "The TV app will open LENTA at $SERVER"
if ! curl -fsS --max-time 5 "$SERVER/api/server" >/dev/null 2>&1; then
  echo "    (Warning: $SERVER does not answer from here. If that is the wrong address, run again with --server http://<address>:8484)"
fi

# ---- the app package ---------------------------------------------------------------------------------------
WORK="$(mktemp -d /tmp/lenta-tv.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT
HERE=/nonexistent
[[ -f "${BASH_SOURCE[0]:-}" ]] && HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC=""
for d in "$HERE/../tizen" /opt/lenta/tizen; do [[ -f "$d/config.xml" ]] && { SRC="$(cd "$d" && pwd)"; break; }; done
mkdir -p "$WORK/app"
if [[ -n "$SRC" ]]; then
  cp -a "$SRC/." "$WORK/app/"
else
  say "Fetching the app from $SERVER"
  curl -fsSL "$SERVER/download/lenta-tizen.wgt" -o "$WORK/src.wgt" || fail "Couldn't download $SERVER/download/lenta-tizen.wgt"
  python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$WORK/src.wgt" "$WORK/app"
  rm -f "$WORK/app/"*signature*.xml
fi
printf "// The LENTA server this app opens first (set by install-tv.sh). Can be changed on the TV.\nwindow.LENTA_DEFAULT_SERVER = '%s';\n" "$SERVER" > "$WORK/app/server.js"

if [[ -n "$CERTS" ]]; then
  [[ -f "$CERTS/author.p12" && -f "$CERTS/distributor.p12" ]] || fail "$CERTS must contain author.p12 and distributor.p12 (from Tizen Studio's Certificate Manager)."
  if [[ -z "${CERT_PASSWORD:-}" ]]; then read -rsp "Password of the Samsung certificate: " CERT_PASSWORD </dev/tty; echo; fi
  mkdir -p "$WORK/certs"; cp "$CERTS/author.p12" "$CERTS/distributor.p12" "$WORK/certs/"
fi
chmod -R a+rwX "$WORK"

# ---- Docker -----------------------------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  say "Installing Docker (to run Samsung's TV tools in a container)"
  if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io >/dev/null
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y -q docker
  else
    fail "Please install Docker first, then run this again."
  fi
  systemctl enable --now docker >/dev/null 2>&1 || true
fi
docker info >/dev/null 2>&1 || systemctl start docker >/dev/null 2>&1 || true      # stopped after an earlier install
docker info >/dev/null 2>&1 || fail "Docker is installed but not running (try: sudo systemctl start docker)."

if docker image inspect "$IMAGE" >/dev/null 2>&1; then
  say "Samsung's TV tools are already here"
else
  say "Downloading Samsung's TV tools (about 1-2 GB, only the first time; this can take several minutes)"
  docker pull "$IMAGE" || fail "Couldn't download the $IMAGE image. Check the server's internet connection and run this again."
fi

# ---- sign and install inside the container ----------------------------------------------------------------
say "Connecting to the TV at $TV (its Developer Mode Host PC IP must be $HOSTIP), signing and installing LENTA"
set +e
docker run -i --rm --network host -v "$WORK:/work" -e TV="$TV" -e APP_ID="$APP_ID" -e CERT_PASSWORD="${CERT_PASSWORD:-}" \
  "$IMAGE" bash -s 2>&1 <<'IN' | tee "$WORK/log"
set -e
sdb connect "$TV:26101" >/tmp/c.txt 2>&1 || true
cat /tmp/c.txt
if ! sdb devices | grep -q "$TV:26101"; then
  echo "LENTA-ERR:connect"; exit 3
fi
mkdir -p /tmp/out
if [ -f /work/certs/author.p12 ]; then
  # the Samsung certificate made for this TV
  tizen security-profiles add -n lenta -a /work/certs/author.p12 -p "$CERT_PASSWORD" \
    -d /work/certs/distributor.p12 -dp "$CERT_PASSWORD" >/dev/null
  tizen package -t wgt -s lenta -o /tmp/out -- /work/app
else
  # the developer certificate that comes with the tools
  tizen package -t wgt -o /tmp/out -- /work/app
fi
WGT="$(ls /tmp/out/*.wgt 2>/dev/null | head -1)"
[ -n "$WGT" ] || { echo "LENTA-ERR:package"; exit 5; }
OUT="$(tizen install -s "$TV:26101" --name "$(basename "$WGT")" -- /tmp/out 2>&1)"; echo "$OUT"
if echo "$OUT" | grep -qiE "fail|error"; then
  exit 4
fi
tizen run -s "$TV:26101" -p "$APP_ID" >/dev/null 2>&1 || true
sdb disconnect "$TV:26101" >/dev/null 2>&1 || true
echo "LENTA-OK"
IN
RC=$?
set -e
[[ $RC -eq 0 ]] && ! grep -q "LENTA-OK" "$WORK/log" && RC=9

case $RC in
  0) say "Done! LENTA is installed on the TV and should be opening now."
     echo "    You'll find it under Apps on the TV from now on. You can switch Developer Mode off again if you like."
     echo "    (The app keeps working; to update it later just run this command again.)" ;;
  3) fail "The TV at $TV did not let this computer in. Check that:
     - the TV is on and its IP address is $TV,
     - Developer Mode is ON with Host PC IP = $HOSTIP (this computer),
     - you restarted the TV after switching Developer Mode on (hold the power button until it restarts)." ;;
  4) fail "The TV refused the app. Newer Samsung TVs (2023 onwards) only accept apps signed with a free Samsung
     certificate made for that TV. See 'Samsung certificate' in docs/SAMSUNG-TV.md, then run:
       sudo $SELF $TV --samsung-cert /folder/with/the/p12/files" ;;
  5) fail "Signing the app failed (see the messages above). If you used --samsung-cert, check the password." ;;
  9) fail "Samsung's tools didn't report a finished install (see the messages above)." ;;
  *) fail "Installing stopped (code $RC). The messages above say where." ;;
esac
