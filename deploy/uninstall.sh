#!/usr/bin/env bash
# Removes the LENTA service and program files. Your media is never touched.
# Pass --purge to also delete the database, settings and cached artwork (wherever the data folder is).
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run this with sudo" >&2; exit 1; }
DATA="$(sed -n 's/^LENTA_DATA_DIR=//p' /etc/lenta/lenta.env 2>/dev/null | tail -1 || true)"
DATA="${DATA:-/var/lib/lenta}"
TRANSCODE="$(sed -n 's/^LENTA_TRANSCODE_DIR=//p' /etc/lenta/lenta.env 2>/dev/null | tail -1 || true)"
CREATED_USERS="$(sed -n 's/^CREATED_USERS=//p' /etc/lenta/install.conf 2>/dev/null || true)"
systemctl disable --now lenta 2>/dev/null || true
rm -f /etc/systemd/system/lenta.service
systemctl daemon-reload
rm -rf /opt/lenta
if [[ "${1:-}" == "--purge" ]]; then
  # Only delete a folder that really is LENTA's (holds its database), never a system path.
  case "$DATA" in /|/home|/root|/etc|/usr|/var|/srv|/mnt|/media|/opt) DATA="" ;; esac
  if [[ -n "$DATA" && -d "$DATA" ]]; then
    if [[ -f "$DATA/lenta.db" || -z "$(ls -A "$DATA")" ]]; then rm -rf "$DATA"; echo "Deleted data folder $DATA"
    else echo "Left $DATA in place: it doesn't look like a LENTA data folder." >&2; fi
  fi
  if [[ -n "$TRANSCODE" && -d "$TRANSCODE" && "$TRANSCODE" != "$DATA"/* ]]; then
    echo "Your separate transcoding folder $TRANSCODE was left in place; delete it yourself if you like."
  fi
  rm -rf /etc/lenta
  # Only accounts the installer created are removed; an existing account you chose is left alone.
  for u in $CREATED_USERS; do
    if [[ "$u" != "root" ]] && id "$u" >/dev/null 2>&1 && [[ "$(id -u "$u")" -ne 0 ]]; then
      userdel "$u" 2>/dev/null && echo "Removed service account $u" || true
    fi
  done
  echo "LENTA removed, including its data."
else
  echo "LENTA removed. Data kept in $DATA and settings in /etc/lenta (use --purge to delete)."
fi
