"""Logging setup plus an in-memory buffer handler.

The buffer handler lets the Streamlit UI display and persist the exact log
lines produced during an analysis or execution run, so they can be saved to
GCS alongside the other artifacts.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone

_CONFIGURED = False


class BufferHandler(logging.Handler):
    """A logging handler that keeps the most recent records in memory."""

    def __init__(self, capacity: int = 2000) -> None:
        super().__init__()
        self._records: deque[str] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._records.append(self.format(record))
        except Exception:  # never let logging crash the app
            self.handleError(record)

    def get_lines(self) -> list[str]:
        return list(self._records)

    def text(self) -> str:
        return "\n".join(self._records)

    def clear(self) -> None:
        self._records.clear()


_BUFFER = BufferHandler()


def configure_logging(level: str = "INFO") -> BufferHandler:
    """Configure root logging once and return the shared buffer handler."""
    global _CONFIGURED
    fmt = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    formatter = logging.Formatter(fmt, datefmt="%Y-%m-%dT%H:%M:%S%z")

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not _CONFIGURED:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root.addHandler(stream)

        _BUFFER.setFormatter(formatter)
        root.addHandler(_BUFFER)

        # Quiet down noisy libraries.
        logging.getLogger("google").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("google_genai").setLevel(logging.WARNING)
        _CONFIGURED = True

    return _BUFFER


def get_buffer() -> BufferHandler:
    return _BUFFER


def run_banner(title: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"===== {title} @ {ts} ====="
