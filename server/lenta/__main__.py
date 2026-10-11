"""Run with:  python -m lenta

Serves HTTP and HTTPS side by side (listeners.py); both are set up in Server admin › Settings.
"""
import asyncio


def main() -> None:
    import os
    import sys
    if os.geteuid() == 0 and os.environ.get("LENTA_ALLOW_ROOT") != "1":
        sys.exit("LENTA refuses to run as root. Run it as its service account (see deploy/install.sh).")
    from .listeners import Manager
    from .main import app
    asyncio.run(Manager(app).run())


if __name__ == "__main__":
    main()
