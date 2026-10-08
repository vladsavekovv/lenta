# Shared by install.sh and set-data-dir.sh: choosing, checking and moving LENTA's data folder.
# The data folder holds the database (users, watch history, metadata, settings), artwork,
# downloaded subtitles, caches and, by default, transcoding scratch space.
# Expects: CONF (settings folder), SERVICE_USER, SERVICE_GROUP. Sourced, not run.

ENV_FILE="$CONF/lenta.env"
DEFAULT_DATA=/var/lib/lenta

# The data folder currently configured, empty if none yet.
current_data_dir() {
  if [[ -f "$ENV_FILE" ]]; then sed -n 's/^LENTA_DATA_DIR=//p' "$ENV_FILE" | tail -1; fi
}

# Normalise and reject places that must never hold LENTA's data.
check_data_dir() {
  local dir="$1"
  if [[ "$dir" != /* ]]; then
    echo "The data folder must be an absolute path (starting with /): $dir" >&2; return 1
  fi
  if [[ ! "$dir" =~ ^[A-Za-z0-9._/+@:-]+$ ]]; then
    echo "Use only letters, digits and . _ - + @ : in the data folder path (no spaces): $dir" >&2; return 1
  fi
  dir="$(realpath -m "$dir")"
  case "$dir" in
    /|/bin|/bin/*|/boot|/boot/*|/dev|/dev/*|/etc|/etc/*|/lib|/lib/*|/lib64|/lib64/*|/proc|/proc/*| \
    /run|/run/*|/sbin|/sbin/*|/sys|/sys/*|/usr|/usr/*|/opt/lenta|/opt/lenta/*|/tmp|/var/tmp|/home|/root|/root/*)
      echo "LENTA can't keep its data in $dir. Choose a dedicated folder, e.g. /srv/lenta or /mnt/ssd/lenta." >&2
      return 1 ;;
  esac
  echo "$dir"
}

# Write LENTA_DATA_DIR (and move LENTA_TRANSCODE_DIR along if it lived inside the old folder).
set_data_dir_in_env() {
  local new="$1" old="$2"
  if grep -q '^LENTA_DATA_DIR=' "$ENV_FILE"; then
    sed -i "s|^LENTA_DATA_DIR=.*|LENTA_DATA_DIR=$new|" "$ENV_FILE"
  else
    echo "LENTA_DATA_DIR=$new" >> "$ENV_FILE"
  fi
  if [[ -n "$old" ]]; then
    sed -i "s|^LENTA_TRANSCODE_DIR=$old\(/.*\)\?$|LENTA_TRANSCODE_DIR=$new\1|" "$ENV_FILE"
  fi
}

# Copy the data from old to new (unless new already holds a LENTA database), keep the old copy
# renamed as a backup. The service must be stopped by the caller.
move_data_dir() {
  local old="$1" new="$2"
  mkdir -p "$new"
  if [[ -f "$new/lenta.db" ]]; then
    echo "==> $new already contains a LENTA database: using it as is (nothing copied from $old)"
    return 0
  fi
  if [[ ! -d "$old" ]]; then
    echo "==> The old data folder $old doesn't exist: starting with an empty $new"
    return 0
  fi
  echo "==> Copying LENTA data from $old to $new"
  rsync -aHAX --info=progress2 --exclude 'transcode/' "$old/" "$new/"
  if [[ ! -f "$new/lenta.db" && -f "$old/lenta.db" ]]; then
    echo "Copy failed: $new/lenta.db is missing. Nothing was changed." >&2
    return 1
  fi
  local backup="${old%/}.moved-$(date +%Y%m%d-%H%M%S)"
  if mv "$old" "$backup" 2>/dev/null; then
    echo "==> Old folder kept as $backup. Delete it once LENTA works from the new place."
  else
    echo "==> The old folder $old was left in place (it may be a mount point). Delete its contents when you're happy."
  fi
}

# Ownership, and proof that the service account can actually write there
# (every parent folder must at least be passable for it).
prepare_data_dir() {
  local dir="$1"
  mkdir -p "$dir"
  chown -R "$SERVICE_USER:$SERVICE_GROUP" "$dir"
  chmod 750 "$dir"
  if ! runuser -u "$SERVICE_USER" -- test -w "$dir" 2>/dev/null; then
    echo "The account '$SERVICE_USER' can't reach $dir: one of its parent folders blocks it." >&2
    echo "Allow passing through them, for example:  sudo setfacl -m u:$SERVICE_USER:x $(dirname "$dir")" >&2
    return 1
  fi
}

# systemd unit with the chosen user, data folder and (optional) separate transcode folder.
render_unit() {
  local template="$1" data="$2" transcode extra=""
  transcode="$(sed -n 's/^LENTA_TRANSCODE_DIR=//p' "$ENV_FILE" | tail -1)"
  if [[ -n "$transcode" && "$transcode" != "$data" && "$transcode" != "$data"/* ]]; then
    mkdir -p "$transcode"
    chown "$SERVICE_USER:$SERVICE_GROUP" "$transcode"
    extra=" $transcode"
  fi
  sed -e "s|@SERVICE_USER@|$SERVICE_USER|g" -e "s|@SERVICE_GROUP@|$SERVICE_GROUP|g" \
      -e "s|@DATA_DIR@|$data|g" -e "s|@EXTRA_RW@|$extra|g" \
      "$template" > /etc/systemd/system/lenta.service
  chmod 644 /etc/systemd/system/lenta.service
}
