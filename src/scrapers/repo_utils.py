import json
import logging
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
import yaml

from src.utils.io.cache import (
    _MISSING,
    CACHE_ROOT,
)
from src.utils.io.cache import (
    SECONDS_PER_DAY as _SECONDS_PER_DAY,
)
from src.utils.io.cache import (
    read_cache as _read_cache,
)
from src.utils.io.cache import (
    read_cache_entry as _read_cache_entry,
)
from src.utils.io.cache import (
    refresh_cache_ts as _refresh_cache_ts,
)
from src.utils.io.cache import (
    write_cache as _write_cache,
)

logger = logging.getLogger(__name__)
_SCRIPT_DIR = Path(__file__).resolve().parent
CACHE_DIR = str(CACHE_ROOT)

_KNOWN_DEAD_HOSTS_PATH = _SCRIPT_DIR.parent.parent / "data" / "known_dead_hosts.yaml"
_known_dead_hosts: set[str] | None = None


def _load_known_dead_hosts() -> set[str]:
    """Load the set of hostnames to skip during URL checking."""
    global _known_dead_hosts
    if _known_dead_hosts is None:
        if _KNOWN_DEAD_HOSTS_PATH.exists():
            with _KNOWN_DEAD_HOSTS_PATH.open() as f:
                hosts = yaml.safe_load(f) or []
            _known_dead_hosts = {h.lower() for h in hosts}
        else:
            _known_dead_hosts = set()
    return _known_dead_hosts


CACHE_TTL = _SECONDS_PER_DAY * 30  # 30 days – conference listings & raw file downloads
CACHE_TTL_URL = _SECONDS_PER_DAY * 90  # 90 days – URL existence checks (positive)
CACHE_TTL_URL_NEG = _SECONDS_PER_DAY * 7  # 7 days  – URL non-existence checks (re-check weekly)
CACHE_TTL_STATS = _SECONDS_PER_DAY * 30  # 30 days – GitHub/Zenodo/Figshare stats


def _github_headers():
    """Return headers with GitHub token if available."""
    headers = {"Accept": "application/vnd.github.v3+json"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


def _session_with_retries(retries: int = 3, backoff: float = 1.0, timeout: int = 30) -> requests.Session:
    """Create a requests.Session with automatic retries and timeouts."""
    from src.utils.apis.http import create_session

    return create_session(retries=retries, backoff=backoff, timeout=timeout)


# Module-level session reused across calls (connection pooling)
_session = _session_with_retries()

github_urls = {
    "sys": {
        "base_url": "https://github.com/sysartifacts/sysartifacts.github.io/blob/master/_conferences/",
        "raw_base_url": "https://raw.githubusercontent.com/sysartifacts/sysartifacts.github.io/master/_conferences/",
        "api_url": "https://api.github.com/repos/sysartifacts/sysartifacts.github.io/contents/_conferences/",
    },
    "sec": {
        "base_url": "https://github.com/secartifacts/secartifacts.github.io/blob/master/_conferences/",
        "raw_base_url": "https://raw.githubusercontent.com/secartifacts/secartifacts.github.io/master/_conferences/",
        "api_url": "https://api.github.com/repos/secartifacts/secartifacts.github.io/contents/_conferences/",
    },
}


# Cache functions imported from src.utils.cache


def check_url_cached(url: str, ttl: int = CACHE_TTL_URL) -> bool:
    """Check if a URL exists, with disk caching.

    Returns True/False.  Positive results are cached for ``ttl`` seconds;
    negative results are cached for CACHE_TTL_URL_NEG (shorter) so they
    are re-checked periodically without hammering every run.
    """
    # Skip non-HTTP URLs entirely
    if not url.startswith(("http://", "https://")):
        return False

    # Skip known-dead hosts to avoid expensive DNS/timeout retries
    try:
        host = urlparse(url).hostname
        if host and host.lower() in _load_known_dead_hosts():
            return False
    except Exception:
        pass

    cached = _read_cache(CACHE_DIR, url, ttl=ttl, namespace="url_exists")
    if cached is True:
        return True  # positive hit – trust it
    # Check negative cache (shorter TTL)
    cached_neg = _read_cache(CACHE_DIR, url, ttl=CACHE_TTL_URL_NEG, namespace="url_exists")
    if cached_neg is False:
        return False  # recently confirmed non-existent

    try:
        resp = _session.head(url, allow_redirects=True, timeout=10)
        if resp.status_code == 429:
            time.sleep(10)
            resp = _session.head(url, allow_redirects=True, timeout=10)
        exists = 200 <= resp.status_code < 300
    except requests.exceptions.ConnectionError as e:
        logger.error(f"  Request error for {url}: {e}")
        # DNS failures and connection refused are effectively permanent
        # for old artifact URLs — cache as negative to avoid retrying.
        _write_cache(CACHE_DIR, url, False, namespace="url_exists")
        return False
    except requests.RequestException as e:
        logger.error(f"  Request error for {url}: {e}")
        return False

    _write_cache(CACHE_DIR, url, exists, namespace="url_exists")
    return exists


def cached_github_stats(url: str, ttl: int = CACHE_TTL_STATS) -> dict[str, Any]:
    """Fetch GitHub repo stats with caching, ETags, and rate-limit handling.

    Uses conditional requests (If-None-Match) so that 304 responses do NOT
    count against the GitHub API rate limit.  This effectively makes re-runs
    free for repos whose data hasn't changed.
    """
    cached = _read_cache(CACHE_DIR, url, ttl=ttl, namespace="github_stats")
    if cached is not _MISSING:
        return cached  # dict or None — still fresh

    repo = url.split("github.com/")[1]
    for suffix in ("/tree/", "/blob/", "/pkgs/", "/releases/", "/wiki", "/issues", "/pull/", "/commit/"):
        if suffix in repo:
            repo = repo.split(suffix)[0]
    # Keep only owner/repo (first two path segments)
    parts = repo.strip("/").split("/")
    repo = "/".join(parts[:2]).removesuffix(".git")

    headers = _github_headers()

    # Use stored ETag for conditional request (304 = free, no rate cost)
    entry = _read_cache_entry(CACHE_DIR, url, namespace="github_stats")
    if entry and entry.get("etag"):
        headers["If-None-Match"] = entry["etag"]

    try:
        resp = _session.get(f"https://api.github.com/repos/{repo}", headers=headers, timeout=_session.default_timeout)
        if resp.status_code == 403 and "rate limit" in resp.text.lower():
            reset_time = int(resp.headers.get("X-RateLimit-Reset", 0))
            wait = max(reset_time - int(time.time()), 0) + 5
            logger.info(f"  Rate limited. Waiting {wait}s for reset...")
            time.sleep(wait)
            resp = _session.get(
                f"https://api.github.com/repos/{repo}", headers=headers, timeout=_session.default_timeout
            )

        if resp.status_code == 304 and entry:
            # Data unchanged — refresh timestamp, return cached data (free!)
            _refresh_cache_ts(CACHE_DIR, url, namespace="github_stats")
            return entry.get("body")
        if resp.status_code == 200:
            d = resp.json()
            result = {
                "github_forks": d.get("forks_count", 0),
                "github_stars": d.get("stargazers_count", 0),
                "updated_at": d.get("updated_at", "NA"),
                "created_at": d.get("created_at", "NA"),
                "pushed_at": d.get("pushed_at", "NA"),
                "name": d.get("full_name", "NA"),
                "description": d.get("description", ""),
                "language": d.get("language", ""),
                "license": (d.get("license") or {}).get("spdx_id", ""),
                "topics": d.get("topics", []),
            }
            etag = resp.headers.get("ETag")
            _write_cache(CACHE_DIR, url, result, namespace="github_stats", etag=etag)
            return result
        logger.warning(f"  Could not collect GitHub stats for {url} (HTTP {resp.status_code})")
    except requests.RequestException as e:
        logger.warning(f"  GitHub request error for {url}: {e}")
    result = None
    _write_cache(CACHE_DIR, url, result, namespace="github_stats")
    return result


def _resolve_zenodo_record_id(url: str) -> str | None:
    """Extract a numeric Zenodo record ID from various URL formats.

    Handles: /records/ID, /record/ID, /uploads/ID, /doi/10.5281/zenodo.ID,
    doi.org/10.5281/zenodo.ID, and bare zenodo.ID fallback.
    Returns None for unparseable URLs (e.g. /badge/ links).
    """
    import re

    if "/records/" in url:
        rec = url.split("/records/")[-1]
    elif "/record/" in url:
        rec = url.split("/record/")[-1]
    elif "/uploads/" in url:
        rec = url.split("/uploads/")[-1]
    elif "/doi/10.5281/zenodo." in url:
        rec = url.split("/doi/10.5281/zenodo.")[-1]
    elif "doi.org/10.5281/zenodo." in url:
        rec = url.split("zenodo.")[-1]
    elif "/badge/" in url:
        return None
    elif "zenodo." in url:
        rec = url.split("zenodo.")[-1]
    else:
        return None

    # Strip fragments, query strings, trailing slashes, path suffixes
    rec = rec.split("#")[0].split("?")[0].strip("/")
    # Take only the leading numeric part (e.g. "1234567/files/..." → "1234567")
    m = re.match(r"(\d+)", rec)
    return m.group(1) if m else None


def _resolve_zenodo_doi(url: str) -> str | None:
    """Follow a DOI redirect to find the canonical Zenodo record ID.

    Concept DOIs (e.g. zenodo.15530592) redirect to the latest version
    (e.g. zenodo.org/records/15530593). The API returns 410 for the
    concept ID, so we need the resolved one.
    """
    try:
        resp = _session.head(url, allow_redirects=True, timeout=10)
        final = resp.url
        if "/records/" in final:
            rec = final.split("/records/")[-1].split("?")[0].split("#")[0].strip("/")
            if rec.isdigit():
                return rec
        if "/record/" in final:
            rec = final.split("/record/")[-1].split("?")[0].split("#")[0].strip("/")
            if rec.isdigit():
                return rec
    except requests.RequestException:
        pass
    return None


def _extract_github_urls_from_zenodo(record: dict) -> list[str]:
    """Extract GitHub repository URLs from Zenodo metadata.

    Sources checked (in order):

    1. ``metadata.related_identifiers`` – structured links.
    2. ``metadata.alternate_identifiers`` – alternate structured links.
    3. ``metadata.description`` – free-text HTML/Markdown.
    4. ``metadata.notes`` – free-text additional notes.

    Returns deduplicated base repo URLs (``https://github.com/owner/repo``).
    """
    import re as _re

    urls: list[str] = []
    seen: set[str] = set()
    meta = record.get("metadata", {})

    # 1. related_identifiers
    for ri in meta.get("related_identifiers", []):
        ident = ri.get("identifier", "")
        if "github.com/" not in ident:
            continue
        base = _normalise_github_repo_url(ident)
        if base and base not in seen:
            seen.add(base)
            urls.append(base)

    # 2. alternate_identifiers
    for ai in meta.get("alternate_identifiers", []):
        ident = ai.get("identifier", "")
        if "github.com/" not in ident:
            continue
        base = _normalise_github_repo_url(ident)
        if base and base not in seen:
            seen.add(base)
            urls.append(base)

    # 3. description (free-text HTML)
    for text in (meta.get("description", ""), meta.get("notes", "")):
        for match in _re.findall(r"https?://github\.com/[^\s<\"'&;)]+", text):
            base = _normalise_github_repo_url(match.rstrip(".,;:)"))
            if base and base not in seen:
                seen.add(base)
                urls.append(base)

    return urls


def _extract_github_urls_from_figshare(record: dict) -> list[str]:
    """Extract GitHub repository URLs from Figshare article metadata.

    Sources checked:

    1. ``references`` – list of URL strings.
    2. ``related_materials[].identifier`` – structured links.
    3. ``description`` – free-text HTML/Markdown.

    Returns deduplicated base repo URLs.
    """
    import re as _re

    raw_urls: list[str] = []
    for ref in record.get("references", []):
        if isinstance(ref, str):
            raw_urls.append(ref)
    for rm in record.get("related_materials", []):
        ident = rm.get("identifier", "")
        if isinstance(ident, str):
            raw_urls.append(ident)

    urls: list[str] = []
    seen: set[str] = set()
    for u in raw_urls:
        if "github.com/" not in u:
            continue
        base = _normalise_github_repo_url(u)
        if base and base not in seen:
            seen.add(base)
            urls.append(base)

    # Also scan description for GitHub URLs
    desc = record.get("description", "")
    for match in _re.findall(r"https?://github\.com/[^\s<\"'&;)]+", desc):
        base = _normalise_github_repo_url(match.rstrip(".,;:)"))
        if base and base not in seen:
            seen.add(base)
            urls.append(base)

    return urls


def _normalise_github_repo_url(url: str) -> str | None:
    """Reduce a GitHub URL to ``https://github.com/owner/repo``.

    Handles tree/blob suffixes, .git extensions, and query strings.
    Returns *None* for URLs that don't look like a valid owner/repo path.
    """
    import re

    url = url.split("?")[0].split("#")[0].rstrip("/")
    # Strip .git suffix
    if url.endswith(".git"):
        url = url[:-4]
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)", url)
    if m:
        return f"https://github.com/{m.group(1)}/{m.group(2)}"
    return None


def cached_zenodo_stats(url: str, ttl: int = CACHE_TTL_STATS) -> dict[str, Any]:
    """Fetch Zenodo record stats with caching and 429 retry."""
    cached = _read_cache(CACHE_DIR, url, ttl=ttl, namespace="zenodo_stats")
    if cached is not _MISSING:
        # Stale entries cached before linked_github_urls extraction was
        # added lack the key entirely.  Force a re-fetch so we discover
        # GitHub repos linked from Zenodo metadata.
        if isinstance(cached, dict) and "linked_github_urls" not in cached:
            pass  # fall through to re-fetch
        else:
            return cached

    rec = _resolve_zenodo_record_id(url)
    if rec is None:
        logger.info(f"  Could not parse Zenodo URL {url}")
        return None

    result = None
    try:
        for attempt in range(4):  # up to 3 retries on 429
            resp = _session.get(f"https://zenodo.org/api/records/{rec}", timeout=_session.default_timeout)
            if resp.status_code == 200:
                record = resp.json()
                stats = record.get("stats", {})
                result = {
                    "zenodo_views": stats.get("unique_views", 0),
                    "zenodo_downloads": stats.get("unique_downloads", 0),
                    "updated_at": record.get("updated", ""),
                    "created_at": record.get("created", ""),
                }
                # Extract linked GitHub URLs from related_identifiers.
                # Always store the key (even empty) so the cache can
                # distinguish "checked, no links" from "never checked".
                result["linked_github_urls"] = _extract_github_urls_from_zenodo(record)
                break
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 0))
                wait = max(retry_after, 2 ** (attempt + 1))  # exponential backoff: 2, 4, 8s
                logger.info(f"  Zenodo 429 for {url}, waiting {wait}s (attempt {attempt + 1}/4)")
                time.sleep(wait)
            elif resp.status_code in (404, 410):
                # Concept DOI or superseded version — resolve via redirect
                resolved = _resolve_zenodo_doi(url)
                if resolved and resolved != rec:
                    logger.info(f"  Zenodo {resp.status_code} for record {rec}, resolved DOI to {resolved}")
                    rec = resolved
                    continue  # retry with the resolved ID
                logger.info(f"  Could not collect Zenodo stats for {url} (HTTP {resp.status_code})")
                break
            else:
                logger.info(f"  Could not collect Zenodo stats for {url} (HTTP {resp.status_code})")
                break
    except requests.RequestException as e:
        logger.error(f"  Zenodo request error for {url}: {e}")

    _write_cache(CACHE_DIR, url, result, namespace="zenodo_stats")
    return result


def cached_figshare_stats(url, ttl=CACHE_TTL_STATS):
    """Fetch Figshare article stats with caching."""
    cached = _read_cache(CACHE_DIR, url, ttl=ttl, namespace="figshare_stats")
    if cached is not _MISSING:
        return cached

    clean = url
    if clean.endswith((".v1", ".v2", ".v3", ".v4", ".v5", ".v6", ".v7", ".v8", ".v9")):
        clean = clean[:-3]
    article_id = clean.split("figshare.")[-1]

    views = downloads = -1
    updated = created = "NA"
    linked: list[str] = []
    try:
        r = _session.get(
            f"https://stats.figshare.com/total/views/article/{article_id}", timeout=_session.default_timeout
        )
        if r.status_code == 200:
            views = r.json().get("totals", -1)
        r = _session.get(
            f"https://stats.figshare.com/total/downloads/article/{article_id}", timeout=_session.default_timeout
        )
        if r.status_code == 200:
            downloads = r.json().get("totals", -1)
        r = _session.get(f"https://api.figshare.com/v2/articles/{article_id}", timeout=_session.default_timeout)
        if r.status_code == 200:
            d = r.json()
            updated = d.get("modified_date", "NA")
            created = d.get("created_date", "NA")
            # Extract linked GitHub URLs from references/related_materials
            linked = _extract_github_urls_from_figshare(d)
    except requests.RequestException as e:
        logger.error(f"  Figshare request error for {url}: {e}")

    result = {
        "figshare_views": views,
        "figshare_downloads": downloads,
        "updated_at": updated,
        "created_at": created,
    }
    if linked:
        result["linked_github_urls"] = linked
    _write_cache(CACHE_DIR, url, result, namespace="figshare_stats")
    return result


def _cached_get(url):
    """requests.get with disk cache, ETag conditional requests, and GitHub auth.

    For GitHub API URLs, sends If-None-Match so that 304 responses are free
    (do not count against rate limits).
    """
    cached = _read_cache(CACHE_DIR, url, ttl=CACHE_TTL, namespace="http_get")
    if cached is not _MISSING:
        return cached

    is_github_api = "api.github.com" in url
    headers = _github_headers() if is_github_api else {}

    # Use stored ETag for conditional request if available
    entry = _read_cache_entry(CACHE_DIR, url, namespace="http_get")
    if entry and entry.get("etag"):
        headers["If-None-Match"] = entry["etag"]

    try:
        response = _session.get(url, headers=headers, allow_redirects=True, timeout=_session.default_timeout)
        # Handle rate limiting with retry
        if response.status_code == 403 and "rate limit" in response.text.lower():
            reset_time = int(response.headers.get("X-RateLimit-Reset", 0))
            wait = max(reset_time - int(time.time()), 0) + 5
            logger.info(f"  Rate limited. Waiting {wait}s for reset...")
            time.sleep(wait)
            response = _session.get(url, headers=headers, allow_redirects=True, timeout=_session.default_timeout)

        if response.status_code == 304 and entry:
            # Data unchanged — refresh timestamp, return cached data (free!)
            _refresh_cache_ts(CACHE_DIR, url, namespace="http_get")
            return entry.get("body")

        response.raise_for_status()
    except requests.RequestException as e:
        logger.warning(f"  HTTP request error for {url}: {e}")
        return None
    body = response.text
    etag = response.headers.get("ETag")
    _write_cache(CACHE_DIR, url, body, namespace="http_get", etag=etag)
    return body


def get_conferences_from_prefix(prefix):
    url = github_urls[prefix]["api_url"]
    data = json.loads(_cached_get(url))
    return [item for item in data if item["type"] == "dir"]


def download_file(url):
    return _cached_get(url)
