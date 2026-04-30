#!/usr/bin/env python3
"""
Enrich author affiliations using AE (Artifact Evaluation) committee member data.

AE members are scraped from conference websites and have ~99.5% affiliation
coverage.  This enricher is fully offline — it reads the existing
``ae_members.json`` file and matches author names to fill in missing
affiliations.
"""

import argparse
import logging
import os
from pathlib import Path
from typing import Optional

from src.utils.io.io import load_json, save_json

from ..utils.conference import normalize_name

logger = logging.getLogger(__name__)


def load_ae_members(data_dir: str) -> dict[str, str]:
    """Load AE member data and build a name → affiliation mapping.

    Returns dict mapping normalized author name → affiliation string.
    The most recent committee appearance wins (AE members list is sorted
    by frequency, so first occurrence is the most active member).
    """
    ae_path = os.path.join(data_dir, "assets", "data", "ae_members.json")
    if not os.path.exists(ae_path):
        logger.warning(f"AE members file not found: {ae_path}")
        return {}

    members = load_json(ae_path)

    # Build exact-name and normalized-name lookup
    name_to_affil: dict[str, str] = {}
    for member in members:
        name = member.get("name", "")
        affiliation = member.get("affiliation", "")
        if name and affiliation and name not in name_to_affil:
            name_to_affil[name] = affiliation

    logger.info(f"Loaded {len(name_to_affil)} AE members with affiliations")
    return name_to_affil


def enrich_affiliations(
    authors_file: Path,
    output_file: Path,
    data_dir: str,
    max_authors: Optional[int] = None,
    dry_run: bool = False,
    verbose: bool = False,
) -> dict[str, int]:
    """Enrich author affiliations from AE committee member data.

    Only fills in missing affiliations — does NOT overwrite existing ones
    (unlike CSRankings which takes precedence as a curated source).
    """
    authors = load_json(authors_file)

    ae_affils = load_ae_members(data_dir)
    if not ae_affils:
        return {"total": len(authors), "enriched": 0, "already_has_affiliation": 0, "no_match": 0}

    # Also build a normalized lookup for fuzzy matching
    norm_to_affil: dict[str, str] = {}
    for name, affil in ae_affils.items():
        nk = normalize_name(name)
        if nk and nk not in norm_to_affil:
            norm_to_affil[nk] = affil

    # Load author index if data_dir is provided
    index_by_name: dict = {}
    _update_index_fn = None
    _save_index_fn = None
    try:
        from src.utils.normalization.author_index import load_author_index, save_author_index, update_author_affiliation

        _, index_by_name = load_author_index(data_dir)
        _update_index_fn = update_author_affiliation

        def _save_index_fn():
            return save_author_index(data_dir, sorted(index_by_name.values(), key=lambda e: e["id"]))

        if index_by_name:
            logger.info(f"Loaded author index ({len(index_by_name)} entries)")
    except ImportError:
        logger.debug("Author index not available, skipping")

    stats = {"total": len(authors), "already_has_affiliation": 0, "enriched": 0, "no_match": 0}

    if max_authors:
        authors = authors[:max_authors]

    logger.info(f"Processing {len(authors)} authors for AE member matches...")

    for i, author in enumerate(authors, 1):
        name = author.get("name", "")
        current_affil = author.get("affiliation", "")

        if current_affil and current_affil != "Unknown":
            stats["already_has_affiliation"] += 1
            continue

        # Try exact match first, then normalized
        affiliation = ae_affils.get(name)
        if not affiliation:
            affiliation = norm_to_affil.get(normalize_name(name))

        if affiliation:
            author["affiliation"] = affiliation
            stats["enriched"] += 1
            if name in index_by_name and _update_index_fn:
                _update_index_fn(index_by_name[name], affiliation, "ae_committee")
            if verbose:
                logger.info(f"  [{i}] {name} → {affiliation}")
        else:
            stats["no_match"] += 1

    stats["remaining"] = sum(1 for a in authors if not a.get("affiliation") or a.get("affiliation") == "Unknown")
    stats["final_coverage"] = 100 * (stats["total"] - stats["remaining"]) / stats["total"] if stats["total"] else 0

    if not dry_run:
        save_json(output_file, authors)
        logger.info(f"Enriched authors saved to: {output_file}")
        if _save_index_fn and index_by_name:
            _save_index_fn()
            logger.info("Author index updated")
    else:
        logger.info(f"[DRY RUN] Would save to: {output_file}")

    logger.info(f"AE-member enrichment: {stats['enriched']} new affiliations, coverage {stats['final_coverage']:.1f}%")
    return stats


def main():
    parser = argparse.ArgumentParser(description="Enrich author affiliations using AE committee member data")
    parser.add_argument("--authors_file", required=True, help="Path to authors.json")
    parser.add_argument("--output", default=None, help="Output path (default: overwrite authors_file)")
    parser.add_argument("--data_dir", required=True, help="Website data directory")
    parser.add_argument("--max_authors", type=int, default=None)
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--dry_run", action="store_true")
    args = parser.parse_args()

    output = Path(args.output) if args.output else Path(args.authors_file)
    enrich_affiliations(
        authors_file=Path(args.authors_file),
        output_file=output,
        data_dir=args.data_dir,
        max_authors=args.max_authors,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()

    main()
