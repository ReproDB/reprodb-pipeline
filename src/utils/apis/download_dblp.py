#!/usr/bin/env python3
"""Download DBLP XML database (~3 GB compressed) for author matching.

Usage::

    python -m src.utils.download_dblp            # interactive
    python -m src.utils.download_dblp --auto     # non-interactive (CI)

Output: ``data/dblp/dblp.xml.gz``

This replaces the former ``scripts/download_dblp.sh`` and removes the
dependency on ``curl``.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

DBLP_URL = "https://dblp.org/xml/dblp.xml.gz"
DBLP_DIR = Path("data/dblp")
DBLP_FILE = DBLP_DIR / "dblp.xml.gz"
MIN_SIZE_MB = 500  # anything smaller is likely truncated


def _remote_last_modified(url: str, *, timeout: int = 10) -> float | None:
    """Return the remote Last-Modified timestamp as epoch seconds, or None."""
    try:
        resp = requests.head(url, timeout=timeout, allow_redirects=True)
        lm = resp.headers.get("Last-Modified")
        if lm:
            from email.utils import parsedate_to_datetime

            return parsedate_to_datetime(lm).timestamp()
    except (requests.RequestException, ValueError, TypeError):
        pass
    return None


def _is_up_to_date(path: Path) -> bool | None:
    """Check if *path* is at least as new as the remote file.

    Returns ``True`` if up-to-date, ``False`` if outdated, or ``None`` if the
    remote date could not be determined.
    """
    local_mtime = path.stat().st_mtime
    remote_mtime = _remote_last_modified(DBLP_URL)
    if remote_mtime is None:
        return None
    return local_mtime >= remote_mtime


def _download(dest: Path) -> None:
    """Stream-download DBLP XML to *dest* with progress logging."""
    logger.info("Downloading %s ...", DBLP_URL)
    resp = requests.get(DBLP_URL, stream=True, timeout=600)
    resp.raise_for_status()

    total = int(resp.headers.get("content-length", 0)) or None
    downloaded = 0
    last_pct = -1

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    try:
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):  # 1 MB
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    if pct >= last_pct + 5:
                        last_pct = pct
                        logger.info("  %d%% (%d / %d MB)", pct, downloaded >> 20, total >> 20)
        tmp.rename(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def download_dblp(*, auto: bool = False) -> bool:
    """Download or update the DBLP XML file.

    Parameters
    ----------
    auto : bool
        If ``True`` run non-interactively (skip prompts, download only
        when missing or outdated).

    Returns
    -------
    bool
        ``True`` if the file is present and valid at exit.
    """
    DBLP_DIR.mkdir(parents=True, exist_ok=True)

    if DBLP_FILE.is_file():
        size_mb = DBLP_FILE.stat().st_size >> 20
        freshness = _is_up_to_date(DBLP_FILE)

        if freshness is True:
            logger.info("DBLP file is up to date (%d MB)", size_mb)
            return True

        if freshness is False:
            logger.warning("DBLP file is outdated (%d MB)", size_mb)
        else:
            logger.warning("DBLP file exists (%d MB), could not check remote date", size_mb)
            if auto:
                return True

        if not auto:
            try:
                answer = input("Re-download? (y/N): ").strip().lower()
            except EOFError:
                answer = "n"
            if answer != "y":
                return True

        DBLP_FILE.unlink()

    # Connectivity check
    try:
        requests.head(DBLP_URL, timeout=10)
    except requests.ConnectionError:
        proxy = os.environ.get("https_proxy", "")
        logger.error("Cannot connect to dblp.org (proxy: %s)", proxy)
        return False

    _download(DBLP_FILE)

    size_mb = DBLP_FILE.stat().st_size >> 20
    if size_mb < MIN_SIZE_MB:
        logger.error("File too small (%d MB, expected >= %d MB) — download may be truncated", size_mb, MIN_SIZE_MB)
        return False

    logger.info("Download complete (%d MB)", size_mb)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Download DBLP XML database")
    parser.add_argument("--auto", action="store_true", help="Non-interactive mode")
    args = parser.parse_args()

    from src.utils.io.logging_config import setup_logging

    setup_logging()

    ok = download_dblp(auto=args.auto)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
