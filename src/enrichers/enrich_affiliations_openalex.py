#!/usr/bin/env python3
"""
Paper-based affiliation enrichment using OpenAlex, Semantic Scholar, CrossRef, and DBLP.

Disambiguation strategy: instead of searching by author name alone (which
returns wrong matches for common names), we look up the author's *known papers*
by title, then extract the affiliation from the matching author entry on that
specific paper.

Sources queried (in priority order):
  1. CrossRef  – DOI lookup → author → affiliation (highest precision)
  2. OpenAlex  – works search by title → authorships → institutions
  3. Semantic Scholar – paper title match → authors → affiliations
  4. CrossRef  – works search by title → author → affiliation

Usage:
    python -m src.enrichers.enrich_affiliations_openalex \
        --authors_file  ../_data/authors.yml \
        --papers_file   ../assets/data/paper_authors_map.json \
        --output_file   ../_data/authors.yml \
        [--max_authors 100] [--verbose]
"""

import argparse
import logging
import os
import re
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
from src.utils.normalization.conference import normalize_name, normalize_title

logger = logging.getLogger(__name__)
# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent  # reprodb-pipeline/
CACHE_DIR = CACHE_ROOT / "openalex"
CACHE_TTL = SECONDS_PER_DAY * 90  # 90 days

REQUEST_DELAY = 0.15  # polite delay between API calls
OPENALEX_DELAY = 0.12  # OpenAlex asks for 10 req/s max
CROSSREF_DELAY = 0.25
S2_DELAY = 0.12  # Semantic Scholar: 100 req/s with key, 1 req/s without
HTTP_TIMEOUT = 10  # seconds per request (prevent hangs)


# Cache functions imported from src.utils.cache


# ---------------------------------------------------------------------------
# Name normalisation helpers
# ---------------------------------------------------------------------------
# Core name/title normalisation is provided by src.utils.conference:
#   normalize_name()  – cross-source person-name matching
#   normalize_title() – paper-title matching


def _last_name(name: str) -> str:
    parts = normalize_name(name).split()
    return parts[-1] if parts else ""


def _names_match(query: str, candidate: str) -> bool:
    """Fuzzy two-way name match (last-name required, first name prefix ok)."""
    q = normalize_name(query)
    c = normalize_name(candidate)
    if q == c:
        return True
    q_parts = q.split()
    c_parts = c.split()
    if not q_parts or not c_parts:
        return False
    # Last names must match
    if q_parts[-1] != c_parts[-1]:
        return False
    # At least first initial should match
    return q_parts[0][0] == c_parts[0][0]


# ---------------------------------------------------------------------------
# OpenAlex: look up paper by title, extract author affiliation
# ---------------------------------------------------------------------------
def _openalex_affiliation_by_title(
    session: requests.Session,
    author_name: str,
    title: str,
    verbose: bool = False,
) -> Optional[str]:
    """Search OpenAlex for a paper title and return the matching author's affiliation."""
    cache_key = f"oa_title:{normalize_title(title)}:{normalize_name(author_name)}"
    cached = _read_cache(str(CACHE_DIR), cache_key, CACHE_TTL, "openalex_title")
    if cached is not _MISSING:
        return cached if cached else None

    clean_title = re.sub(r"\.$", "", title).strip()  # remove trailing period
    url = f"https://api.openalex.org/works?search={quote(clean_title)}&per_page=3&select=title,authorships"

    try:
        time.sleep(OPENALEX_DELAY)
        resp = session.get(url, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        for work in data.get("results", []):
            work_title = (work.get("title") or "").strip().rstrip(".")
            if normalize_title(work_title) != normalize_title(clean_title):
                continue
            # Found the paper – look for the author
            for authorship in work.get("authorships", []):
                raw = authorship.get("raw_author_name", "") or ""
                display = (authorship.get("author") or {}).get("display_name", "")
                if _names_match(author_name, raw) or _names_match(author_name, display):
                    institutions = authorship.get("institutions", [])
                    if institutions:
                        inst_name = institutions[0].get("display_name", "")
                        if inst_name:
                            if verbose:
                                logger.info(f"      OA-title: {inst_name}")
                            _write_cache(str(CACHE_DIR), cache_key, inst_name, "openalex_title")
                            return inst_name
    except Exception as e:
        if verbose:
            logger.error(f"      OA-title error: {e}")

    _write_cache(str(CACHE_DIR), cache_key, "", "openalex_title")
    return None


# ---------------------------------------------------------------------------
# CrossRef: look up paper by title, extract author affiliation
# ---------------------------------------------------------------------------
def _crossref_affiliation_by_title(
    session: requests.Session,
    author_name: str,
    title: str,
    verbose: bool = False,
) -> Optional[str]:
    """Search CrossRef for a paper title and return the matching author's affiliation."""
    cache_key = f"cr_title:{normalize_title(title)}:{normalize_name(author_name)}"
    cached = _read_cache(str(CACHE_DIR), cache_key, CACHE_TTL, "crossref_title")
    if cached is not _MISSING:
        return cached if cached else None

    clean_title = re.sub(r"\.$", "", title).strip()
    url = f"https://api.crossref.org/works?query.bibliographic={quote(clean_title)}&rows=3&select=title,author"

    try:
        time.sleep(CROSSREF_DELAY)
        resp = session.get(url, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        for item in data.get("message", {}).get("items", []):
            item_titles = item.get("title", [])
            item_title = item_titles[0] if item_titles else ""
            if normalize_title(item_title) != normalize_title(clean_title):
                continue
            for author in item.get("author", []):
                family = author.get("family", "")
                given = author.get("given", "")
                full = f"{given} {family}".strip()
                if _names_match(author_name, full):
                    for aff in author.get("affiliation", []):
                        name_str = aff.get("name", "")
                        if name_str:
                            if verbose:
                                logger.info(f"      CR-title: {name_str}")
                            _write_cache(str(CACHE_DIR), cache_key, name_str, "crossref_title")
                            return name_str
    except Exception as e:
        if verbose:
            logger.error(f"      CR-title error: {e}")

    _write_cache(str(CACHE_DIR), cache_key, "", "crossref_title")
    return None


# ---------------------------------------------------------------------------
# CrossRef: look up paper by DOI, extract author affiliation
# ---------------------------------------------------------------------------
def _is_real_doi(doi_url: str) -> bool:
    """Check if a URL is an actual DOI (not a conference presentation URL)."""
    if not doi_url:
        return False
    # Actual DOIs contain 10.XXXX/ pattern
    doi_part = doi_url.replace("https://doi.org/", "").replace("http://doi.org/", "")
    return bool(re.match(r"10\.\d{4,}/", doi_part))


def _crossref_affiliation_by_doi(
    session: requests.Session,
    author_name: str,
    doi_url: str,
    verbose: bool = False,
) -> Optional[str]:
    """Look up a specific DOI in CrossRef and extract the matching author's affiliation."""
    if not doi_url or not _is_real_doi(doi_url):
        return None

    cache_key = f"cr_doi:{doi_url}:{normalize_name(author_name)}"
    cached = _read_cache(str(CACHE_DIR), cache_key, CACHE_TTL, "crossref_doi")
    if cached is not _MISSING:
        return cached if cached else None

    # Normalise DOI URL → DOI string
    doi = doi_url.replace("https://doi.org/", "").replace("http://doi.org/", "")
    url = f"https://api.crossref.org/works/{quote(doi, safe='')}"

    try:
        time.sleep(CROSSREF_DELAY)
        resp = session.get(url, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        item = data.get("message", {})
        for author in item.get("author", []):
            family = author.get("family", "")
            given = author.get("given", "")
            full = f"{given} {family}".strip()
            if _names_match(author_name, full):
                for aff in author.get("affiliation", []):
                    name_str = aff.get("name", "")
                    if name_str:
                        if verbose:
                            logger.info(f"      CR-doi: {name_str}")
                        _write_cache(str(CACHE_DIR), cache_key, name_str, "crossref_doi")
                        return name_str
    except Exception as e:
        if verbose:
            logger.error(f"      CR-doi error: {e}")

    _write_cache(str(CACHE_DIR), cache_key, "", "crossref_doi")
    return None


# ---------------------------------------------------------------------------
# Semantic Scholar: search paper by title, extract author affiliation
# ---------------------------------------------------------------------------
def _s2_affiliation_by_title(
    session: requests.Session,
    author_name: str,
    title: str,
    verbose: bool = False,
) -> Optional[str]:
    """Search Semantic Scholar for a paper by title and return the matching author's affiliation."""
    cache_key = f"s2_title:{normalize_title(title)}:{normalize_name(author_name)}"
    cached = _read_cache(str(CACHE_DIR), cache_key, CACHE_TTL, "s2_title")
    if cached is not _MISSING:
        return cached if cached else None

    clean_title = re.sub(r"\.$", "", title).strip()
    url = f"https://api.semanticscholar.org/graph/v1/paper/search/match?query={quote(clean_title)}&fields=title,authors.name,authors.affiliations"

    headers = {}
    s2_api_key = os.environ.get("S2_API_KEY", "")
    if s2_api_key:
        headers["x-api-key"] = s2_api_key

    try:
        time.sleep(S2_DELAY)
        resp = session.get(url, timeout=HTTP_TIMEOUT, headers=headers)
        if resp.status_code == 404:
            # "Title match not found"
            _write_cache(str(CACHE_DIR), cache_key, "", "s2_title")
            return None
        resp.raise_for_status()
        data = resp.json()

        for paper in data.get("data", []):
            paper_title = (paper.get("title") or "").strip().rstrip(".")
            if normalize_title(paper_title) != normalize_title(clean_title):
                continue
            for author in paper.get("authors", []):
                display = author.get("name", "")
                if _names_match(author_name, display):
                    affiliations = author.get("affiliations") or []
                    if affiliations:
                        inst_name = affiliations[0]
                        if inst_name:
                            if verbose:
                                logger.info(f"      S2-title: {inst_name}")
                            _write_cache(str(CACHE_DIR), cache_key, inst_name, "s2_title")
                            return inst_name
    except Exception as e:
        if verbose:
            logger.error(f"      S2-title error: {e}")

    _write_cache(str(CACHE_DIR), cache_key, "", "s2_title")
    return None


# ---------------------------------------------------------------------------
# Multi-source enrichment for one author
# ---------------------------------------------------------------------------
def find_affiliation_for_author(
    session: requests.Session,
    author_name: str,
    papers: list[dict],
    verbose: bool = False,
) -> tuple[Optional[str], str]:
    """
    Try to find affiliation using paper-based disambiguation.

    Strategy (two passes):
      Pass 1 – papers with real DOIs (highest confidence):
        CrossRef DOI lookup for exact author match.
      Pass 2 – title-based search across papers (up to 5):
        OpenAlex, Semantic Scholar, then CrossRef title search.
    Papers are tried newest-first for the most current affiliation.
    Note: DBLP affiliations are already applied in step 0 (generate_author_stats).
    """
    sorted_papers = sorted(papers, key=lambda p: p.get("year", 0), reverse=True)

    # Pass 1: DOI-based lookups (most precise, no ambiguity)
    for paper in sorted_papers:
        doi_url = paper.get("doi_url", "")
        if doi_url and _is_real_doi(doi_url):
            if verbose:
                logger.info(f"    DOI: {paper.get('title', '')[:55]}... ({paper.get('year', '?')})")
            affil = _crossref_affiliation_by_doi(session, author_name, doi_url, verbose)
            if affil:
                return affil, "crossref_doi"

    # Pass 2: title-based lookups (try up to 5 papers, skip 2025 first pass)
    papers_tried = 0
    for paper in sorted_papers:
        if papers_tried >= 5:
            break
        title = paper.get("title", "")
        if not title:
            continue
        papers_tried += 1

        if verbose:
            logger.info(f"    Title: {title[:55]}... ({paper.get('year', '?')})")

        # OpenAlex by title (best coverage)
        affil = _openalex_affiliation_by_title(session, author_name, title, verbose)
        if affil:
            return affil, "openalex_title"

        # Semantic Scholar by title
        affil = _s2_affiliation_by_title(session, author_name, title, verbose)
        if affil:
            return affil, "s2_title"

        # CrossRef by title
        affil = _crossref_affiliation_by_title(session, author_name, title, verbose)
        if affil:
            return affil, "crossref_title"

    return None, ""


# ---------------------------------------------------------------------------
# Build a name → papers index from paper_authors_map.json
# ---------------------------------------------------------------------------
def _build_author_papers_index(papers_file: str) -> dict[str, list[dict]]:
    """Map each author name → list of paper dicts (with title, year, doi_url)."""
    papers = load_json(papers_file)

    index: dict[str, list[dict]] = {}
    for paper in papers:
        for author in paper.get("authors", []):
            index.setdefault(author, []).append(paper)
    return index


# ---------------------------------------------------------------------------
# YAML helpers (line-by-line to avoid slow full-parse)
# ---------------------------------------------------------------------------
def _parse_authors_yml_fast(path: str) -> list[dict]:
    """
    Fast line-by-line parse of authors.yml extracting only name + affiliation.
    Returns list of {"name": ..., "affiliation": ..., "line_num": ...}.
    """
    authors = []
    current: dict = {}
    with open(path, encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            if line.startswith("- "):
                if current:
                    authors.append(current)
                current = {"line_num": line_num}
                # Check if this line itself has a field
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
    """
    Rewrite authors.yml, replacing affiliation for names in *updates*.
    Returns count of replacements made.

    Because 'affiliation:' appears *before* 'name:' in each YAML entry,
    we do two passes:
      1. Scan to build a map: author_name → line index of their affiliation line.
      2. Rewrite affiliation lines for names present in *updates*.
    """
    lines = Path(path).read_text(encoding="utf-8").splitlines(keepends=True)

    # Pass 1: collect (affiliation_line_idx, name) pairs per entry
    #   Each entry starts with "- affiliation: ..."
    entry_affil_idx: Optional[int] = None
    entry_affil_empty = False
    name_to_affil_line: dict[str, int] = {}

    for i, line in enumerate(lines):
        stripped = line.strip()
        # Detect entry boundary: "- affiliation: ..."
        if line.startswith("- affiliation:"):
            val = stripped.split(":", 1)[1].strip()
            entry_affil_empty = val in ("''", '""', "")
            entry_affil_idx = i if entry_affil_empty else None
        elif stripped.startswith("name:") and entry_affil_idx is not None:
            val = stripped.split(":", 1)[1].strip().strip("'\"")
            if val:
                name_to_affil_line[val] = entry_affil_idx
            entry_affil_idx = None

    # Pass 2: rewrite the affiliation lines for matching authors
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
# Main enrichment loop
# ---------------------------------------------------------------------------
def enrich(
    authors_file: str,
    papers_file: str,
    output_file: Optional[str] = None,
    max_authors: Optional[int] = None,
    verbose: bool = False,
    dry_run: bool = False,
    recheck: bool = False,
    data_dir: Optional[str] = None,
) -> dict:
    """
    Main entry point.  Reads authors.yml and paper_authors_map.json,
    enriches missing affiliations, writes back.

    Returns stats dict.
    """
    output_file = output_file or authors_file

    # Build paper index
    logger.info("Loading paper-authors map...")
    author_papers = _build_author_papers_index(papers_file)
    logger.info(f"  {len(author_papers)} unique author names across papers")

    # Parse authors.yml (fast)
    logger.info("Parsing authors.yml (fast line scan)...")
    authors = _parse_authors_yml_fast(authors_file)
    total = len(authors)
    if recheck:
        candidates = list(authors)
        logger.info(f"  {total} total authors, rechecking ALL affiliations")
    else:
        candidates = [a for a in authors if not a.get("affiliation")]
        logger.info(f"  {total} total authors, {len(candidates)} missing affiliations")

    if max_authors:
        candidates = candidates[:max_authors]
        logger.info(f"  Processing first {len(candidates)} (--max_authors)")

    # HTTP session
    session = create_session()
    http_proxy = os.environ.get("http_proxy") or os.environ.get("HTTP_PROXY", "")
    https_proxy = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY", "")
    if https_proxy or http_proxy:
        session.proxies = {"http": http_proxy, "https": https_proxy}
        logger.info(f"  Using proxy: {https_proxy or http_proxy}")

    # Load author index
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
                return save_author_index(data_dir, sorted(index_by_name.values(), key=lambda e: e["id"]))

            if index_by_name:
                logger.info(f"  Loaded author index ({len(index_by_name)} entries)")
        except ImportError:
            logger.debug("Optional module not available, skipping enrichment")

    stats = {
        "total": total,
        "candidates": len(candidates),
        "found": 0,
        "not_found": 0,
        "by_source": {},
        "errors": 0,
    }

    updates: dict[str, str] = {}

    logger.info(f"\nEnriching {len(candidates)} authors...")
    logger.info("=" * 70)

    for idx, author in enumerate(candidates, 1):
        name = author.get("name", "")
        if not name:
            continue

        try:
            papers = author_papers.get(name, [])
            paper_count = len(papers)

            if verbose:
                logger.info(f"[{idx}/{len(candidates)}] {name}  ({paper_count} papers)")

            affiliation, source = find_affiliation_for_author(session, name, papers, verbose=verbose)

            if affiliation:
                stats["found"] += 1
                stats["by_source"][source] = stats["by_source"].get(source, 0) + 1
                updates[name] = affiliation
                # Update author index
                if name in index_by_name and _update_index_fn:
                    _update_index_fn(index_by_name[name], affiliation, source)
                if not verbose:
                    logger.info(f"[{idx}/{len(candidates)}] {name:40s}  +  {affiliation[:50]}  ({source})")
            else:
                stats["not_found"] += 1
                if not verbose:
                    logger.info(f"[{idx}/{len(candidates)}] {name:40s}  -")
        except Exception:
            stats["errors"] = stats.get("errors", 0) + 1
            logger.warning(f"[{idx}/{len(candidates)}] {name}: error during enrichment", exc_info=True)

    logger.info("=" * 70)
    logger.info(f"\nResults:  found {stats['found']}, not found {stats['not_found']}")
    for src, cnt in sorted(stats["by_source"].items(), key=lambda x: -x[1]):
        logger.info(f"  {src:20s}: {cnt}")

    if not dry_run and updates:
        logger.info(f"\nWriting {len(updates)} updates to {output_file} ...")
        replaced = _update_authors_yml(output_file, updates)
        logger.info(f"  {replaced} lines updated in YAML.")
        # Save updated author index
        if _save_index_fn and index_by_name:
            _save_index_fn()
            logger.info("  Author index updated")
    elif dry_run:
        logger.info(f"\n[DRY RUN] Would update {len(updates)} authors.")

    stats["updates_written"] = len(updates) if not dry_run else 0
    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Paper-based affiliation enrichment (OpenAlex + S2 + CrossRef + DBLP)")
    parser.add_argument("--authors_file", required=True, help="Path to authors.yml")
    parser.add_argument("--papers_file", required=True, help="Path to paper_authors_map.json")
    parser.add_argument("--output_file", default=None, help="Output path (default: overwrite authors_file)")
    parser.add_argument("--max_authors", type=int, default=None, help="Limit number of authors to process")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--dry_run", action="store_true", help="Don't write changes")
    parser.add_argument(
        "--recheck", action="store_true", help="Re-query all authors, including those with existing affiliations"
    )
    parser.add_argument("--data_dir", default=None, help="Website repo root for author index updates")
    args = parser.parse_args()

    enrich(
        authors_file=args.authors_file,
        papers_file=args.papers_file,
        output_file=args.output_file,
        max_authors=args.max_authors,
        verbose=args.verbose,
        dry_run=args.dry_run,
        recheck=args.recheck,
        data_dir=args.data_dir,
    )


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()

    main()
