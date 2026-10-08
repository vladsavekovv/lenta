#!/usr/bin/env bash
# Moves LENTA's data folder (database, artwork, downloaded subtitles, caches) to another place.
#   sudo /opt/lenta/deploy/set-data-dir.sh /srv/lenta
#   sudo /opt/lenta/deploy/set-data-dir.sh            shows the current data folder
#
# The service is stopped, the data copied, the configuration and service permissions updated,
# and the service started again. The old folder is kept as a backup until you delete it.
# If the new folder already holds a LENTA database (e.g. restored from a backup), it is used as is.
set -euo pipefail
CONF=/etc/lenta
PREFIX=/opt/lenta
HERE="$(cd "$(dirname "$0")" && pwd)"
[[ $EUID -eq 0 ]] || { echo "Run this with sudo: sudo $0 ${1:-/new/path}" >&2; exit 1; }
[[ -f "$CONF/lenta.env" ]] || { echo "LENTA is not installed (no $CONF/lenta.env). Use deploy/install.sh." >&2; exit 1; }

SERVICE_USER="$(sed -n 's/^SERVICE_USER=//p' "$CONF/install.conf" 2>/dev/null || true)"
SERVICE_USER="${SERVICE_USER:-lenta}"
CREATED_USERS="$(sed -n 's/^CREATED_USERS=//p' "$CONF/install.conf" 2>/dev/null || true)"
id "$SERVICE_USER" >/dev/null 2>&1 || { echo "Service account '$SERVICE_USER' doesn't exist." >&2; exit 1; }
SERVICE_GROUP="$(id -gn "$SERVICE_USER")"
# shellcheck source=datadir.sh
source "$HERE/datadir.sh"

OLD="$(current_data_dir)"
OLD="$(realpath -m "${OLD:-$DEFAULT_DATA}")"
if [[ $# -eq 0 ]]; then
  echo "LENTA data folder: $OLD"
  echo "Move it with:      sudo $0 /new/path"
  exit 0
fi
NEW="$(check_data_dir "$1")" || exit 1
if [[ "$NEW" == "$OLD" ]]; then echo "LENTA already keeps its data in $NEW."; exit 0; fi
if [[ "$NEW" == "$OLD"/* || "$OLD" == "$NEW"/* ]]; then
  echo "The new data folder can't be inside the old one (or the other way round)." >&2; exit 1
fi
if [[ -d "$NEW" && -n "$(ls -A "$NEW" 2>/dev/null)" && ! -f "$NEW/lenta.db" ]]; then
  echo "$NEW is not empty and holds no LENTA database. Choose an empty or new folder." >&2; exit 1
fi
NEED=$(du -sk --exclude=transcode "$OLD" 2>/dev/null | cut -f1 || echo 0)
mkdir -p "$NEW"
FREE=$(df -Pk "$NEW" | awk 'NR==2{print $4}')
if [[ -n "$NEED" && -n "$FREE" && "$NEED" -gt "$FREE" ]]; then
  echo "Not enough space in $NEW: need $((NEED/1024)) MB, $((FREE/1024)) MB free." >&2; exit 1
fi

echo "==> Stopping LENTA"
systemctl stop lenta 2>/dev/null || true
cp -a "$CONF/lenta.env" "$CONF/lenta.env.bak"
move_data_dir "$OLD" "$NEW"
set_data_dir_in_env "$NEW" "$OLD"
prepare_data_dir "$NEW" || { echo "Settings left unchanged; restore with: sudo cp $CONF/lenta.env.bak $CONF/lenta.env" >&2; exit 1; }
for u in $CREATED_USERS; do
  if [[ "$u" == "$SERVICE_USER" ]]; then usermod -d "$NEW" "$u" 2>/dev/null || true; fi
done
TEMPLATE="$HERE/lenta.service"
[[ -f "$TEMPLATE" ]] || TEMPLATE="$PREFIX/deploy/lenta.service"
render_unit "$TEMPLATE" "$NEW"
systemctl daemon-reload
echo "==> Starting LENTA"
systemctl start lenta
sleep 3
if systemctl is-active --quiet lenta; then
  echo "LENTA now keeps its data in $NEW."
else
  echo "LENTA did not start. See: sudo journalctl -u lenta -n 50" >&2
  echo "To go back: sudo cp $CONF/lenta.env.bak $CONF/lenta.env and move the data back." >&2
  exit 1
fi
