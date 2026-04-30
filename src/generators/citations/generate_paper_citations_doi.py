"""Generate paper citation counts via OpenAlex and Semantic Scholar (DOI-based).

Reads:
  assets/data/artifacts.json          — paper DOIs (``paper_url`` field)

Writes:
  _build/paper_citations.json         — per-paper citation data
  _build/citation_history.json        — time-series history (append-only)

Usage::

    python -m src.generators.generate_paper_citations_doi --data_dir ../reprodb.github.io/src
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import time
from datetime import datetime, timezone
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
    extract_paper_doi,
    openalex_lookup,
    openalex_title_search,
    s2_lookup,
)
from src.utils.io.cache import _MISSING, read_cache, write_cache
from src.utils.io.io import load_json, save_json
from src.utils.normalization.conference import conf_area, normalize_title

logger = logging.getLogger(__name__)

# ── Backward-compatible aliases ──────────────────────────────────────────────
# These names were previously defined here and are imported by tests and
# generate_baseline_citations.  Keep thin re-exports so existing call-sites
# continue to work while new code imports from ``src.utils.citation_apis``.
CACHE_DIR = CITATION_CACHE_DIR
CACHE_NS = "openalex"
CACHE_TTL = CITATION_CACHE_TTL
_OPENALEX_DELAY = OPENALEX_DELAY
_S2_DELAY = S2_DELAY
_S2_MAX_TIMEOUT_FAILURES = S2_MAX_TIMEOUT_FAILURES
_extract_paper_doi = extract_paper_doi
_cache_key = cache_key
_openalex_lookup = openalex_lookup
_openalex_title_search = openalex_title_search
_s2_lookup = s2_lookup


# ── Main generator ───────────────────────────────────────────────────────────


def generate(data_dir: str) -> list[dict] | None:
    """Collect paper citation counts and write results + history."""
    data_path = Path(data_dir)
    artifacts_path = data_path / "assets" / "data" / "artifacts.json"
    build_dir = data_path / "_build"
    build_dir.mkdir(parents=True, exist_ok=True)
    out_path = build_dir / "paper_citations.json"

    if not artifacts_path.exists():
        logger.error("artifacts.json not found at %s", artifacts_path)
        return None

    artifacts: list[dict] = load_json(artifacts_path)
    logger.info("Loaded %d artifacts", len(artifacts))

    # Deduplicate by normalized title
    seen_titles: set[str] = set()
    unique: list[dict] = []
    for a in artifacts:
        norm = normalize_title(a.get("title", ""))
        if norm and norm not in seen_titles:
            seen_titles.add(norm)
            unique.append(a)
    logger.info("%d unique papers to process", len(unique))

    session = create_session()

    s2_disabled = os.environ.get("DISABLE_SEMANTIC_SCHOLAR", "").strip() == "1"
    s2_timeout_failures = 0

    entries: list[dict] = []
    cached_count = 0
    fetched_count = 0

    for i, artifact in enumerate(unique):
        title = artifact.get("title", "")
        norm = normalize_title(title)
        doi = _extract_paper_doi(artifact.get("paper_url"))

        # Try cache first (keyed by DOI or normalized title)
        cache_k = _cache_key(doi) if doi else _cache_key(norm)
        cached = read_cache(CACHE_DIR, cache_k, CACHE_TTL, CACHE_NS)
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
            time.sleep(_OPENALEX_DELAY)
            oa = _openalex_lookup(doi, session)
            if oa:
                openalex_count = oa["cited_by_count"]
                openalex_id = oa["openalex_id"]
                source = "openalex_doi"

            if not s2_disabled:
                time.sleep(_S2_DELAY)
                s2_count = _s2_lookup(doi, session)
                if s2_count is None and s2_timeout_failures < _S2_MAX_TIMEOUT_FAILURES:
                    s2_timeout_failures += 1
                    if s2_timeout_failures >= _S2_MAX_TIMEOUT_FAILURES:
                        logger.warning("Disabling Semantic Scholar after %d timeout failures", s2_timeout_failures)
                        s2_disabled = True
        else:
            # No DOI — fall back to OpenAlex title search
            time.sleep(_OPENALEX_DELAY)
            oa = _openalex_title_search(title, session)
            if oa:
                openalex_count = oa["cited_by_count"]
                openalex_id = oa["openalex_id"]
                source = "openalex_title"

        cited_by = best_citation_count(openalex_count, s2_count)

        entry = {
            "title": title,
            "conference": artifact.get("conference", ""),
            "year": artifact.get("year", 0),
            "category": artifact.get("category", ""),
            "ae_paper": True,
            "paper_doi": doi,
            "openalex_id": openalex_id,
            "cited_by_count": cited_by,
            "citations_openalex": openalex_count,
            "citations_semantic_scholar": s2_count,
            "source": source,
        }
        entries.append(entry)
        write_cache(CACHE_DIR, cache_k, entry, CACHE_NS)
        fetched_count += 1

        if (i + 1) % 100 == 0:
            logger.info("Progress: %d/%d (cached=%d, fetched=%d)", i + 1, len(unique), cached_count, fetched_count)

    logger.info(
        "Done: %d entries (cached=%d, fetched=%d, with_citations=%d)",
        len(entries),
        cached_count,
        fetched_count,
        sum(1 for e in entries if isinstance(e.get("cited_by_count"), int) and e["cited_by_count"] > 0),
    )

    save_json(out_path, entries)
    logger.info("Wrote %s", out_path)

    # ── History tracking ─────────────────────────────────────────────────
    _update_history(entries, build_dir)

    return entries


# ── Citation history (append-only snapshots) ─────────────────────────────────


def _update_history(entries: list[dict], build_dir: Path) -> None:
    """Append a dated snapshot per paper to ``citation_history.json``.

    Mirrors the ``repo_stats_history.json`` pattern: each key is
    ``<conference>/<year>/<normalized_title_hash>`` and holds metadata
    plus a list of dated snapshots.
    """
    history_path = build_dir / "citation_history.json"
    history: dict = {}
    if history_path.exists():
        history = load_json(history_path)
        logger.info("Loaded citation history (%d papers)", len(history))

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    updated = 0

    for e in entries:
        if e.get("cited_by_count") is None:
            continue  # skip papers without any citation data

        # Stable key: conference/year/hash
        norm = normalize_title(e.get("title", ""))
        key = f"{e.get('conference', '')}/{e.get('year', 0)}/{hashlib.sha256(norm.encode()).hexdigest()[:12]}"

        if key not in history:
            history[key] = {
                "meta": {
                    "title": e.get("title", ""),
                    "conference": e.get("conference", ""),
                    "year": e.get("year", 0),
                    "area": conf_area(e.get("conference", "")),
                    "paper_doi": e.get("paper_doi", ""),
                },
                "snapshots": [],
            }

        # Update metadata DOI if we now have one
        if e.get("paper_doi") and not history[key]["meta"].get("paper_doi"):
            history[key]["meta"]["paper_doi"] = e["paper_doi"]

        snapshot = {
            "date": today,
            "cited_by_count": e["cited_by_count"],
            "citations_openalex": e.get("citations_openalex"),
            "citations_semantic_scholar": e.get("citations_semantic_scholar"),
        }

        snapshots = history[key]["snapshots"]
        if snapshots and snapshots[-1].get("date") == today:
            snapshots[-1] = snapshot  # idempotent same-day update
        else:
            snapshots.append(snapshot)
        updated += 1

    save_json(history_path, history)
    logger.info("Wrote citation history (%d papers, %d updated) to %s", len(history), updated, history_path)


# ── CLI ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Paper citation counts via OpenAlex/Semantic Scholar")
    parser.add_argument("--data_dir", required=True, help="Path to reprodb.github.io")
    args = parser.parse_args()

    generate(args.data_dir)


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()

    main()
