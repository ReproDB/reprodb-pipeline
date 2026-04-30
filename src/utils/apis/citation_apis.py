"""Shared citation-lookup helpers for OpenAlex, Semantic Scholar, and DOI handling.

Every citation generator should import from here rather than reimplementing
its own API callers, DOI extractors, or rate-limiting constants.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request

import requests

from src.utils.io.cache import CACHE_ROOT, SECONDS_PER_DAY
from src.utils.normalization.conference import normalize_title

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

CITATION_CACHE_DIR = str(CACHE_ROOT / "paper_citations_doi")
CITATION_CACHE_TTL = 30 * SECONDS_PER_DAY  # 30 days

OPENALEX_BASE = "https://api.openalex.org"
S2_BASE = "https://api.semanticscholar.org/graph/v1"
USER_AGENT = "ReproDB-Pipeline/1.0 (https://github.com/reprodb/reprodb-pipeline; mailto:reprodb@example.com)"

# Rate-limiting
OPENALEX_DELAY = 0.12  # seconds between OpenAlex calls
S2_DELAY = 0.12  # seconds between Semantic Scholar calls
S2_MAX_TIMEOUT_FAILURES = 5  # disable S2 after this many timeouts

# ── DOI extraction / normalisation ───────────────────────────────────────────

_DOI_PREFIXES_STRIP = ("https://doi.org/", "http://doi.org/")
DOI_REGEX = re.compile(r"10\.[0-9]{4,9}/[-._;()/:A-Za-z0-9]+")
ARTIFACT_DOI_PREFIXES = (
    "10.5281/zenodo.",  # Zenodo
    "10.6084/m9.figshare.",  # Figshare
)


def extract_paper_doi(paper_url: str | None) -> str:
    """Extract a bare DOI from a paper_url value (``https://doi.org/…`` or bare).

    Returns ``""`` if none found.
    """
    if not paper_url:
        return ""
    doi = paper_url.strip()
    for prefix in _DOI_PREFIXES_STRIP:
        if doi.lower().startswith(prefix):
            doi = doi[len(prefix) :]
            break
    if doi.startswith("10.") and "/" in doi:
        return doi.rstrip(".,);")
    return ""


def extract_doi(url: str | None) -> str:
    """Extract a DOI from an arbitrary URL using regex.

    Handles Zenodo record URLs as a special case.  Returns ``""`` if none found.
    """
    if not url or not isinstance(url, str):
        return ""
    match = DOI_REGEX.search(url)
    if match:
        return match.group(0).rstrip(".,);").lower()
    # Zenodo record URL → DOI
    m = re.search(r"zenodo\.org/(?:record|records)/(\d+)", url, re.I)
    if m:
        return f"10.5281/zenodo.{m.group(1)}".lower()
    return ""


def normalize_doi(value: str) -> str:
    """Normalise a DOI string (strip ``https://doi.org/`` prefix, lowercase)."""
    if not value:
        return ""
    for prefix in _DOI_PREFIXES_STRIP:
        if value.lower().startswith(prefix):
            value = value[len(prefix) :]
            break
    match = DOI_REGEX.search(value)
    if match:
        return match.group(0).rstrip(".,);").lower()
    return ""


def is_artifact_doi(doi: str) -> bool:
    """Return *True* if *doi* belongs to an artifact repository (Zenodo, Figshare)."""
    if not doi:
        return False
    return doi.lower().startswith(ARTIFACT_DOI_PREFIXES)


def cache_key(doi: str) -> str:
    """Deterministic cache key from a DOI or normalised title."""
    return hashlib.sha256(doi.lower().encode()).hexdigest()


# ── HTTP helpers ─────────────────────────────────────────────────────────────


def create_session() -> requests.Session:
    """Return a *requests* session pre-configured with the pipeline User-Agent."""
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    return session


def fetch_json_urllib(url: str, *, timeout: int = 20, headers: dict | None = None) -> dict:
    """GET *url* and parse the JSON response (stdlib ``urllib``)."""
    req_headers = {"User-Agent": USER_AGENT}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        data: dict = json.loads(resp.read().decode("utf-8", "ignore"))
        return data


# ── OpenAlex helpers ─────────────────────────────────────────────────────────


def openalex_lookup(doi: str, session: requests.Session) -> dict | None:
    """Query OpenAlex for a paper by DOI.

    Returns ``{"cited_by_count", "openalex_id", "title"}`` or *None*.
    """
    url = f"{OPENALEX_BASE}/works/https://doi.org/{urllib.parse.quote(doi, safe='')}"
    try:
        resp = session.get(url, timeout=20)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        return {
            "cited_by_count": data.get("cited_by_count"),
            "openalex_id": data.get("id", ""),
            "title": data.get("title", ""),
        }
    except (requests.RequestException, ValueError):
        return None


def openalex_lookup_with_retry(
    doi: str,
    session: requests.Session,
    *,
    max_attempts: int = 4,
    fetch_citing_dois: bool = False,
) -> dict:
    """Query OpenAlex by DOI with retries.  Returns ``{count, citing_dois, error}``."""
    url = f"{OPENALEX_BASE}/works/https://doi.org/{urllib.parse.quote(doi, safe='')}"
    last_err = ""
    for attempt in range(max_attempts):
        try:
            resp = session.get(url, timeout=25)
            if resp.status_code == 404:
                return {"count": None, "citing_dois": [], "error": ""}
            resp.raise_for_status()
            payload = resp.json()
            cited = payload.get("cited_by_count")
            cited_val = cited if isinstance(cited, int) else None
            citing_dois: list[str] = []
            if fetch_citing_dois and cited_val and cited_val > 0:
                openalex_id = payload.get("id", "")
                if openalex_id:
                    work_id = openalex_id.split("/")[-1] if "/" in openalex_id else openalex_id
                    citing_works_url = f"{OPENALEX_BASE}/works?filter=cites:{work_id}"
                    citing_dois = openalex_fetch_citing_dois(citing_works_url, session)
            return {"count": cited_val, "citing_dois": citing_dois, "error": ""}
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            logger.debug("[OpenAlex] retrying DOI %s after error: %s", doi, last_err)
            time.sleep(0.6 * (attempt + 1))
    return {"count": None, "citing_dois": [], "error": last_err}


def openalex_title_search(title: str, session: requests.Session) -> dict | None:
    """Fall back to OpenAlex title search when DOI is unavailable.

    Returns ``{"cited_by_count", "openalex_id", "title"}`` or *None*.
    """
    norm = normalize_title(title)
    if not norm or len(norm) < 10:
        return None
    url = f"{OPENALEX_BASE}/works?filter=title.search:{urllib.parse.quote(norm)}&per_page=3"
    try:
        resp = session.get(url, timeout=20)
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except (requests.RequestException, ValueError):
        return None

    query_words = set(norm.split())
    for work in results:
        work_title = normalize_title(work.get("title", ""))
        work_words = set(work_title.split())
        if not query_words or not work_words:
            continue
        jaccard = len(query_words & work_words) / len(query_words | work_words)
        if jaccard >= 0.6:
            return {
                "cited_by_count": work.get("cited_by_count"),
                "openalex_id": work.get("id", ""),
                "title": work.get("title", ""),
            }
    return None


def openalex_fetch_citing_dois(base_url: str, session: requests.Session) -> list[str]:
    """Paginate through OpenAlex citing-works and collect DOIs."""
    citing_dois: set[str] = set()
    cursor: str | None = "*"
    while cursor:
        url = f"{base_url}&per_page=200&cursor={urllib.parse.quote(cursor, safe='')}"
        try:
            resp = session.get(url, timeout=25)
            resp.raise_for_status()
            payload = resp.json()
        except Exception:
            break
        for work in payload.get("results", []) or []:
            doi_val = work.get("doi") or (work.get("ids", {}) or {}).get("doi") or ""
            norm = normalize_doi(doi_val)
            if norm:
                citing_dois.add(norm)
        cursor = (payload.get("meta", {}) or {}).get("next_cursor")
        if not cursor:
            break
        time.sleep(0.2)
    return sorted(citing_dois)


# ── Semantic Scholar helpers ─────────────────────────────────────────────────


def _s2_headers() -> dict[str, str]:
    """Return S2 request headers, including API key if available."""
    headers: dict[str, str] = {}
    api_key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip()
    if api_key:
        headers["x-api-key"] = api_key
    return headers


def s2_lookup(doi: str, session: requests.Session, *, timeout: int = 8) -> int | None:
    """Query Semantic Scholar for citation count by DOI."""
    url = f"{S2_BASE}/paper/DOI:{urllib.parse.quote(doi, safe='')}?fields=citationCount"
    headers = _s2_headers()
    try:
        resp = session.get(url, timeout=timeout, headers=headers)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        count = resp.json().get("citationCount")
        return count if isinstance(count, int) else None
    except (requests.RequestException, ValueError):
        return None


def s2_lookup_with_retry(
    doi: str,
    session: requests.Session,
    *,
    max_attempts: int | None = None,
    request_timeout: int | None = None,
    fetch_citing_dois: bool = False,
) -> dict:
    """Query S2 by DOI with retries.  Returns ``{count, citing_dois, error}``."""
    if max_attempts is None:
        max_attempts = int(os.environ.get("SEMANTIC_SCHOLAR_MAX_ATTEMPTS", "2"))
    if request_timeout is None:
        request_timeout = int(os.environ.get("SEMANTIC_SCHOLAR_TIMEOUT", "8"))
    headers = _s2_headers()
    paper_id = f"DOI:{doi}"
    base_url = f"{S2_BASE}/paper/{urllib.parse.quote(paper_id, safe='')}"
    last_err = ""
    for attempt in range(max_attempts):
        try:
            resp = session.get(f"{base_url}?fields=citationCount", timeout=request_timeout, headers=headers)
            if resp.status_code == 404:
                return {"count": None, "citing_dois": [], "error": ""}
            resp.raise_for_status()
            cited = resp.json().get("citationCount")
            cited_val = cited if isinstance(cited, int) else None
            citing_dois: list[str] = []
            if fetch_citing_dois:
                citing_dois = s2_fetch_citing_dois(base_url, session, headers=headers)
            return {"count": cited_val, "citing_dois": citing_dois, "error": ""}
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            if attempt + 1 < max_attempts:
                logger.debug("[SemanticScholar] retrying DOI %s after error: %s", doi, last_err)
            time.sleep(0.6 * (attempt + 1))
    return {"count": None, "citing_dois": [], "error": last_err}


def s2_fetch_citing_dois(
    base_url: str,
    session: requests.Session,
    *,
    headers: dict[str, str] | None = None,
) -> list[str]:
    """Paginate through S2 citations and collect DOIs."""
    if headers is None:
        headers = _s2_headers()
    citing_dois: set[str] = set()
    offset = 0
    page_size = 100
    while True:
        url = f"{base_url}/citations?fields=externalIds&limit={page_size}&offset={offset}"
        try:
            resp = session.get(url, timeout=25, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
        except Exception:
            break
        for entry in payload.get("data", []) or []:
            ext_ids = (entry.get("citingPaper", {}) or {}).get("externalIds", {}) or {}
            doi_val = ext_ids.get("DOI", "")
            norm = normalize_doi(doi_val)
            if norm:
                citing_dois.add(norm)
        next_offset = payload.get("next")
        if next_offset is None:
            if len(payload.get("data", []) or []) < page_size:
                break
            offset += page_size
        else:
            offset = next_offset
        time.sleep(0.2)
    return sorted(citing_dois)


def s2_reachable(session: requests.Session | None = None) -> bool:
    """Preflight check — return *True* if Semantic Scholar is reachable."""
    check_url = f"{S2_BASE}/paper/DOI%3A10.1038%2Fnature12373?fields=paperId"
    timeout = int(os.environ.get("SEMANTIC_SCHOLAR_PREFLIGHT_TIMEOUT", "3"))
    try:
        if session is not None:
            session.get(check_url, timeout=timeout).raise_for_status()
        else:
            fetch_json_urllib(check_url, timeout=timeout)
        return True
    except Exception as e:
        logger.info("[SemanticScholar] preflight failed: %s: %s", type(e).__name__, e)
        return False


# ── Composite helper ─────────────────────────────────────────────────────────


def best_citation_count(openalex_count: int | None, s2_count: int | None) -> int | None:
    """Return the maximum of two citation counts, or *None* if both are missing."""
    counts = [c for c in (openalex_count, s2_count) if isinstance(c, int)]
    return max(counts) if counts else None
