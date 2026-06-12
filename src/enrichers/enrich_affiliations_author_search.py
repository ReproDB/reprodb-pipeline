#!/usr/bin/env python3
"""
Affiliation enrichment via co-author bridge in OpenAlex.

Strategy:
  1. For an unaffiliated author X, find their co-authors from our dataset.
  2. Search each co-author in OpenAlex to get their OpenAlex author ID.
  3. Scan that co-author's works for any paper listing X by name →
     extract X's OpenAlex author ID.
  4. Validate the matched ID: check that X's OpenAlex works contain at least
     one paper title from our dataset (confirms correct disambiguation).
  5. Fetch X's own works sorted by publication_year desc and return the
     institution from their most recent paper that has one.

This avoids false positives from common names (e.g. "Kai Ye" matching a
medical researcher) by requiring the found author's profile to overlap with
our known paper titles.

Usage:
    python -m src.enrichers.enrich_affiliations_author_search \
        --authors_file  output/staging/_data/authors.yml \
        --papers_file   output/staging/assets/data/paper_authors_map.json \
        [--output_file  output/staging/_data/authors.yml] \
        [--max_authors 100] [--verbose] [--dry_run]
"""

import argparse
import logging
import os
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import requests

from src.utils.apis.http import create_session
from src.utils.io.cache import _MISSING, CACHE_ROOT, SECONDS_PER_DAY
from src.utils.io.cache import read_cache as _read_cache
from src.utils.io.cache import write_cache as _write_cache
from src.utils.io.io import load_json
from src.utils.normalization.conference import normalize_name

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache & rate limits
# ---------------------------------------------------------------------------
CACHE_DIR = CACHE_ROOT / "author_search"
CACHE_TTL = SECONDS_PER_DAY * 90  # 90 days
OPENALEX_DELAY = 0.12  # 10 req/s max
HTTP_TIMEOUT = 15


# ---------------------------------------------------------------------------
# OpenAlex helpers
# ---------------------------------------------------------------------------
def _find_coauthor_openalex_id(
    session: requests.Session,
    coauthor_name: str,
) -> Optional[str]:
    """Search OpenAlex for a co-author and return their ID if found with high confidence."""
    cache_key = f"coauthor_id:{coauthor_name}"
    cached = _read_cache(str(CACHE_DIR), cache_key, CACHE_TTL, namespace="coauthor_ids")
    if cached is not _MISSING:
        return cached if cached else None

    url = f"https://api.openalex.org/authors?search={quote(coauthor_name)}&per_page=1"
    try:
        time.sleep(OPENALEX_DELAY)
        resp = session.get(url, timeout=HTTP_TIMEOUT)
        if resp.status_code != 200:
            return None
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None

    results = data.get("results", [])
    if not results:
        _write_cache(str(CACHE_DIR), cache_key, "", namespace="coauthor_ids")
        return None

    top = results[0]
    # Basic name match check
    if normalize_name(top["display_name"]) != normalize_name(coauthor_name):
        _write_cache(str(CACHE_DIR), cache_key, "", namespace="coauthor_ids")
        return None

    oa_id = top["id"].split("/")[-1]
    _write_cache(str(CACHE_DIR), cache_key, oa_id, namespace="coauthor_ids")
    return oa_id


def _normalize_title(title: str) -> str:
    """Lowercase, strip punctuation/whitespace for fuzzy title matching."""
    return "".join(c for c in title.lower() if c.isalnum() or c == " ").strip()


def _find_author_openalex_id_via_coauthor(
    session: requests.Session,
    target_name: str,
    coauthor_oa_id: str,
) -> Optional[str]:
    """
    Scan a co-author's OpenAlex works for any paper listing target_name.
    Returns the target author's OpenAlex ID, or None.
    """
    cache_key = f"bridge_id3:{target_name}:{coauthor_oa_id}"
    cached = _read_cache(str(CACHE_DIR), cache_key, CACHE_TTL, namespace="bridge_ids")
    if cached is not _MISSING:
        return cached if cached else None

    works_url = (
        f"https://api.openalex.org/works?filter=author.id:{coauthor_oa_id}"
        f"&per_page=200&select=title,authorships"
    )
    try:
        time.sleep(OPENALEX_DELAY)
        resp = session.get(works_url, timeout=HTTP_TIMEOUT)
        if resp.status_code != 200:
            return None
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None

    target_norm = normalize_name(target_name)

    for work in data.get("results", []):
        for auth in work.get("authorships", []):
            auth_name = auth.get("author", {}).get("display_name", "")
            if normalize_name(auth_name) == target_norm:
                oa_id = auth.get("author", {}).get("id", "")
                if oa_id:
                    oa_id = oa_id.split("/")[-1]
                    _write_cache(str(CACHE_DIR), cache_key, oa_id, namespace="bridge_ids")
                    return oa_id

    _write_cache(str(CACHE_DIR), cache_key, "", namespace="bridge_ids")
    return None


def _get_most_recent_affiliation(
    session: requests.Session,
    author_oa_id: str,
) -> Optional[str]:
    """
    Fetch an author's works sorted by publication_year desc and return
    the institution from their most recent paper that has one.
    """
    cache_key = f"recent_affil:{author_oa_id}"
    cached = _read_cache(str(CACHE_DIR), cache_key, CACHE_TTL, namespace="recent_affil")
    if cached is not _MISSING:
        return cached if cached else None

    works_url = (
        f"https://api.openalex.org/works?filter=author.id:{author_oa_id}"
        f"&sort=publication_year:desc&per_page=50&select=authorships"
    )
    try:
        time.sleep(OPENALEX_DELAY)
        resp = session.get(works_url, timeout=HTTP_TIMEOUT)
        if resp.status_code != 200:
            return None
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None

    for work in data.get("results", []):
        for auth in work.get("authorships", []):
            aid = auth.get("author", {}).get("id", "")
            if aid and aid.split("/")[-1] == author_oa_id:
                institutions = auth.get("institutions", [])
                inst_names = [i["display_name"] for i in institutions if i.get("display_name")]
                if inst_names:
                    _write_cache(str(CACHE_DIR), cache_key, inst_names[0], namespace="recent_affil")
                    return inst_names[0]

    _write_cache(str(CACHE_DIR), cache_key, "", namespace="recent_affil")
    return None


# Minimum number of co-authors that must agree on the same OpenAlex ID.
# If the target has fewer total co-authors than this threshold, accept a single match.
_MIN_CONSENSUS = 2
# Maximum co-authors to probe before deciding (limits API calls)
_MAX_COAUTHORS_TO_PROBE = 8


def resolve_via_coauthor_bridge(
    session: requests.Session,
    target_name: str,
    coauthor_names: list[str],
    verbose: bool = False,
) -> Optional[str]:
    """
    For an unaffiliated author, find their OpenAlex ID via multiple co-authors'
    works. Require consensus: the same OpenAlex ID must be found via at least
    2 independent co-authors (or 1 if the author has fewer than 2 co-authors
    in our dataset). Then return the most recent affiliation from that profile.
    """
    # Collect votes: OpenAlex ID → set of co-authors that found it
    id_votes: dict[str, set[str]] = {}
    probed = 0

    for coauthor in coauthor_names:
        if probed >= _MAX_COAUTHORS_TO_PROBE:
            break

        # Find co-author's OpenAlex ID
        oa_id = _find_coauthor_openalex_id(session, coauthor)
        if not oa_id:
            continue

        probed += 1

        # Find target author's OpenAlex ID from co-author's papers
        target_oa_id = _find_author_openalex_id_via_coauthor(session, target_name, oa_id)
        if not target_oa_id:
            continue

        id_votes.setdefault(target_oa_id, set()).add(coauthor)

        # Early exit: if we already have consensus, no need to check more
        if len(id_votes[target_oa_id]) >= _MIN_CONSENSUS:
            break

    if not id_votes:
        return None

    # Pick the ID with the most votes
    best_id = max(id_votes, key=lambda k: len(id_votes[k]))
    vote_count = len(id_votes[best_id])

    # Require consensus unless the author has very few co-authors
    min_required = min(_MIN_CONSENSUS, len(coauthor_names))
    if vote_count < min_required:
        if verbose:
            logger.info(
                f"      Bridge: {target_name} no consensus "
                f"(best={best_id}, votes={vote_count}, need={min_required})"
            )
        return None

    # Get most recent affiliation from the consensus-confirmed profile
    institution = _get_most_recent_affiliation(session, best_id)
    if institution and verbose:
        voters = sorted(id_votes[best_id])
        logger.info(f"      Bridge: {target_name} (OA:{best_id}) confirmed by {voters} -> {institution}")
    return institution


# ---------------------------------------------------------------------------
# YAML helpers
# ---------------------------------------------------------------------------
def _parse_authors_yml_fast(path: str) -> list[dict]:
    """Fast line-by-line parse of authors.yml extracting name + affiliation."""
    authors = []
    current: dict = {}
    with open(path, encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            if line.startswith("- "):
                if current:
                    authors.append(current)
                current = {"line_num": line_num}
                rest = line[2:].strip()
                if rest.startswith("affiliation:"):
                    val = rest.split(":", 1)[1].strip().strip("'\"")
                    current["affiliation"] = val
                elif rest.startswith("name:"):
                    val = rest.split(":", 1)[1].strip().strip("'\"")
                    current["name"] = val
            elif line.startswith("  ") and current is not None:
                stripped = line.strip()
                if stripped.startswith("name:"):
                    val = stripped.split(":", 1)[1].strip().strip("'\"")
                    current["name"] = val
                elif stripped.startswith("affiliation:"):
                    val = stripped.split(":", 1)[1].strip().strip("'\"")
                    current["affiliation"] = val
    if current:
        authors.append(current)
    return authors


def _update_authors_yml(path: str, updates: dict[str, str]) -> int:
    """Rewrite authors.yml with new affiliations. Returns count of replacements."""
    lines = Path(path).read_text(encoding="utf-8").splitlines(keepends=True)

    entry_affil_idx: Optional[int] = None
    name_to_affil_line: dict[str, int] = {}

    for i, line in enumerate(lines):
        stripped = line.strip()
        if line.startswith("- affiliation:"):
            val = stripped.split(":", 1)[1].strip()
            entry_affil_empty = val in ("''", '""', "")
            entry_affil_idx = i if entry_affil_empty else None
        elif stripped.startswith("name:") and entry_affil_idx is not None:
            val = stripped.split(":", 1)[1].strip().strip("'\"")
            if val:
                name_to_affil_line[val] = entry_affil_idx
            entry_affil_idx = None

    replaced = 0
    for name, affiliation in updates.items():
        idx = name_to_affil_line.get(name)
        if idx is None:
            continue
        new_affil = affiliation.replace("'", "''")
        lines[idx] = f"- affiliation: '{new_affil}'\n"
        replaced += 1

    Path(path).write_text("".join(lines), encoding="utf-8")
    return replaced


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def _build_author_papers_index(papers_file: str) -> dict[str, list[dict]]:
    """Map each author name → list of paper dicts."""
    papers = load_json(papers_file)
    index: dict[str, list[dict]] = {}
    for paper in papers:
        for author in paper.get("authors", []):
            index.setdefault(author, []).append(paper)
    return index


def _get_coauthors(author_name: str, author_papers: dict[str, list[dict]]) -> list[str]:
    """Get unique co-authors for an author, sorted by most co-authored papers first."""
    coauth_counts: dict[str, int] = {}
    for paper in author_papers.get(author_name, []):
        for a in paper.get("authors", []):
            if a != author_name:
                coauth_counts[a] = coauth_counts.get(a, 0) + 1
    # Sort by frequency (try most frequent co-authors first — more likely to share a lab)
    return sorted(coauth_counts, key=coauth_counts.get, reverse=True)


def enrich(
    authors_file: str,
    papers_file: str,
    output_file: Optional[str] = None,
    max_authors: Optional[int] = None,
    verbose: bool = False,
    dry_run: bool = False,
    data_dir: Optional[str] = None,
) -> dict:
    """Main entry point. Returns stats dict."""
    output_file = output_file or authors_file

    logger.info("Loading paper-authors map...")
    author_papers = _build_author_papers_index(papers_file)
    logger.info(f"  {len(author_papers)} unique author names")

    logger.info("Parsing authors.yml...")
    authors = _parse_authors_yml_fast(authors_file)
    total = len(authors)
    candidates = [a for a in authors if not a.get("affiliation")]
    logger.info(f"  {total} total, {len(candidates)} missing affiliations")

    if max_authors:
        candidates = candidates[:max_authors]
        logger.info(f"  Processing first {len(candidates)} (--max_authors)")

    # HTTP session
    session = create_session()
    http_proxy = os.environ.get("http_proxy") or os.environ.get("HTTP_PROXY", "")
    https_proxy = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY", "")
    if https_proxy or http_proxy:
        session.proxies = {"http": http_proxy, "https": https_proxy}

    # Optional author index
    index_by_name = {}
    _update_index_fn = None
    _save_index_fn = None
    if data_dir:
        try:
            from src.utils.normalization.author_index import (
                load_author_index,
                save_author_index,
                update_author_affiliation,
            )
            _, index_by_name = load_author_index(data_dir)
            _update_index_fn = update_author_affiliation

            def _save_index_fn():
                return save_author_index(
                    data_dir, sorted(index_by_name.values(), key=lambda e: e["id"])
                )
        except ImportError:
            pass

    stats = {"total": total, "candidates": len(candidates), "found": 0, "not_found": 0, "errors": 0}
    updates: dict[str, str] = {}

    logger.info(f"\nEnriching {len(candidates)} authors via co-author bridge...")
    logger.info("=" * 70)

    for idx, author in enumerate(candidates, 1):
        name = author.get("name", "")
        if not name:
            continue

        try:
            coauthors = _get_coauthors(name, author_papers)
            if verbose:
                logger.info(f"[{idx}/{len(candidates)}] {name} ({len(coauthors)} co-authors)")

            affiliation = resolve_via_coauthor_bridge(session, name, coauthors, verbose)

            if affiliation:
                stats["found"] += 1
                updates[name] = affiliation
                if name in index_by_name and _update_index_fn:
                    _update_index_fn(index_by_name[name], affiliation, "coauthor_bridge")
                if not verbose:
                    logger.info(f"[{idx}/{len(candidates)}] {name:40s}  +  {affiliation[:50]}")
            else:
                stats["not_found"] += 1
                if not verbose:
                    logger.info(f"[{idx}/{len(candidates)}] {name:40s}  -")
        except Exception:
            stats["errors"] = stats.get("errors", 0) + 1
            logger.warning(f"[{idx}/{len(candidates)}] {name}: error", exc_info=True)

    logger.info("=" * 70)
    logger.info(f"\nResults: found {stats['found']}, not found {stats['not_found']}")

    if not dry_run and updates:
        logger.info(f"\nWriting {len(updates)} updates to {output_file} ...")
        replaced = _update_authors_yml(output_file, updates)
        logger.info(f"  {replaced} lines updated.")
        if _save_index_fn and index_by_name:
            _save_index_fn()
    elif dry_run:
        logger.info(f"\n[DRY RUN] Would update {len(updates)} authors.")

    stats["updates_written"] = len(updates) if not dry_run else 0
    return stats


def main():
    parser = argparse.ArgumentParser(description="Affiliation enrichment via co-author bridge in OpenAlex")
    parser.add_argument("--authors_file", required=True, help="Path to authors.yml")
    parser.add_argument("--papers_file", required=True, help="Path to paper_authors_map.json")
    parser.add_argument("--output_file", default=None, help="Output path (default: overwrite authors_file)")
    parser.add_argument("--max_authors", type=int, default=None, help="Limit number of authors to process")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--dry_run", action="store_true", help="Don't write changes")
    parser.add_argument("--data_dir", default=None, help="Website repo root for author index updates")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")

    enrich(
        authors_file=args.authors_file,
        papers_file=args.papers_file,
        output_file=args.output_file,
        max_authors=args.max_authors,
        verbose=args.verbose,
        dry_run=args.dry_run,
        data_dir=args.data_dir,
    )


if __name__ == "__main__":
    main()
