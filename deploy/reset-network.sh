#!/usr/bin/env bash
# Get back into LENTA when HTTP or HTTPS was set up in a way you can't reach any more.
#   sudo /opt/lenta/deploy/reset-network.sh          show what LENTA listens on
#   sudo /opt/lenta/deploy/reset-network.sh --reset  plain HTTP back on its usual port (LENTA_PORT, 8484),
#                                                    HTTPS on 8443 with LENTA's own certificate, then restart
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
RESET=0
[[ "${1:-}" == "--reset" ]] && RESET=1

set -a; source "$ENV_FILE"; set +a
cd "$PREFIX/server"
sudo -u "$SERVICE_USER" --preserve-env=LENTA_DATA_DIR,LENTA_TRANSCODE_DIR,LENTA_PORT \
  env LENTA_RESET="$RESET" "$PREFIX/venv/bin/python" -c '
import os
from lenta.db import init_db, set_setting
from lenta.listeners import wanted
init_db()
if os.environ["LENTA_RESET"] == "1":
    for k, v in (("net_http_enabled", "1"), ("net_http_port", ""), ("net_https_enabled", "1"),
                 ("net_https_port", "8443"), ("net_cert_mode", "auto")):
        set_setting(k, v)
w = wanted()
for proto in ("http", "https"):
    port, on = w[proto]["port"], w[proto]["on"]
    print(f"  {proto.upper():5}  " + (f"on, port {port}" if on else "off"))
'
if [[ $RESET == 1 ]]; then
  systemctl restart lenta
  IP="$(hostname -I | awk '{print $1}')"
  echo "Done. Open  http://$IP:${LENTA_PORT:-8484}  (or https://$IP:8443)"
else
  echo "To turn plain HTTP back on with the usual ports:  sudo $0 --reset"
fi
