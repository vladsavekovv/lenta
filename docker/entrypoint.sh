#!/bin/sh
# Start LENTA in a container, as a normal account rather than as root.
#
# The server refuses to run as root — the same refusal it makes on a host — so something has to decide
# which account it runs as. Two ways in:
#
#   * The container is started as a user already (compose's `user:`, or the image's own `lenta`,
#     uid 1000). Nothing to do: hand straight over.
#   * The container is started as root, which is what Unraid, Synology and a plain `docker run` do by
#     default. Then PUID and PGID say which account the files on the host belong to, the data folder is
#     given to it, and the server is started as that account. Root gets no further than this script.
#
# Media folders are never touched. They are the host's, usually mounted read-only, and a media server
# has no business changing what it was asked to play.
set -eu

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
DATA="${LENTA_DATA_DIR:-/data}"

if [ "$(id -u)" != "0" ]; then
  exec "$@"                       # already an ordinary account: run as it
fi

mkdir -p "$DATA"
# Only when it is not already theirs: on a large library this walk is worth skipping.
owner="$(stat -c '%u:%g' "$DATA" 2>/dev/null || echo '')"
if [ "$owner" != "${PUID}:${PGID}" ]; then
  echo "LENTA: giving $DATA to ${PUID}:${PGID}"
  chown -R "${PUID}:${PGID}" "$DATA" || echo "LENTA: could not change the owner of $DATA — carrying on"
fi

if [ -n "${LENTA_TRANSCODE_DIR:-}" ] && [ "${LENTA_TRANSCODE_DIR}" != "${DATA}/transcode" ]; then
  mkdir -p "$LENTA_TRANSCODE_DIR"
  chown -R "${PUID}:${PGID}" "$LENTA_TRANSCODE_DIR" || true
fi

# --init-groups needs a name; fall back to the raw numbers for a PUID with no account in the image.
if id -nu "$PUID" >/dev/null 2>&1; then
  exec setpriv --reuid "$PUID" --regid "$PGID" --init-groups "$@"
fi
exec setpriv --reuid "$PUID" --regid "$PGID" --clear-groups "$@"
