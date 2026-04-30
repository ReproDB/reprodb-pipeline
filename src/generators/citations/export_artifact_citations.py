#!/usr/bin/env python3
"""
Export artifact citations in a simple format showing artifact DOI and citing DOIs.

Usage:
    python3 export_artifact_citations.py --data_dir ../reprodb.github.io/src
    python3 export_artifact_citations.py --data_dir ../reprodb.github.io/src --output citations_export.txt
"""

import argparse
import contextlib
import json
import logging
import sys
from pathlib import Path

from src.utils.io.io import load_json

logger = logging.getLogger(__name__)


def export_citations(data_dir: str, output_file: str = None) -> None:
    """Export artifact citations to a simple DOI mapping format."""

    citations_path = Path(data_dir) / "assets" / "data" / "artifact_citations.json"

    if not citations_path.exists():
        logger.error(f"Error: {citations_path} not found.")
        logger.info("Run generate_artifact_citations.py first.")
        sys.exit(1)

    artifacts = load_json(citations_path)

    # Open output file or use stdout
    out_cm = open(output_file, "w") if output_file else contextlib.nullcontext(sys.stdout)  # noqa: SIM115

    with out_cm as out:
        artifacts_with_citations = 0
        total_citing_dois = 0

        for artifact in artifacts:
            doi = artifact.get("doi")
            if not doi:
                continue

            # Collect citing DOIs from both sources
            citing_dois = []

            # Add OpenAlex citing DOIs
            openalex_citing = artifact.get("citing_dois_openalex", [])
            if openalex_citing:
                citing_dois.extend(openalex_citing)

            # Add Semantic Scholar citing DOIs
            semantic_citing = artifact.get("citing_dois_semantic_scholar", [])
            if semantic_citing:
                citing_dois.extend(semantic_citing)

            # Remove duplicates while preserving order
            seen = set()
            unique_citing_dois = []
            for citing_doi in citing_dois:
                if citing_doi not in seen:
                    seen.add(citing_doi)
                    unique_citing_dois.append(citing_doi)

            # Only output if there are citations
            if unique_citing_dois:
                artifacts_with_citations += 1
                total_citing_dois += len(unique_citing_dois)

                # Format: artifact_doi: ["citing_doi_1", "citing_doi_2", ...]
                citing_dois_str = json.dumps(unique_citing_dois)
                out.write(f"{doi}: {citing_dois_str}\n")

        # Print summary to stderr so it doesn't interfere with output
        logger.info("\n# Summary:")
        logger.info(f"# Total artifacts with DOIs: {sum(1 for a in artifacts if a.get('doi'))}")
        logger.info(f"# Artifacts with citing DOIs: {artifacts_with_citations}")
        logger.info(f"# Total citing DOIs collected: {total_citing_dois}")

    if output_file:
        logger.info(f"\nWrote citations export to: {output_file}")


def main():
    parser = argparse.ArgumentParser(
        description="Export artifact citations as DOI mappings",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Output to stdout
  python3 export_artifact_citations.py --data_dir ../reprodb.github.io/src

  # Output to file
  python3 export_artifact_citations.py --data_dir ../reprodb.github.io/src --output citations.txt

  # Pipe to other tools
  python3 export_artifact_citations.py --data_dir ../reprodb.github.io/src | grep "10.5281/zenodo"
        """,
    )

    parser.add_argument(
        "--data_dir", required=True, help="Path to the website data directory (e.g., ../reprodb.github.io/src)"
    )

    parser.add_argument("--output", "-o", help="Output file path (default: stdout)")

    args = parser.parse_args()

    export_citations(args.data_dir, args.output)


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()

    main()
