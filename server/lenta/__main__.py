"""Run with:  python -m lenta"""
import uvicorn

from .config import HOST, PORT


def main() -> None:
    import os
    import sys
    if os.geteuid() == 0 and os.environ.get("LENTA_ALLOW_ROOT") != "1":
        sys.exit("LENTA refuses to run as root. Run it as its service account (see deploy/install.sh).")
    uvicorn.run("lenta.main:app", host=HOST, port=PORT, proxy_headers=True, forwarded_allow_ips="*",
                log_level="warning", timeout_keep_alive=30)


if __name__ == "__main__":
    main()
