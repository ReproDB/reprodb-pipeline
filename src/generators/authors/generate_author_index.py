#!/usr/bin/env python3
"""
Generate and maintain the canonical author index.

The author index is the single source of truth for author identity and
affiliation.  Every author gets a stable integer ID that never changes.

Reads:
  - assets/data/authors.json       (from generate_author_stats — names + display_names)
  - assets/data/author_index.json  (previous index, if any — preserves IDs)

Writes:
  - assets/data/author_index.json

Usage:
  python -m src.generators.generate_author_index --data_dir ../reprodb.github.io/src
"""

import argparse
import logging
from datetime import datetime
from pathlib import Path

from src.models.authors.author_index import AuthorIndexEntry
from src.utils.io.io import load_json, resolve_data_path, save_validated_json
from src.utils.normalization.conference import clean_name

logger = logging.getLogger(__name__)


def load_existing_index(path: Path) -> tuple[list, dict[str, dict], int]:
    """Load the previous author index, return (list, name->entry dict, max_id)."""
    if not path.exists():
        return [], {}, 0
    entries = load_json(path)
    by_name = {e["name"]: e for e in entries}
    max_id = max((e["id"] for e in entries), default=0)
    return entries, by_name, max_id


def load_authors_json(path: Path) -> list[dict]:
    """Load authors.json produced by generate_author_stats."""
    if not path.exists():
        return []
    return load_json(path)


def build_index(authors: list[dict], existing_by_name: dict[str, dict], max_id: int) -> list[dict]:
    """Build a new index, preserving existing IDs and syncing affiliations.

    Returns the updated index list.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    index = []
    next_id = max_id + 1
    new_count = 0
    preserved_count = 0

    for author in authors:
        name = author.get("name", "")
        if not name:
            continue

        display_name = author.get("display_name") or clean_name(name)
        category = author.get("category", "systems")

        if name in existing_by_name:
            entry = existing_by_name[name].copy()
            entry["affiliation_history"] = list(entry.get("affiliation_history", []))
            entry["external_ids"] = dict(entry.get("external_ids", {}))
            entry["display_name"] = display_name
            entry["category"] = category
            preserved_count += 1
        else:
            entry = {
                "id": next_id,
                "name": name,
                "display_name": display_name,
                "affiliation": "",
                "affiliation_source": "",
                "affiliation_updated": "",
                "affiliation_history": [],
                "external_ids": {},
                "category": category,
            }
            next_id += 1
            new_count += 1

        index.append(entry)

    # Sort by ID for stable output
    index.sort(key=lambda e: e["id"])

    logger.info(f"Author index: {len(index)} total ({new_count} new, {preserved_count} preserved)")
    return index


def generate_author_index(data_dir: str) -> dict:
    """Main entry point: build/update the canonical author index."""
    data_dir_path = Path(data_dir)

    authors_path = resolve_data_path(data_dir_path, "authors.json")
    index_path = resolve_data_path(data_dir_path, "author_index.json")

    authors = load_authors_json(authors_path)
    if not authors:
        logger.warning(f"No authors found at {authors_path}")
        return {"total": 0, "new": 0}

    _, existing_by_name, max_id = load_existing_index(index_path)
    index = build_index(authors, existing_by_name, max_id)

    # Write to assets/data (canonical location)
    output_path = data_dir_path / "assets" / "data" / "author_index.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    save_validated_json(output_path, index, AuthorIndexEntry)

    logger.info(f"Wrote {len(index)} entries to {output_path}")
    return {"total": len(index), "path": str(output_path)}


def main():
    parser = argparse.ArgumentParser(description="Build/update canonical author index")
    parser.add_argument("--data_dir", type=str, required=True, help="Website data directory (contains assets/data/)")
    args = parser.parse_args()

    generate_author_index(args.data_dir)


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()
    main()
