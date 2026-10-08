#!/usr/bin/env bash
# Set a new password for a LENTA account, for example when the only administrator forgot theirs.
#   sudo /opt/lenta/deploy/reset-password.sh                 list the accounts
#   sudo /opt/lenta/deploy/reset-password.sh <username>      set a new password (asked twice, not shown)
# Add --admin after the user name to also make that account an administrator.
set -euo pipefail

PREFIX=/opt/lenta
ENV_FILE=/etc/lenta/lenta.env
SERVICE_USER="$(sed -n 's/^SERVICE_USER=//p' /etc/lenta/install.conf 2>/dev/null || true)"
SERVICE_USER="${SERVICE_USER:-lenta}"

if [[ $EUID -ne 0 ]]; then
  echo "Run this with sudo: sudo $0 $*" >&2
  exit 1
fi
if [[ ! -x $PREFIX/venv/bin/python || ! -f $ENV_FILE ]]; then
  echo "LENTA doesn't seem to be installed here (no $PREFIX/venv or $ENV_FILE)." >&2
  exit 1
fi

USERNAME="${1:-}"
MAKE_ADMIN=0
[[ "${2:-}" == "--admin" ]] && MAKE_ADMIN=1

PASSWORD=""
if [[ -n $USERNAME ]]; then
  read -rsp "New password for '$USERNAME': " PASSWORD; echo
  read -rsp "Type it again: " AGAIN; echo
  if [[ "$PASSWORD" != "$AGAIN" ]]; then echo "The two passwords are different. Nothing changed." >&2; exit 1; fi
  if [[ ${#PASSWORD} -lt 6 ]]; then echo "Use at least 6 characters. Nothing changed." >&2; exit 1; fi
fi

# Run as the service account with the service's settings, so the right data folder is used
# and file ownership stays correct. The password goes through stdin, never the command line.
set -a; source "$ENV_FILE"; set +a
cd "$PREFIX/server"
printf '%s' "$PASSWORD" | sudo -u "$SERVICE_USER" --preserve-env=LENTA_DATA_DIR,LENTA_TRANSCODE_DIR \
  env LENTA_USERNAME="$USERNAME" LENTA_MAKE_ADMIN="$MAKE_ADMIN" \
  "$PREFIX/venv/bin/python" -c '
import os, sys
from lenta import auth
from lenta.db import db, init_db
init_db()
name = os.environ["LENTA_USERNAME"]
with db() as con:
    if not name:
        rows = con.execute("SELECT username, is_admin FROM users ORDER BY username COLLATE NOCASE").fetchall()
        if not rows:
            print("No accounts yet: open LENTA in a browser to create the administrator.")
        for r in rows:
            print(("  admin   " if r["is_admin"] else "  viewer  ") + r["username"])
        sys.exit(0)
    row = con.execute("SELECT id FROM users WHERE username = ? COLLATE NOCASE", (name,)).fetchone()
    if not row:
        sys.exit(f"No account called {name!r}. Run without a name to list them.")
    con.execute("UPDATE users SET password_hash = ? WHERE id = ?", (auth.hash_password(sys.stdin.read()), row["id"]))
    if os.environ["LENTA_MAKE_ADMIN"] == "1":
        con.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (row["id"],))
print(f"Password changed for {name}." + (" It is now an administrator." if os.environ["LENTA_MAKE_ADMIN"] == "1" else ""))
'
