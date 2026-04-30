#!/usr/bin/env python3
"""Export JSON Schema files from Pydantic models.

Generates one ``.schema.json`` file per model, matching the layout in the
``data-schemas`` repository.  Run this after modifying any model in
``src/models/`` to keep schemas in sync.

Usage:
    python -m src.models.export_schemas --output_dir ../data-schemas/schemas
"""

import argparse
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
# Registry: (schema_filename, list_wrapper, model_class_import_path)
# list_wrapper == True means the top-level schema is ``type: array`` wrapping items.
SCHEMA_REGISTRY: list[tuple[str, bool, str, str]] = [
    # (filename, is_array, module, class_name)
    ("ae_chairs.schema.json", True, "src.models.committees.ae_chairs", "AEChair"),
    ("ae_members.schema.json", True, "src.models.committees.ae_members", "AEMember"),
    ("artifact_availability.schema.json", False, "src.models.artifacts.artifact_availability", "ArtifactAvailability"),
    ("artifact_citations.schema.json", True, "src.models.artifacts.artifact_citations", "ArtifactCitation"),
    ("artifacts.schema.json", True, "src.models.artifacts.artifacts", "Artifact"),
    ("artifacts_by_conference.schema.json", True, "src.models.aggregates.artifacts_by_conference", "ConferenceEntry"),
    ("artifacts_by_year.schema.json", True, "src.models.aggregates.artifacts_by_year", "ArtifactsByYear"),
    ("author_index.schema.json", True, "src.models.authors.author_index", "AuthorIndexEntry"),
    ("author_profiles.schema.json", True, "src.models.authors.author_profiles", "AuthorProfile"),
    ("author_stats.schema.json", True, "src.models.authors.author_stats", "AuthorStats"),
    ("combined_rankings.schema.json", True, "src.models.authors.combined_rankings", "AuthorRanking"),
    ("committee_stats.schema.json", False, "src.models.committees.committee_stats", "CommitteeStats"),
    ("chair_stats.schema.json", False, "src.models.committees.chair_stats", "ChairStats"),
    (
        "institution_ranking_history.schema.json",
        True,
        "src.models.institutions.institution_ranking_history",
        "InstitutionRankingHistoryEntry",
    ),
    ("institution_rankings.schema.json", True, "src.models.institutions.institution_rankings", "InstitutionRanking"),
    ("paper_index.schema.json", True, "src.models.artifacts.paper_index", "Paper"),
    ("participation_stats.schema.json", False, "src.models.committees.participation_stats", "ParticipationStats"),
    ("ranking_history.schema.json", True, "src.models.aggregates.ranking_history", "RankingHistoryEntry"),
    ("repo_stats.schema.json", True, "src.models.aggregates.repo_stats", "RepoStatsEntry"),
    ("repo_stats_summary.schema.json", False, "src.models.aggregates.repo_stats", "RepoStatsSummary"),
    ("repo_stats_yearly.schema.json", True, "src.models.aggregates.repo_stats_yearly", "RepoStatsYearly"),
    ("search_data.schema.json", True, "src.models.artifacts.search_data", "SearchEntry"),
    ("summary.schema.json", False, "src.models.aggregates.summary", "Summary"),
    ("top_repos.schema.json", True, "src.models.aggregates.top_repos", "TopRepo"),
]

BASE_URL = "https://reprodb.github.io/data-schemas/schemas"


def _import_class(module_path: str, class_name: str):
    """Dynamically import a class from a dotted module path."""
    import importlib

    mod = importlib.import_module(module_path)
    return getattr(mod, class_name)


def _make_array_schema(item_schema: dict[str, Any], title: str, description: str, schema_id: str) -> dict:
    """Wrap an item schema in a JSON Schema array with $defs."""
    from src.models import SCHEMA_VERSION

    # Pydantic generates a schema with $defs for nested models.
    # We hoist $defs to top level and use $ref for items.
    defs = item_schema.pop("$defs", {})

    # The main class schema becomes a $def entry
    class_name = item_schema.get("title", "Item")
    defs[class_name] = item_schema

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": schema_id,
        "title": title,
        "description": description,
        "version": SCHEMA_VERSION,
        "type": "array",
        "items": {"$ref": f"#/$defs/{class_name}"},
        "$defs": defs,
    }


def _make_object_schema(obj_schema: dict[str, Any], schema_id: str) -> dict:
    """Add standard JSON Schema metadata to an object schema (placed first)."""
    from src.models import SCHEMA_VERSION

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": schema_id,
        "version": SCHEMA_VERSION,
        **obj_schema,
    }


def export_all(output_dir: str) -> list[str]:
    """Export all registered schemas. Returns list of written file paths."""
    os.makedirs(output_dir, exist_ok=True)
    written = []

    for filename, is_array, module_path, class_name in SCHEMA_REGISTRY:
        cls = _import_class(module_path, class_name)
        schema = cls.model_json_schema()
        schema_id = f"{BASE_URL}/{filename}"

        if is_array:
            title = schema.get("title", class_name)
            item_description = schema.get("description", "")
            # Use the class docstring first line if model description is empty
            if not item_description and cls.__doc__:
                item_description = cls.__doc__.strip().split("\n")[0]
            # Collection description summarises the array; item description stays on the $def.
            collection_description = f"Array of {title} records. Each element: {item_description}"
            final = _make_array_schema(schema, f"{title} Collection", collection_description, schema_id)
        else:
            final = _make_object_schema(schema, schema_id)

        path = os.path.join(output_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(final, f, indent=2, ensure_ascii=False)
            f.write("\n")

        written.append(path)
        logger.info(f"  {filename}")

    return written


def tag_schema_repo(schema_repo: str | Path) -> str | None:
    """Create a git tag ``v{SCHEMA_VERSION}`` in the data-schemas repo.

    If the tag already exists the function is a no-op.  Returns the tag
    name on success, ``None`` when the tag was already present.
    """
    from src.models import SCHEMA_VERSION

    repo = Path(schema_repo).resolve()
    tag = f"v{SCHEMA_VERSION}"

    # Check if tag exists
    result = subprocess.run(
        ["git", "tag", "-l", tag],
        capture_output=True,
        text=True,
        cwd=repo,
        timeout=10,
    )
    if tag in result.stdout.strip().splitlines():
        logger.info("Tag %s already exists in %s", tag, repo)
        return None

    # Stage, commit, then tag
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, timeout=10)
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repo, timeout=10)
    if diff.returncode != 0:
        subprocess.run(
            ["git", "commit", "-m", f"Schema {tag}"],
            cwd=repo,
            check=True,
            timeout=30,
        )
    subprocess.run(
        ["git", "tag", "-a", tag, "-m", f"Data schema version {SCHEMA_VERSION}"],
        cwd=repo,
        check=True,
        timeout=10,
    )
    logger.info("Created tag %s in %s", tag, repo)
    return tag


def main():
    parser = argparse.ArgumentParser(description="Export JSON Schemas from Pydantic models.")
    parser.add_argument(
        "--output_dir",
        type=str,
        default="../data-schemas/schemas",
        help="Output directory for .schema.json files (default: ../data-schemas/schemas)",
    )
    parser.add_argument(
        "--tag",
        action="store_true",
        help="Git-tag the data-schemas repo with the current SCHEMA_VERSION",
    )
    args = parser.parse_args()

    logger.info(f"Exporting {len(SCHEMA_REGISTRY)} schemas to {args.output_dir}")
    written = export_all(args.output_dir)
    logger.info(f"\nDone. {len(written)} schema files written.")

    if args.tag:
        repo_dir = Path(args.output_dir).resolve().parent
        tag_schema_repo(repo_dir)


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()

    main()
