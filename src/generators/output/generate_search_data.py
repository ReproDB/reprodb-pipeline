#!/usr/bin/env python3
"""Generate search_data.json by merging artifacts.json, paper_authors_map.json, and authors.json."""

import argparse
import logging
import re
from pathlib import Path

from src.utils.io.io import load_json, resolve_data_path, save_validated_json

from src.models.artifacts.search_data import SearchEntry

logger = logging.getLogger(__name__)


def _title_key(t: str) -> str:
    """Strict title key: lowercase, alphanumeric only (no spaces)."""
    return re.sub(r"[^a-z0-9]", "", t.lower())


def generate_search_data(data_dir: str) -> list:
    assets_data = Path(data_dir) / "assets" / "data"

    artifacts = load_json(assets_data / "artifacts.json")

    # paper_authors_map is an intermediate file in _build/
    pa_path = resolve_data_path(Path(data_dir), "paper_authors_map.json")
    paper_authors = []
    if pa_path.exists():
        paper_authors = load_json(pa_path)

    authors_path = assets_data / "authors.json"
    authors_data = []
    if authors_path.exists():
        authors_data = load_json(authors_path)

    # Build author -> affiliation lookup
    author_affiliation = {}
    for a in authors_data:
        author_affiliation[a["name"]] = a.get("affiliation", "")
        if a.get("display_name"):
            author_affiliation[a["display_name"]] = a.get("affiliation", "")

    # Build paper_authors lookup by normalized title
    pa_lookup = {}
    for pa in paper_authors:
        key = _title_key(pa["title"])
        pa_lookup[key] = pa

    # Merge
    merged = []
    for art in artifacts:
        key = _title_key(art["title"])
        pa = pa_lookup.get(key, {})
        authors_list = pa.get("authors", [])
        clean_authors = [re.sub(r"\s+\d{4}$", "", a) for a in authors_list]
        affiliations = sorted({author_affiliation[a] for a in authors_list if author_affiliation.get(a)})

        doi_url = pa.get("doi_url", "")

        entry = {
            "title": art["title"].strip(),
            "conference": art["conference"],
            "category": art["category"],
            "year": art["year"],
            "badges": art["badges"],
            "artifact_urls": art.get("artifact_urls", []),
            "doi_url": doi_url,
            "authors": clean_authors,
            "affiliations": affiliations,
        }
        for optional_key in ("paper_url", "appendix_url", "award"):
            if art.get(optional_key):
                entry[optional_key] = art[optional_key]
        merged.append(entry)

    merged.sort(key=lambda x: (-x["year"], x["conference"], x["title"]))

    out_path = assets_data / "search_data.json"
    save_validated_json(out_path, merged, SearchEntry, indent=None)

    logger.info(
        f"search_data.json: {len(merged)} artifacts "
        f"({sum(1 for e in merged if e['authors'])} with authors, "
        f"{sum(1 for e in merged if e['affiliations'])} with affiliations)"
    )
    return merged


def main():
    parser = argparse.ArgumentParser(description="Generate search_data.json")
    parser.add_argument("--data_dir", type=str, required=True, help="Website output directory")
    args = parser.parse_args()
    generate_search_data(args.data_dir)


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()

    main()
