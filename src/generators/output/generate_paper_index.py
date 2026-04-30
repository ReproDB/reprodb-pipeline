#!/usr/bin/env python3
"""
Generate a canonical paper index (papers.json) from authors.yml.

Each unique paper gets a stable integer ID. Co-authored papers are stored
once in the index and referenced by ID from each author entry.

Usage:
    python -m src.generators.generate_paper_index --data_dir ../reprodb.github.io/src
"""

import argparse
import logging
from pathlib import Path

from src.utils.normalization.conference import normalize_title
from src.utils.io.io import load_json, load_yaml, save_json, save_validated_json

from src.models.artifacts.paper_index import Paper

logger = logging.getLogger(__name__)


def load_existing_index(path):
    """Load existing paper index to preserve IDs across runs."""
    if not Path(path).exists():
        return [], {}
    entries = load_json(path)
    by_norm_title = {}
    for entry in entries:
        key = normalize_title(entry.get("title", ""))
        if key:
            by_norm_title[key] = entry
    return entries, by_norm_title


def build_paper_index(authors_data, existing_by_title, max_id):
    """Build paper index from authors.yml data.

    Returns (papers_list, norm_title_to_id dict).
    """
    # Collect unique papers from all authors
    seen = {}  # normalized_title -> paper dict
    for author in authors_data:
        for paper in author.get("papers", []):
            title = paper.get("title", "")
            norm = normalize_title(title)
            if not norm:
                continue
            if norm not in seen:
                seen[norm] = {
                    "title": title,
                    "conference": paper.get("conference", ""),
                    "year": paper.get("year"),
                    "category": paper.get("category", ""),
                    "badges": paper.get("badges", []),
                    "artifact_citations": paper.get("artifact_citations", 0),
                }
            else:
                # Update citation count if higher
                existing_cit = seen[norm].get("artifact_citations", 0) or 0
                new_cit = paper.get("artifact_citations", 0) or 0
                if new_cit > existing_cit:
                    seen[norm]["artifact_citations"] = new_cit

        # Also collect papers_without_artifacts
        for paper in author.get("papers_without_artifacts", []):
            title = paper.get("title", "")
            norm = normalize_title(title)
            if not norm:
                continue
            if norm not in seen:
                seen[norm] = {
                    "title": title,
                    "conference": paper.get("conference", ""),
                    "year": paper.get("year"),
                    "category": paper.get("category", ""),
                    "badges": [],
                    "artifact_citations": 0,
                    "has_artifact": False,
                }

    # Assign IDs: preserve existing, assign new for unseen
    papers = []
    next_id = max_id + 1

    for norm_title, paper in seen.items():
        if norm_title in existing_by_title:
            paper["id"] = existing_by_title[norm_title]["id"]
        else:
            paper["id"] = next_id
            next_id += 1
        # Mark whether this paper has artifacts
        if "has_artifact" not in paper:
            paper["has_artifact"] = True
        papers.append(paper)

    papers.sort(key=lambda x: x["id"])
    norm_to_id = {normalize_title(p["title"]): p["id"] for p in papers}
    return papers, norm_to_id


def main():
    parser = argparse.ArgumentParser(description="Generate canonical paper index.")
    parser.add_argument("--data_dir", type=str, required=True, help="Path to the website repo root (containing _data/)")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    authors_path = data_dir / "_data" / "authors.yml"
    index_path = data_dir / "_data" / "papers.json"

    # Load authors (use JSON if available, else YAML)
    json_path = data_dir / "assets" / "data" / "authors.json"
    if json_path.exists():
        authors_data = load_json(json_path)
    else:
        authors_data = load_yaml(authors_path)

    existing, existing_by_title = load_existing_index(index_path)
    max_id = max((e["id"] for e in existing), default=0)

    papers, norm_to_id = build_paper_index(authors_data, existing_by_title, max_id)

    # Write paper index
    index_path.parent.mkdir(parents=True, exist_ok=True)
    save_validated_json(index_path, papers, Paper)

    # Also write to assets/data for client-side loading
    assets_path = data_dir / "assets" / "data" / "papers.json"
    assets_path.parent.mkdir(parents=True, exist_ok=True)
    save_json(assets_path, papers, indent=None)

    logger.info(f"Paper index: {len(papers)} unique papers -> {index_path}")
    artifact_papers = sum(1 for p in papers if p.get("has_artifact", True))
    logger.info(f"  With artifacts: {artifact_papers}, without: {len(papers) - artifact_papers}")

    return papers, norm_to_id


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()

    main()
