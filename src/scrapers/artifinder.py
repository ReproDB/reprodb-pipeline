"""Loader for ArtiFinder-Data (https://github.com/DistriNet/ArtiFinder-Data).

ArtiFinder scrapes conference papers directly and identifies links to their
artifacts.  The published data set is organised as::

    data/<venue>/<year>.yaml

where ``<venue>`` is one of ``ccs``, ``ndss``, ``sp``, ``usenix`` and each YAML
file is a list of entries::

    - title: "Paper title."
      authors: ["Jane Doe", "John Roe 0001"]
      page_link: "https://doi.org/..."
      artifacts_discovered:                 # ordered best score first; [] if none
        - "https://github.com/org/repo"
        - "https://doi.org/10.5281/zenodo.1"
      validated: false

The older schema used a single ``discovered_artifact: <url> | null`` key; it is
still accepted.  Only entries with at least one link are returned, but the
total number of scanned papers per conference-year is also reported so that a
discovery rate can be computed.  All links are kept, normalised and
de-duplicated, in ArtiFinder's order (best score first) as ``artifact_urls``.

Public API:
    load_artifinder(conf_regex=None) -> ArtiFinderData
    load_artifinder_data(conf_regex=None) -> list[dict]   # entries only
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

import yaml

from .repo_utils import download_file

logger = logging.getLogger(__name__)

# ── Remote layout ────────────────────────────────────────────────────────────
ARTIFINDER_OWNER_REPO = "DistriNet/ArtiFinder-Data"
ARTIFINDER_API_BASE = f"https://api.github.com/repos/{ARTIFINDER_OWNER_REPO}/contents/data"
ARTIFINDER_RAW_BASE = f"https://raw.githubusercontent.com/{ARTIFINDER_OWNER_REPO}/main/data"

#: Environment variable pointing at a local checkout of ArtiFinder-Data. When
#: set (and it contains a ``data/`` directory or the venue directories
#: directly), the loader reads from disk instead of GitHub. Useful for offline
#: CI runs and reproducible builds.
ARTIFINDER_LOCAL_ENV = "REPRODB_ARTIFINDER_DIR"

# ArtiFinder venue directory → (ReproDB conference id, research area).
# All ArtiFinder venues are security conferences.  CCS and SP are included for
# completeness even though CCS is not (yet) tracked for AE in ReproDB.
_VENUE_MAP: dict[str, tuple[str, str]] = {
    "acsac": ("ACSAC", "security"),
    "ccs": ("CCS", "security"),
    "ndss": ("NDSS", "security"),
    "sp": ("SP", "security"),
    "usenix": ("USENIXSEC", "security"),
}

_YEAR_RE = re.compile(r"^(\d{4})\.ya?ml$")

#: Default earliest year to consider.  Artifact evaluation started around 2017,
#: so ArtiFinder links from earlier editions have no AE counterpart to enrich
#: and are excluded by default.  Override via ``load_artifinder(min_year=...)``.
DEFAULT_MIN_YEAR = 2017


class ArtiFinderNoResultsError(RuntimeError):
    """Raised when ArtiFinder yields no discovered-artifact entries at all.

    This usually means the upstream data could not be fetched (network/proxy
    problem, GitHub rate limiting, a changed repository layout) or that the
    ``conf_regex`` / ``min_year`` filters excluded everything. Failing loudly
    prevents silently overwriting previously generated outputs with empty ones.
    """


class ArtiFinderData(NamedTuple):
    """Parsed ArtiFinder data set.

    Attributes:
        entries: One dict per paper that has a discovered artifact link, with
            keys ``conference``, ``category``, ``year``, ``title``, ``authors``,
            ``page_link`` and ``artifact_urls`` (all discovered links, best
            score first).
        counts: One dict per conference-year with keys ``conference``,
            ``category``, ``year``, ``total_papers`` (scanned) and
            ``discovered`` (papers with a non-null artifact link).
        source_updated: ``YYYY-MM-DD`` date the ArtiFinder-Data source was last
            updated (its latest commit), or ``None`` if it could not be found.
    """

    entries: list[dict]
    counts: list[dict]
    source_updated: str | None = None


def normalize_artifact_url(url: str) -> str:
    """Return *url* with an explicit scheme and no trailing slash.

    ArtiFinder sometimes records bare hosts (``github.com/org/repo``); prefix
    those with ``https://``.  Values that already carry a scheme are returned
    unchanged apart from trailing-slash trimming.
    """
    u = (url or "").strip()
    if not u:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", u):
        u = "https://" + u.lstrip("/")
    return u.rstrip("/")


def _extract_artifact_urls(rec: dict) -> list[str]:
    """Return the normalised, de-duplicated artifact URLs of a record.

    Reads the current ``artifacts_discovered`` list (already ordered best score
    first) and, for backwards compatibility, the legacy single-valued
    ``discovered_artifact`` key.  Every link is kept as-is, including bare
    hosts and org/profile pages.
    """
    raw: list = []
    listed = rec.get("artifacts_discovered")
    if isinstance(listed, list):
        raw.extend(listed)
    elif isinstance(listed, str):
        raw.append(listed)
    legacy = rec.get("discovered_artifact")
    if legacy:
        raw.append(legacy)

    urls: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            continue
        url = normalize_artifact_url(item)
        if url and url not in urls:
            urls.append(url)
    return urls


def _clean_author(name: str) -> str:
    """Strip DBLP disambiguation suffixes and collapse whitespace."""
    n = re.sub(r"[\t\n\r]+", " ", name or "")
    n = re.sub(r"\s+\d{4}$", "", n)
    return re.sub(r"\s+", " ", n).strip()


def _list_dir(api_url: str) -> list[dict]:
    """Return the GitHub contents listing for *api_url* (empty list on failure)."""
    body = download_file(api_url)
    if not body:
        return []
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        logger.warning("  ArtiFinder: could not parse directory listing at %s", api_url)
        return []
    return data if isinstance(data, list) else []


def _resolve_local_dir(local_dir: str | Path | None) -> Path | None:
    """Return the ``data`` directory of a local ArtiFinder-Data checkout, or None.

    Accepts either the repository root (containing ``data/``) or the ``data``
    directory itself. Falls back to the :data:`ARTIFINDER_LOCAL_ENV` env var.
    """
    raw = local_dir if local_dir is not None else os.environ.get(ARTIFINDER_LOCAL_ENV)
    if not raw:
        return None
    base = Path(raw)
    if (base / "data").is_dir():
        base = base / "data"
    if not base.is_dir():
        logger.warning("  ArtiFinder: local dir %s not found; falling back to remote", raw)
        return None
    return base


def _local_venues(base: Path) -> list[dict]:
    """List venue directories in a local checkout, mirroring the API shape."""
    return [{"name": p.name, "type": "dir"} for p in sorted(base.iterdir()) if p.is_dir()]


def _local_files(venue_dir: Path) -> list[dict]:
    """List year files in a local venue directory, mirroring the API shape."""
    return [{"name": p.name, "type": "file"} for p in sorted(venue_dir.iterdir()) if p.is_file()]


def _local_read(path: Path) -> str | None:
    """Read a local file, returning None if it is missing."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _remote_source_updated() -> str | None:
    """Return the ``YYYY-MM-DD`` date of the latest ArtiFinder-Data commit."""
    body = download_file(f"https://api.github.com/repos/{ARTIFINDER_OWNER_REPO}/commits?per_page=1")
    if not body:
        return None
    try:
        commits = json.loads(body)
        return str(commits[0]["commit"]["committer"]["date"])[:10]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return None


def _local_source_updated(base: Path) -> str | None:
    """Return the last-updated date of a local ArtiFinder-Data checkout.

    Prefers the last git commit date; falls back to the newest YAML mtime.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(base), "log", "-1", "--format=%cs"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    mtimes = [p.stat().st_mtime for p in base.rglob("*.yaml")]
    if mtimes:
        return datetime.fromtimestamp(max(mtimes), tz=timezone.utc).strftime("%Y-%m-%d")
    return None


def _parse_year_file(conf: str, area: str, year: int, raw_text: str) -> tuple[list[dict], int]:
    """Parse a ``<year>.yaml`` file.

    Returns ``(entries_with_artifact, total_papers_scanned)``.
    """
    try:
        records = yaml.safe_load(raw_text) or []
    except yaml.YAMLError as exc:
        logger.warning("  ArtiFinder: YAML error in %s/%s: %s", conf, year, exc)
        return [], 0
    if not isinstance(records, list):
        return [], 0

    entries: list[dict] = []
    total = 0
    for rec in records:
        if not isinstance(rec, dict):
            continue
        title = (rec.get("title") or "").strip()
        if not title:
            continue
        total += 1
        urls = _extract_artifact_urls(rec)
        if not urls:
            continue
        authors = [_clean_author(a) for a in (rec.get("authors") or []) if isinstance(a, str) and a.strip()]
        entries.append(
            {
                "conference": conf,
                "category": area,
                "year": year,
                "title": title,
                "authors": authors,
                "page_link": (rec.get("page_link") or None),
                "artifact_urls": urls,
            }
        )
    return entries, total


def load_artifinder(
    conf_regex: str | None = None,
    min_year: int | None = DEFAULT_MIN_YEAR,
    local_dir: str | Path | None = None,
) -> ArtiFinderData:
    """Download and parse the ArtiFinder data set.

    Args:
        conf_regex: Optional regex applied to ``f"{conf.lower()}{year}"`` (e.g.
            ``"usenixsec2023"``) to restrict which conference-years are loaded.
        min_year: Earliest conference edition year to include (inclusive).
            Defaults to :data:`DEFAULT_MIN_YEAR` (2017, the artifact-evaluation
            era).  Pass ``None`` to load the full history back to the 2000s.
        local_dir: Optional path to a local ArtiFinder-Data checkout (repo root
            or its ``data`` directory).  Defaults to the ``REPRODB_ARTIFINDER_DIR``
            environment variable.  When present, the loader reads from disk and
            skips all network access.
    """
    base = _resolve_local_dir(local_dir)
    if base is not None:
        logger.info("  ArtiFinder: reading from local checkout %s", base)
        top = _local_venues(base)
    else:
        top = _list_dir(ARTIFINDER_API_BASE)
    if not top:
        logger.warning("  ArtiFinder: no data directories found (network issue?)")
        return ArtiFinderData([], [])

    all_entries: list[dict] = []
    counts: list[dict] = []
    for item in top:
        if item.get("type") != "dir":
            continue
        venue = item.get("name", "")
        mapping = _VENUE_MAP.get(venue.lower())
        if not mapping:
            logger.debug("  ArtiFinder: skipping unmapped venue %r", venue)
            continue
        conf, area = mapping

        files = _local_files(base / venue) if base is not None else _list_dir(f"{ARTIFINDER_API_BASE}/{venue}")
        for f in files:
            if f.get("type") != "file":
                continue
            m = _YEAR_RE.match(f.get("name", ""))
            if not m:
                continue
            year = int(m.group(1))
            if min_year is not None and year < min_year:
                continue
            if conf_regex and not re.search(conf_regex, f"{conf.lower()}{year}"):
                continue
            if base is not None:
                raw = _local_read(base / venue / f["name"])
            else:
                raw = download_file(f"{ARTIFINDER_RAW_BASE}/{venue}/{f['name']}")
            if not raw:
                continue
            year_entries, total = _parse_year_file(conf, area, year, raw)
            all_entries.extend(year_entries)
            if total:
                counts.append(
                    {
                        "conference": conf,
                        "category": area,
                        "year": year,
                        "total_papers": total,
                        "discovered": len(year_entries),
                    }
                )

    logger.info(
        "  ArtiFinder: loaded %d discovered artifacts across %d venues (%d conference-years scanned, min_year=%s)",
        len(all_entries),
        len({e["conference"] for e in all_entries}),
        len(counts),
        min_year,
    )
    source_updated = _local_source_updated(base) if base is not None else _remote_source_updated()
    return ArtiFinderData(all_entries, counts, source_updated)


def load_artifinder_data(
    conf_regex: str | None = None,
    min_year: int | None = DEFAULT_MIN_YEAR,
    local_dir: str | Path | None = None,
) -> list[dict]:
    """Convenience wrapper returning only the entries with discovered artifacts."""
    return load_artifinder(conf_regex, min_year=min_year, local_dir=local_dir).entries
