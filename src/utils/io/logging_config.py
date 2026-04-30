"""Centralized logging configuration for the ReproDB pipeline."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time

logger = logging.getLogger(__name__)

LOG_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

LOG_FORMATS = ("text", "json")

TEXT_FORMAT = "%(message)s"


class JSONFormatter(logging.Formatter):
    """Emit each log record as a single JSON line.

    Fields: ``ts`` (ISO-8601), ``level``, ``logger``, ``message``,
    plus ``exc`` when an exception is attached.
    """

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def add_log_level_arg(parser: argparse.ArgumentParser) -> None:
    """Add ``--log-level`` and ``--log-format`` arguments to an argparse parser."""
    parser.add_argument(
        "--log-level",
        choices=LOG_LEVELS,
        default="info",
        help="Set logging verbosity (default: info)",
    )
    parser.add_argument(
        "--log-format",
        choices=LOG_FORMATS,
        default="text",
        help="Log output format: text (human) or json (structured)",
    )


def setup_logging(level: int = logging.INFO, *, log_format: str = "text") -> None:
    """Configure root logger with a consistent format.

    Call once from each script's ``if __name__ == "__main__"`` block::

        from src.utils.io.logging_config import setup_logging
        setup_logging()

    Args:
        level: ``int`` (e.g. ``logging.DEBUG``) or ``str`` (e.g. ``"debug"``).
        log_format: ``"text"`` for human-readable or ``"json"`` for structured JSON lines.
    """
    if isinstance(level, str):
        level = LOG_LEVELS.get(level.lower(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)

    if log_format == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter(TEXT_FORMAT))

    # Avoid duplicate handlers on repeated calls
    root.handlers.clear()
    root.addHandler(handler)
