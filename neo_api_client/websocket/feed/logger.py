"""Small structured-log adapter used by the upstream SFeed client."""

from __future__ import annotations

import logging
from typing import Any


class _EventLogger:
    def __init__(self, name: str) -> None:
        self._logger = logging.getLogger(name)

    def _write(self, level: int, event: str, **fields: Any) -> None:
        # Do not log auth fields or raw frames. Keep only connection state
        # useful for diagnosing a feed restart.
        allowed = {
            "url",
            "error",
            "attempt",
            "max_attempts",
            "max_reconnect_attempts",
            "reconnect_count",
            "close_code",
            "close_reason",
            "intent",
            "tokens",
        }
        safe_fields = {key: value for key, value in fields.items() if key in allowed}
        suffix = f" {safe_fields}" if safe_fields else ""
        self._logger.log(level, "%s%s", event, suffix)

    def debug(self, event: str, **fields: Any) -> None:
        self._write(logging.DEBUG, event, **fields)

    def info(self, event: str, **fields: Any) -> None:
        self._write(logging.INFO, event, **fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._write(logging.WARNING, event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        self._write(logging.ERROR, event, **fields)


def get_logger(name: str) -> _EventLogger:
    return _EventLogger(name)
