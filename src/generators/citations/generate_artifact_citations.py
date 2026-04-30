#!/usr/bin/env python3
"""
Generate artifact DOI citation counts via OpenAlex and Semantic Scholar and write per-artifact metadata.

Outputs:
  assets/data/artifact_citations.json
  assets/data/artifact_citations_summary.json

Usage:
  python generate_artifact_citations.py --data_dir ../reprodb.github.io/src
"""

import argparse
import logging
import os
import re
from pathlib import Path

from src.utils.apis.citation_apis import (
    best_citation_count,
    create_session,
    extract_doi,
    fetch_json_urllib,
    is_artifact_doi,
    openalex_lookup_with_retry,
    s2_lookup_with_retry,
    s2_reachable,
)
from src.utils.io.io import load_json, save_json
from src.utils.normalization.artifact_urls import get_artifact_urls

logger = logging.getLogger(__name__)


def log(msg: str) -> None:
    logger.info(msg)


def short_url(url: str, max_len: int = 120) -> str:
    if len(url) <= max_len:
        return url
    return url[: max_len - 3] + "..."


def load_local_env_file(file_path: str) -> None:
    if not Path(file_path).exists():
        return
    try:
        with open(file_path, "r", encoding="utf-8") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    os.environ.setdefault(key, value)
        log(f"Loaded local environment from {file_path}")
    except Exception as e:
        log(f"Warning: could not load local env file {file_path}: {type(e).__name__}: {e}")


def extract_zenodo_record_id(url) -> str:
    if not url or not isinstance(url, str):
        return ""
    m = re.search(r"zenodo\.org/(?:record|records)/(\d+)", url, re.I)
    if m:
        return m.group(1)
    m = re.search(r"zenodo\.org/badge/latestdoi/(\d+)", url, re.I)
    if m:
        return m.group(1)
    return ""


def fetch_zenodo_doi(record_id: str, cache: dict) -> str:
    """Get DOI for a Zenodo record.

    Always returns the Zenodo DOI (10.5281/zenodo.{record_id}) to ensure we get
    artifact citations, not paper citations from DOIs that authors may have linked.
    """
    if record_id in cache:
        return cache[record_id]

    doi = f"10.5281/zenodo.{record_id}".lower()

    url = f"https://zenodo.org/api/records/{record_id}"
    try:
        fetch_json_urllib(url, timeout=30)
        cache[record_id] = doi
        return doi
    except Exception:
        cache[record_id] = ""
        return ""


def generate(data_dir: str) -> None:
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    local_env_path = repo_root / ".env.local"
    load_local_env_file(local_env_path)

    logger.info("=" * 60)
    logger.info("Starting artifact citation generation...")
    logger.info("=" * 60)

    artifacts_path = Path(data_dir) / "assets" / "data" / "artifacts.json"
    out_path = Path(data_dir) / "assets" / "data" / "artifact_citations.json"

    logger.info(f"Loading artifacts from: {artifacts_path}")

    if not artifacts_path.exists():
        logger.error(f"Error: {artifacts_path} not found. Run generate_statistics.py first.")
        return

    artifacts = load_json(artifacts_path)

    logger.info(f"✓ Loaded {len(artifacts)} artifacts")

    zenodo_cache = {}
    openalex_cache: dict[str, dict] = {}
    semantic_scholar_cache: dict[str, dict] = {}
    session = create_session()
    fetch_citing_dois = os.environ.get("FETCH_CITING_DOIS", "1").strip() != "0"

    logger.info(f"Processing {len(artifacts)} artifacts...")
    logger.info(f"Fetch citing DOIs: {fetch_citing_dois}")
    logger.info("")

    entries = []
    seen_doi = set()
    dois_found = 0
    dois_filtered = 0
    semantic_failures = 0
    semantic_disabled = os.environ.get("DISABLE_SEMANTIC_SCHOLAR", "").strip() == "1"

    if semantic_disabled:
        log("[SemanticScholar] disabled via DISABLE_SEMANTIC_SCHOLAR=1")
    else:
        log("[SemanticScholar] running connectivity preflight...")
        if not s2_reachable():
            semantic_disabled = True
            log("[SemanticScholar] disabled for this run due to connectivity failure")

    for idx, artifact in enumerate(artifacts, 1):
        title = artifact.get("title", "")
        if not title:
            continue
        try:
            urls = get_artifact_urls(artifact)

            doi = ""
            source = ""

            # First priority: Try to get DOI from Zenodo API for Zenodo records
            # This ensures we get the artifact DOI, not a paper DOI embedded in the page
            for url in urls:
                record_id = extract_zenodo_record_id(url)
                if record_id:
                    doi = fetch_zenodo_doi(record_id, zenodo_cache)
                    if doi:
                        source = "zenodo_api"
                        break

            # Fallback: Extract DOI directly from URL (for non-Zenodo artifacts)
            if not doi:
                for url in urls:
                    doi = extract_doi(url)
                    if doi:
                        source = "url"
                        break

            # Filter: Only keep artifact DOIs (Zenodo, Figshare), drop paper DOIs (ACM, IEEE, etc.)
            if doi and not is_artifact_doi(doi):
                dois_filtered += 1
                doi = ""
                source = ""
            elif doi:
                dois_found += 1

            # Progress indicator
            if idx % 50 == 0:
                logger.info(
                    f"Progress: {idx}/{len(artifacts)} artifacts processed, {dois_found} DOIs found, {dois_filtered} filtered",
                )

            cited_by = None
            openalex_err = ""
            semantic_err = ""
            openalex_count = None
            semantic_count = None
            openalex_citing_dois: list[str] = []
            semantic_citing_dois: list[str] = []
            if doi:
                if doi in openalex_cache:
                    openalex_entry = openalex_cache[doi]
                else:
                    openalex_entry = openalex_lookup_with_retry(doi, session, fetch_citing_dois=fetch_citing_dois)
                    openalex_cache[doi] = openalex_entry
                if semantic_disabled:
                    semantic_entry = {
                        "count": None,
                        "citing_dois": [],
                        "error": "disabled_after_connect_failures",
                    }
                else:
                    if doi in semantic_scholar_cache:
                        semantic_entry = semantic_scholar_cache[doi]
                    else:
                        semantic_entry = s2_lookup_with_retry(doi, session, fetch_citing_dois=fetch_citing_dois)
                        semantic_scholar_cache[doi] = semantic_entry
                openalex_count = openalex_entry.get("count")
                semantic_count = semantic_entry.get("count")
                openalex_citing_dois = openalex_entry.get("citing_dois", [])
                semantic_citing_dois = semantic_entry.get("citing_dois", [])
                openalex_err = openalex_entry.get("error", "")
                semantic_err = semantic_entry.get("error", "")

                if semantic_err and "timed out" in semantic_err.lower():
                    semantic_failures += 1
                    if semantic_failures >= 5 and not semantic_disabled:
                        semantic_disabled = True
                        log(
                            "[SemanticScholar] disabled for this run after 5 timeout failures (network/connectivity issue)"
                        )

                cited_by = best_citation_count(openalex_count, semantic_count)
                seen_doi.add(doi)

            entries.append(
                {
                    "title": title,
                    "conference": artifact.get("conference", ""),
                    "year": artifact.get("year", ""),
                    "doi": doi,
                    "doi_source": source,
                    "cited_by_count": cited_by,
                    "citations_openalex": openalex_count,
                    "citations_semantic_scholar": semantic_count,
                    "citing_dois_openalex": openalex_citing_dois,
                    "citing_dois_semantic_scholar": semantic_citing_dois,
                    "openalex_error": openalex_err,
                    "semantic_scholar_error": semantic_err,
                }
            )
        except Exception:
            logger.warning(f"Error processing artifact '{title}', skipping", exc_info=True)

    # Count cited artifacts for logging
    cited = sum(1 for e in entries if isinstance(e.get("cited_by_count"), int) and e["cited_by_count"] > 0)

    save_json(out_path, entries)

    logger.info("")
    logger.info("✓ Processing complete!")
    logger.info(f"  Total artifacts: {len(artifacts)}")
    logger.info(f"  Artifact DOIs found: {dois_found}")
    logger.info(f"  Paper DOIs filtered: {dois_filtered}")
    logger.info(f"  Artifacts with citations: {cited}")
    logger.info("")
    logger.info(f"Wrote {out_path} ({len(entries)} entries)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate artifact citation stats via OpenAlex and Semantic Scholar")
    parser.add_argument("--data_dir", type=str, required=True, help="Path to reprodb.github.io")
    parser.add_argument(
        "--enable-citations",
        action="store_true",
        default=False,
        help="Actually run citation collection. Without this flag, the script exits immediately.",
    )
    args = parser.parse_args()

    if not args.enable_citations:
        logger.info("=" * 78)
        logger.warning("WARNING: Citation collection is DISABLED by default.")
        logger.info("")
        logger.info("OpenAlex citation counts for artifact DOIs are UNRELIABLE.")
        logger.info("Verification (March 2026) found that ALL 43 reported citing DOIs")
        logger.info("were false positives (paper DOI cited instead of artifact DOI),")
        logger.info("self-citations, or unresolvable. Zero genuine third-party artifact")
        logger.info("citations exist in the current dataset.")
        logger.info("")
        logger.info("If you still want to run citation collection (e.g., for research")
        logger.info("or to check whether the situation has improved), pass:")
        logger.info("  --enable-citations")
        logger.info("")
        logger.info("After collection, run verify_artifact_citations.py to validate")
        logger.info("whether any reported citations are genuine.")
        logger.info("=" * 78)
        return

    generate(args.data_dir)


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()

    main()
