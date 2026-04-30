"""Shared disk-based cache backed by hashed JSON files.

Every cached value is stored as ``{"ts": <unix-epoch>, "body": <payload>}``,
optionally with an ``"etag"`` field for conditional HTTP requests.

The *body* may be any JSON-serialisable Python value (``str``, ``dict``,
``list``, ``bool``, ``int``, ``float``, or ``None``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Time constants (shared across the pipeline).
SECONDS_PER_DAY = 86400

# Default cache root: <repo>/src/.cache (siblings of utils/, scrapers/, enrichers/).
# All scrapers/enrichers should resolve cache paths from this constant rather than
# computing their own. Override via the ``REPRODB_CACHE_ROOT`` environment variable.
_DEFAULT_CACHE_ROOT = Path(__file__).resolve().parent.parent.parent / ".cache"
CACHE_ROOT: Path = Path(os.environ.get("REPRODB_CACHE_ROOT", str(_DEFAULT_CACHE_ROOT)))


def cache_path(base_dir: str, key: str, namespace: str = "default") -> str:
    """Return the file path for *key* inside *base_dir*/*namespace*/."""
    ns_dir = os.path.join(base_dir, namespace)
    os.makedirs(ns_dir, exist_ok=True)
    hashed = hashlib.sha256(key.encode()).hexdigest()
    return os.path.join(ns_dir, hashed)


_MISSING = object()  # sentinel – distinguishes "body not in entry" from "body is None"


def read_cache(base_dir: str, key: str, ttl: int, namespace: str = "default") -> Any:
    """Return cached body if fresh (younger than *ttl* seconds), else ``_MISSING``.

    Returns the *original* Python value that was stored (dict, list, str, …).
    Returns the module-level sentinel ``_MISSING`` on cache miss so callers
    can distinguish a miss from a cached ``None``.
    """
    path = cache_path(base_dir, key, namespace)
    if not os.path.exists(path):
        return _MISSING
    try:
        with open(path) as f:
            entry = json.load(f)
        ts = float(entry.get("ts", 0))
        if time.time() - ts < ttl:
            return entry.get("body", _MISSING)
    except (json.JSONDecodeError, KeyError, OSError):
        pass
    return _MISSING


def write_cache(
    base_dir: str,
    key: str,
    body: Any,
    namespace: str = "default",
    etag: str | None = None,
) -> None:
    """Write *body* to the cache, optionally storing an HTTP ETag.

    Uses atomic write (temp file + ``os.replace``) to avoid partial/corrupt
    cache files when multiple processes run concurrently.
    """
    path = cache_path(base_dir, key, namespace)
    entry: dict[str, Any] = {"ts": time.time(), "body": body}
    if etag:
        entry["etag"] = etag
    dir_name = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(entry, f)
        os.replace(tmp_path, path)
    except BaseException:
        os.unlink(tmp_path)
        raise


def read_cache_entry(base_dir: str, key: str, namespace: str = "default") -> dict | None:
    """Return the full cache entry dict regardless of TTL, or ``None``."""
    path = cache_path(base_dir, key, namespace)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            result: dict = json.load(f)
            return result
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def refresh_cache_ts(base_dir: str, key: str, namespace: str = "default") -> None:
    """Touch the timestamp of a cache entry without changing its body."""
    path = cache_path(base_dir, key, namespace)
    try:
        with open(path) as f:
            entry = json.load(f)
        entry["ts"] = time.time()
        with open(path, "w") as f:
            json.dump(entry, f)
    except (json.JSONDecodeError, KeyError, OSError):
        pass
