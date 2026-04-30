"""Collect citation counts for non-AE papers from the same conferences.

Reads:
  assets/data/artifacts.json          — AE papers (to exclude)
  .cache/dblp_extracted/papers_by_venue.json — all DBLP papers per venue/year

Writes:
  _build/baseline_citations.json      — citations for non-AE papers

This enables comparing citation counts between papers that went through
artifact evaluation vs. those that did not, at the same conference/year.

Usage::

    python -m src.generators.generate_baseline_citations \
        --data_dir ../reprodb.github.io/src
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from pathlib import Path

from src.utils.apis.citation_apis import (
    CITATION_CACHE_DIR,
    CITATION_CACHE_TTL,
    OPENALEX_DELAY,
    S2_DELAY,
    S2_MAX_TIMEOUT_FAILURES,
    best_citation_count,
    cache_key,
    create_session,
    openalex_lookup,
    openalex_title_search,
    s2_lookup,
)
from src.utils.apis.dblp_extract import load_papers_by_venue
from src.utils.io.cache import _MISSING, read_cache, write_cache
from src.utils.io.io import load_json, save_json
from src.utils.normalization.conference import conf_area, normalize_title

logger = logging.getLogger(__name__)

# Use a separate cache namespace to avoid collisions with AE citation cache.
BASELINE_CACHE_NS = "baseline_citations"


def _build_ae_title_set(artifacts: list[dict]) -> dict[tuple[str, int], set[str]]:
    """Build a mapping of (conference, year) → set of normalized AE paper titles."""
    ae_titles: dict[tuple[str, int], set[str]] = {}
    for a in artifacts:
        conf = a.get("conference", "")
        year = a.get("year", 0)
        norm = normalize_title(a.get("title", ""))
        if conf and year and norm:
            ae_titles.setdefault((conf, year), set()).add(norm)
    return ae_titles


def _find_non_ae_papers(
    ae_titles: dict[tuple[str, int], set[str]],
) -> list[dict]:
    """Return DBLP papers that are NOT in the AE set, for matching conf/years."""
    papers_by_venue = load_papers_by_venue()
    non_ae: list[dict] = []

    for (conf, year), ae_norms in sorted(ae_titles.items()):
        dblp_papers = papers_by_venue.get(conf, {}).get(str(year), [])
        if not dblp_papers:
            logger.warning("No DBLP data for %s %d — skipping baseline", conf, year)
            continue

        matched = 0
        for paper in dblp_papers:
            norm = normalize_title(paper.get("title", ""))
            if norm in ae_norms:
                matched += 1
                continue  # Skip AE papers
            non_ae.append(
                {
                    "title": paper.get("title", ""),
                    "conference": conf,
                    "year": year,
                    "category": conf_area(conf),
                    "doi": paper.get("doi", ""),
                    "dblp_key": paper.get("dblp_key", ""),
                }
            )

        logger.info(
            "%s %d: %d DBLP papers, %d matched AE, %d non-AE",
            conf,
            year,
            len(dblp_papers),
            matched,
            len(dblp_papers) - matched,
        )

    return non_ae


def generate(data_dir: str) -> list[dict] | None:
    """Collect citation counts for non-AE papers and write results."""
    data_path = Path(data_dir)
    artifacts_path = data_path / "assets" / "data" / "artifacts.json"
    build_dir = data_path / "_build"
    build_dir.mkdir(parents=True, exist_ok=True)
    out_path = build_dir / "baseline_citations.json"

    if not artifacts_path.exists():
        logger.error("artifacts.json not found at %s", artifacts_path)
        return None

    artifacts: list[dict] = load_json(artifacts_path)
    logger.info("Loaded %d AE artifacts (for exclusion)", len(artifacts))

    # Build set of AE titles per conference/year
    ae_titles = _build_ae_title_set(artifacts)
    logger.info("AE papers span %d conference/year combinations", len(ae_titles))

    # Find non-AE papers from DBLP
    non_ae_papers = _find_non_ae_papers(ae_titles)
    logger.info("%d non-AE papers to fetch citations for", len(non_ae_papers))

    if not non_ae_papers:
        logger.warning("No non-AE papers found — is DBLP data extracted?")
        save_json(out_path, [])
        return []

    # Deduplicate by normalized title (same paper can appear in DBLP twice)
    seen_titles: set[str] = set()
    unique: list[dict] = []
    for p in non_ae_papers:
        norm = normalize_title(p.get("title", ""))
        if norm and norm not in seen_titles:
            seen_titles.add(norm)
            unique.append(p)
    logger.info("%d unique non-AE papers after dedup", len(unique))

    # Fetch citations
    session = create_session()

    s2_disabled = os.environ.get("DISABLE_SEMANTIC_SCHOLAR", "").strip() == "1"
    s2_timeout_failures = 0

    entries: list[dict] = []
    cached_count = 0
    fetched_count = 0

    for i, paper in enumerate(unique):
        title = paper.get("title", "")
        norm = normalize_title(title)
        doi = paper.get("doi", "")

        # Cache key uses baseline namespace to avoid collision with AE cache
        cache_k = cache_key(doi) if doi else cache_key(norm)
        cached = read_cache(CITATION_CACHE_DIR, cache_k, CITATION_CACHE_TTL, BASELINE_CACHE_NS)
        if cached is not _MISSING:
            entries.append(cached)
            cached_count += 1
            continue

        # Lookup via APIs
        openalex_count: int | None = None
        s2_count: int | None = None
        source = ""
        openalex_id = ""

        if doi:
            time.sleep(OPENALEX_DELAY)
            oa = openalex_lookup(doi, session)
            if oa:
                openalex_count = oa["cited_by_count"]
                openalex_id = oa["openalex_id"]
                source = "openalex_doi"

            if not s2_disabled:
                time.sleep(S2_DELAY)
                s2_count = s2_lookup(doi, session)
                if s2_count is None and s2_timeout_failures < S2_MAX_TIMEOUT_FAILURES:
                    s2_timeout_failures += 1
                    if s2_timeout_failures >= S2_MAX_TIMEOUT_FAILURES:
                        logger.warning(
                            "Disabling Semantic Scholar after %d timeout failures",
                            s2_timeout_failures,
                        )
                        s2_disabled = True
        else:
            # No DOI — fall back to OpenAlex title search
            time.sleep(OPENALEX_DELAY)
            oa = openalex_title_search(title, session)
            if oa:
                openalex_count = oa["cited_by_count"]
                openalex_id = oa["openalex_id"]
                source = "openalex_title"

        cited_by = best_citation_count(openalex_count, s2_count)

        entry = {
            "title": title,
            "conference": paper.get("conference", ""),
            "year": paper.get("year", 0),
            "category": paper.get("category", ""),
            "ae_paper": False,
            "paper_doi": doi,
            "openalex_id": openalex_id,
            "cited_by_count": cited_by,
            "citations_openalex": openalex_count,
            "citations_semantic_scholar": s2_count,
            "source": source,
        }
        entries.append(entry)
        write_cache(CITATION_CACHE_DIR, cache_k, entry, BASELINE_CACHE_NS)
        fetched_count += 1

        if (i + 1) % 100 == 0:
            logger.info(
                "Progress: %d/%d (cached=%d, fetched=%d)",
                i + 1,
                len(unique),
                cached_count,
                fetched_count,
            )

    logger.info(
        "Done: %d entries (cached=%d, fetched=%d, with_citations=%d)",
        len(entries),
        cached_count,
        fetched_count,
        sum(1 for e in entries if isinstance(e.get("cited_by_count"), int) and e["cited_by_count"] > 0),
    )

    save_json(out_path, entries)
    logger.info("Wrote %s", out_path)

    return entries


# ── CLI ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect citation counts for non-AE papers (baseline comparison)")
    parser.add_argument("--data_dir", required=True, help="Path to reprodb.github.io")
    args = parser.parse_args()

    generate(args.data_dir)


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()

    main()
