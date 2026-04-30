#!/usr/bin/env python3
"""
Enrich author affiliations using CSRankings data.

CSRankings (http://csrankings.org) maintains a comprehensive database of
computer science faculty affiliations. This script downloads the official
csrankings.csv file and matches our authors to their faculty records.
"""

import argparse
import csv
import logging
import os
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

import requests

from src.utils.io.cache import CACHE_ROOT, SECONDS_PER_DAY
from src.utils.io.io import load_json, save_json

logger = logging.getLogger(__name__)
CSRANKINGS_URL = "https://raw.githubusercontent.com/emeryberger/CSrankings/gh-pages/csrankings.csv"
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
CACHE_DIR = CACHE_ROOT / "csrankings"
CACHE_FILE = CACHE_DIR / "csrankings.csv"
CACHE_TTL_DAYS = 30  # CSRankings data changes monthly


def download_csrankings(force_refresh: bool = False, verbose: bool = False) -> Path:
    """Download CSRankings CSV file with caching."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Check cache freshness
    if CACHE_FILE.exists() and not force_refresh:
        age_days = (time.time() - CACHE_FILE.stat().st_mtime) / SECONDS_PER_DAY
        if age_days < CACHE_TTL_DAYS:
            if verbose:
                logger.info(f"Using cached CSRankings data (age: {age_days:.1f} days)")
            return CACHE_FILE

    # Download fresh data
    if verbose:
        logger.info(f"Downloading CSRankings data from {CSRANKINGS_URL}...")

    # Support proxy environment variables
    proxies = {}
    if os.environ.get("http_proxy"):
        proxies["http"] = os.environ["http_proxy"]
    if os.environ.get("https_proxy"):
        proxies["https"] = os.environ["https_proxy"]

    try:
        response = requests.get(CSRANKINGS_URL, proxies=proxies, timeout=60)
        response.raise_for_status()

        CACHE_FILE.write_text(response.text, encoding="utf-8")
        if verbose:
            logger.info(f"Downloaded {len(response.text)} bytes to {CACHE_FILE}")

        return CACHE_FILE
    except Exception as e:
        if CACHE_FILE.exists():
            logger.warning(f"Download failed ({e}), using stale cache")
            return CACHE_FILE
        raise


def load_csrankings(csv_path: Path, verbose: bool = False) -> dict[str, list[dict]]:
    """
    Load CSRankings CSV and build name lookup index.
    Returns dict mapping normalized names to list of possible records.
    """
    name_index = defaultdict(list)

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("name", "").strip()
            affiliation = row.get("affiliation", "").strip()

            if not name or not affiliation:
                continue

            # Store record with multiple name variations for matching
            record = {
                "name": name,
                "affiliation": affiliation,
                "homepage": row.get("homepage", "").strip(),
                "scholarid": row.get("scholarid", "").strip(),
                "orcid": row.get("orcid", "").strip(),
            }

            # Index by normalized full name
            normalized_name = _normalize_for_csrankings(name)
            name_index[normalized_name].append(record)

            # Also index by last name for partial matching
            parts = name.split()
            if len(parts) >= 2:
                last_name = parts[-1].lower()
                name_index[f"lastname:{last_name}"].append(record)

    if verbose:
        logger.info(
            f"Loaded {len([r for records in name_index.values() for r in records if 'lastname:' not in r])} CSRankings records"
        )

    return name_index


def _normalize_for_csrankings(name: str) -> str:
    """Normalize name for matching: lowercase, remove punctuation."""
    name = re.sub(r"\s*\[[^\]]+\]\s*", " ", name)
    return "".join(c.lower() for c in name if c.isalnum() or c.isspace()).strip()


def fuzzy_name_match(author_name: str, csrankings_name: str) -> bool:
    """
    Check if names match, handling common variations:
    - Middle names / initials
    - Name order (First Last vs Last, First)
    - Accents and Unicode normalization
    """
    auth_norm = _normalize_for_csrankings(author_name)
    cs_norm = _normalize_for_csrankings(csrankings_name)

    # Exact match
    if auth_norm == cs_norm:
        return True

    # Split into parts
    auth_parts = auth_norm.split()
    cs_parts = cs_norm.split()

    if not auth_parts or not cs_parts:
        return False

    # Last name must match
    if auth_parts[-1] != cs_parts[-1]:
        return False

    # First name match (allowing initials)
    auth_first = auth_parts[0]
    cs_first = cs_parts[0]

    if auth_first == cs_first:
        return True

    # Check if one is initial of the other
    return bool(
        len(auth_first) == 1
        and cs_first.startswith(auth_first)
        or len(cs_first) == 1
        and auth_first.startswith(cs_first)
    )


def match_author_to_csrankings(
    author_name: str, name_index: dict[str, list[dict]], verbose: bool = False
) -> Optional[str]:
    """
    Match author to CSRankings record and return affiliation.
    Returns None if no match found.
    """
    normalized = _normalize_for_csrankings(author_name)

    # Try exact normalized match first
    candidates = name_index.get(normalized, [])

    if not candidates:
        # Try last name match
        parts = author_name.split()
        if len(parts) >= 2:
            last_name_key = f"lastname:{parts[-1].lower()}"
            candidates = name_index.get(last_name_key, [])

    # Find best match using fuzzy matching
    for record in candidates:
        if fuzzy_name_match(author_name, record["name"]):
            if verbose:
                logger.info(f"    Matched '{author_name}' -> '{record['name']}' ({record['affiliation']})")
            return record["affiliation"]

    return None


def enrich_affiliations(
    authors_file: Path,
    output_file: Path,
    name_index: dict[str, list[dict]],
    max_authors: Optional[int] = None,
    dry_run: bool = False,
    verbose: bool = False,
    data_dir: Optional[str] = None,
) -> dict[str, int]:
    """
    Enrich author affiliations using CSRankings data.
    Returns statistics about enrichment.
    """
    # Load authors
    authors = load_json(authors_file)

    # Load author index if data_dir is provided
    index_by_name = {}
    if data_dir:
        try:
            from src.utils.normalization.author_index import (
                load_author_index,
                save_author_index,
                update_author_affiliation,
            )

            _, index_by_name = load_author_index(data_dir)
            if index_by_name:
                logger.info(f"Loaded author index ({len(index_by_name)} entries)")
        except ImportError:
            logger.debug("Author index not available, skipping author ID enrichment")

    # Track statistics
    stats = {"total": len(authors), "already_has_affiliation": 0, "csrankings_match": 0, "no_match": 0, "enriched": 0}

    # Track overwrites when CSRankings supersedes existing affiliations
    stats["overwritten"] = 0

    if max_authors:
        authors = authors[:max_authors]

    logger.info(f"Processing {len(authors)} authors for CSRankings matches...")

    # Enrich affiliations (CSRankings takes precedence if available)
    enriched_count = 0
    for i, author in enumerate(authors, 1):
        name = author.get("name", "")
        current_affil = author.get("affiliation", "")
        has_affil = bool(current_affil and current_affil != "Unknown")
        if has_affil:
            stats["already_has_affiliation"] += 1

        if verbose:
            logger.info(f"  [{i}/{len(authors)}] Looking up: {name}")

        affiliation = match_author_to_csrankings(name, name_index, verbose)

        if affiliation:
            if affiliation != current_affil:
                author["affiliation"] = affiliation
                # Update author index
                if name in index_by_name:
                    update_author_affiliation(index_by_name[name], affiliation, "csrankings")
                if has_affil:
                    stats["overwritten"] += 1
                else:
                    enriched_count += 1
            stats["csrankings_match"] += 1
            if verbose:
                logger.info(f"    ✓ Found: {affiliation}")

            # Progress update every 100 authors
            if not verbose and i % 100 == 0:
                logger.info(f"  Processed {i}/{len(authors)}... (found {enriched_count} so far)")
        else:
            stats["no_match"] += 1
            if verbose:
                logger.info("    ✗ No match in CSRankings")

    stats["enriched"] = enriched_count
    stats["remaining"] = sum(1 for a in authors if not a.get("affiliation") or a.get("affiliation") == "Unknown")
    stats["final_coverage"] = 100 * (stats["total"] - stats["remaining"]) / stats["total"] if stats["total"] else 0

    # Save results
    if not dry_run:
        save_json(output_file, authors)
        logger.info(f"\nEnriched authors saved to: {output_file}")
        # Save updated author index
        if data_dir and index_by_name:
            idx_path = save_author_index(
                data_dir, [index_by_name[n] for n in sorted(index_by_name, key=lambda n: index_by_name[n]["id"])]
            )
            logger.info(f"Author index updated: {idx_path}")
    else:
        logger.info(f"\nDry run - would save to: {output_file}")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Enrich author affiliations using CSRankings data")
    parser.add_argument(
        "--authors_file",
        type=Path,
        default=Path("authors.json"),
        help="Input authors JSON file (default: authors.json)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("authors_enriched_csrankings.json"),
        help="Output file (default: authors_enriched_csrankings.json)",
    )
    parser.add_argument("--max_authors", type=int, help="Maximum authors to process (for testing)")
    parser.add_argument("--force_refresh", action="store_true", help="Force download of fresh CSRankings data")
    parser.add_argument("--dry_run", action="store_true", help="Do not save output file")
    parser.add_argument("--verbose", action="store_true", help="Print detailed progress")
    parser.add_argument("--data_dir", default=None, help="Website repo root for author index updates")

    args = parser.parse_args()

    # Download CSRankings data
    csv_path = download_csrankings(force_refresh=args.force_refresh, verbose=args.verbose)

    # Load and index CSRankings
    name_index = load_csrankings(csv_path, verbose=args.verbose)

    # Enrich affiliations
    stats = enrich_affiliations(
        authors_file=args.authors_file,
        output_file=args.output,
        name_index=name_index,
        max_authors=args.max_authors,
        dry_run=args.dry_run,
        verbose=args.verbose,
        data_dir=args.data_dir,
    )

    # Print summary
    logger.info("\n" + "=" * 60)
    logger.info("CSRankings Enrichment Summary")
    logger.info("=" * 60)
    logger.info(f"Total authors:              {stats['total']:,}")
    logger.info(f"Already have affiliation:   {stats['already_has_affiliation']:,}")
    logger.info(f"Missing affiliation:        {stats['total'] - stats['already_has_affiliation']:,}")
    logger.info(f"CSRankings matches:         {stats['csrankings_match']:,}")
    logger.info(f"No match found:             {stats['no_match']:,}")
    logger.info(f"Total enriched:             {stats['enriched']:,}")
    logger.info(f"Overwritten affiliations:   {stats['overwritten']:,}")

    if stats["total"] > 0:
        match_rate = 100 * stats["csrankings_match"] / stats["total"]
        logger.info(f"Match rate:                 {match_rate:.1f}%")

    logger.info(f"Final coverage:             {stats['final_coverage']:.1f}%")
    if stats["total"] > 0:
        logger.info(
            f"Still missing:              {stats['remaining']:,} ({100 * stats['remaining'] / stats['total']:.1f}%)"
        )
    logger.info("=" * 60)


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()

    main()
