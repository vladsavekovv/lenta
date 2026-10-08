"""Logging with an in-memory ring buffer the admin dashboard can read."""
import collections
import logging
import time

_buffer: collections.deque = collections.deque(maxlen=500)


class _RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _buffer.append({
                "time": record.created,
                "level": record.levelname,
                "source": record.name.replace("lenta.", ""),
                "message": record.getMessage(),
            })
        except Exception:  # never let logging break the server
            pass


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger("lenta")
    if root.handlers:
        return
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    root.addHandler(stream)
    root.addHandler(_RingHandler())


def recent(limit: int = 200) -> list:
    return list(_buffer)[-limit:][::-1]


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"lenta.{name}")


__all__ = ["setup_logging", "recent", "get", "time"]
