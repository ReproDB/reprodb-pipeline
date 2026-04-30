"""Shared helpers for classifying artifact URLs by hosting source.

Provides:
    resolve_doi_prefix(url) — Map a DOI to its repository name (Zenodo, Figshare, …).
    extract_source(url)     — Determine the hosting source of an artifact URL.
    get_artifact_url(artifact, normalise_fn) — Extract the first valid URL from an artifact dict.
    get_artifact_urls(artifact, normalise_fn) — Extract all valid URLs from an artifact dict.
"""

from __future__ import annotations

import re
from collections.abc import Callable

# ── DOI prefix → repository mapping ────────────────────────────────────────

DOI_PREFIX_TO_REPO: dict[str, str] = {
    "10.5281": "Zenodo",
    "10.6084": "Figshare",
    "10.17605": "OSF",  # Open Science Framework
    "10.4121": "Dataverse",  # Royal Data Repository
    "10.60517": "Zenodo",  # Zenodo (alternative prefix)
    "10.7278": "Figshare",  # Figshare (alternative)
    "10.25835": "NIST",  # NIST Data
}

# Keys to check for legacy single-valued URL fields (in priority order)
_LEGACY_URL_KEYS = [
    "repository_url",
    "artifact_url",
    "github_url",
    "second_repository_url",
    "bitbucket_url",
]


def resolve_doi_prefix(url: str) -> str | None:
    """Map a DOI URL to the name of its repository (e.g. ``'Zenodo'``).

    Returns ``None`` if the DOI prefix is unrecognised or the URL contains no DOI.
    """
    doi_match = re.search(
        r"(?:doi\.org/)?(?:https?://doi\.org/)?(10\.\d+(?:[/\.][\w.\-]+)*)",
        url,
    )
    if not doi_match:
        return None

    prefix_parts = doi_match.group(1).split("/")[0].split(".")[0:2]
    doi_prefix = ".".join(prefix_parts)
    return DOI_PREFIX_TO_REPO.get(doi_prefix)


def extract_source(url: str) -> str:
    """Classify an artifact URL by its hosting platform.

    Returns a human-readable label such as ``'GitHub'``, ``'Zenodo'``,
    ``'Figshare'``, ``'OSF'``, ``'GitLab'``, ``'Bitbucket'``, ``'DOI'``,
    ``'Other'``, or ``'unknown'`` when *url* is empty/None.
    """
    if not url:
        return "unknown"

    url_lower = url.lower()

    if "github.com" in url_lower or "github.io" in url_lower:
        return "GitHub"
    if "zenodo" in url_lower or "zenodo.org" in url_lower:
        return "Zenodo"
    if "figshare" in url_lower:
        return "Figshare"
    if "osf.io" in url_lower:
        return "OSF"
    if "gitlab" in url_lower:
        return "GitLab"
    if "bitbucket" in url_lower:
        return "Bitbucket"
    if "archive.org" in url_lower or "arxiv" in url_lower:
        return "Archive"
    if "dataverse" in url_lower:
        return "Dataverse"
    if "doi.org" in url_lower:
        resolved = resolve_doi_prefix(url_lower)
        return resolved if resolved else "DOI"
    return "Other"


def get_artifact_url(artifact: dict, normalise_fn: object = None) -> str | None:
    """Return the first valid URL from *artifact*, or ``None``.

    Parameters
    ----------
    artifact : dict
        An artifact record (as produced by ``parse_results_md``).
    normalise_fn : callable, optional
        A function ``str -> str|None`` that normalises raw URL values.
        Defaults to identity (returns the value as-is).
    """
    normalise: Callable[[str], str | None] = normalise_fn or (lambda v: v or None)  # type: ignore[assignment]

    # New format: ``artifact_urls`` is the canonical list
    urls = artifact.get("artifact_urls", [])
    if isinstance(urls, list):
        for u in urls:
            norm = normalise(u)
            if norm:
                return norm

    # Legacy fallback — single-valued URL fields
    for key in _LEGACY_URL_KEYS:
        val = artifact.get(key, "")
        if isinstance(val, list):
            val = val[0] if val else ""
        norm = normalise(val)
        if norm:
            return norm

    return None


def get_artifact_urls(artifact: dict, normalise_fn=None) -> list[str]:
    """Return *all* valid URLs from *artifact*.

    Parameters
    ----------
    artifact : dict
        An artifact record.
    normalise_fn : callable, optional
        A function ``str -> str|None`` that normalises raw URL values.
    """
    normalise = normalise_fn or (lambda v: v or None)
    urls: list[str] = []

    art_urls = artifact.get("artifact_urls", [])
    if isinstance(art_urls, list):
        for u in art_urls:
            norm = normalise(u)
            if norm:
                urls.append(norm)

    # Legacy fallback — only used when no ``artifact_urls`` were found
    if not urls:
        for key in _LEGACY_URL_KEYS:
            val = artifact.get(key, "")
            if isinstance(val, list):
                val = val[0] if val else ""
            norm = normalise(val)
            if norm and norm not in urls:
                urls.append(norm)

    return urls
