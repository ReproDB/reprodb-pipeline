"""
Generate artifact availability (liveness) data.

Checks whether artifact URLs are still reachable and writes per-artifact
availability results for analysis of decay rates by platform, age, and area.

Writes:
  assets/data/artifact_availability.json — per-artifact liveness results

Usage:
  python -m src.generators.generate_artifact_availability \
      --conf_regex '.*20[12][0-9]' --output_dir ../reprodb.github.io/src
"""

import argparse
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.scrapers.parse_results_md import get_ae_results
from src.utils.collection.test_artifact_repositories import check_artifact_exists
from src.utils.io.io import load_yaml, save_json
from src.utils.normalization.conference import conf_area as _conf_area
from src.utils.normalization.conference import parse_conf_year as _extract_conference_year

logger = logging.getLogger(__name__)
URL_KEYS = ["repository_url", "artifact_url"]


def _detect_platform(url):
    """Detect hosting platform from a URL."""
    if not url or not isinstance(url, str):
        return "unknown"
    url_lower = url.lower()
    if "github.com" in url_lower:
        return "GitHub"
    if "gitlab.com" in url_lower:
        return "GitLab"
    if "bitbucket.org" in url_lower:
        return "Bitbucket"
    if "zenodo.org" in url_lower:
        return "Zenodo"
    if "doi.org/10.5281" in url_lower or "doi.org/10.60517" in url_lower:
        return "Zenodo"
    if "figshare.com" in url_lower or "doi.org/10.6084" in url_lower:
        return "Figshare"
    if "doi.org" in url_lower:
        return "DOI-other"
    return "other"


def generate_availability(results: dict[str, list[dict]]) -> tuple[list[dict], dict, dict]:
    """Check URL liveness for all artifacts and produce per-artifact records.

    Returns a list of dicts, one per artifact, with fields:
      conference, year, area, title, url_key, url, platform, accessible
    """
    # Run parallel liveness checks
    results, counts, failed = check_artifact_exists(results, URL_KEYS)

    records = []
    for conf_year, artifacts in results.items():
        conf_name, year = _extract_conference_year(conf_year)
        if year is None:
            continue
        area = _conf_area(conf_name)
        for artifact in artifacts:
            title = artifact.get("title", "")
            for url_key in URL_KEYS:
                url = artifact.get(url_key, "")
                if not url or (isinstance(url, list) and not url):
                    continue
                if isinstance(url, list):
                    url = url[0]
                if not isinstance(url, str) or not url.strip():
                    continue
                exists_key = f"{url_key}_exists"
                accessible = artifact.get(exists_key, False)
                platform = _detect_platform(url)
                records.append(
                    {
                        "conference": conf_name,
                        "year": year,
                        "area": area,
                        "title": title,
                        "url_key": url_key,
                        "url": url,
                        "platform": platform,
                        "accessible": accessible,
                    }
                )

    return records, counts, failed


def build_summary(records: list[dict]) -> dict[str, Any]:
    """Aggregate availability records into summary statistics."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Overall
    total = len(records)
    accessible = sum(1 for r in records if r["accessible"])

    # By platform
    by_platform = defaultdict(lambda: {"total": 0, "accessible": 0})
    for r in records:
        by_platform[r["platform"]]["total"] += 1
        if r["accessible"]:
            by_platform[r["platform"]]["accessible"] += 1

    # By area
    by_area = defaultdict(lambda: {"total": 0, "accessible": 0})
    for r in records:
        by_area[r["area"]]["total"] += 1
        if r["accessible"]:
            by_area[r["area"]]["accessible"] += 1

    # By year
    by_year = defaultdict(lambda: {"total": 0, "accessible": 0})
    for r in records:
        by_year[r["year"]]["total"] += 1
        if r["accessible"]:
            by_year[r["year"]]["accessible"] += 1

    # By year × area
    by_year_area = defaultdict(lambda: defaultdict(lambda: {"total": 0, "accessible": 0}))
    for r in records:
        by_year_area[r["year"]][r["area"]]["total"] += 1
        if r["accessible"]:
            by_year_area[r["year"]][r["area"]]["accessible"] += 1

    # By year × platform
    by_year_platform = defaultdict(lambda: defaultdict(lambda: {"total": 0, "accessible": 0}))
    for r in records:
        by_year_platform[r["year"]][r["platform"]]["total"] += 1
        if r["accessible"]:
            by_year_platform[r["year"]][r["platform"]]["accessible"] += 1

    # By conference
    by_conf = defaultdict(lambda: {"total": 0, "accessible": 0})
    for r in records:
        by_conf[r["conference"]]["total"] += 1
        if r["accessible"]:
            by_conf[r["conference"]]["accessible"] += 1

    def _pct(d):
        return round(100 * d["accessible"] / d["total"], 1) if d["total"] > 0 else 0

    summary = {
        "checked_at": now,
        "total_urls": total,
        "accessible_urls": accessible,
        "accessibility_pct": round(100 * accessible / total, 1) if total > 0 else 0,
        "by_platform": {k: {**v, "pct": _pct(v)} for k, v in sorted(by_platform.items())},
        "by_area": {k: {**v, "pct": _pct(v)} for k, v in sorted(by_area.items())},
        "by_year": {str(k): {**v, "pct": _pct(v)} for k, v in sorted(by_year.items())},
        "by_year_area": {
            str(y): {a: {**d, "pct": _pct(d)} for a, d in sorted(data.items())}
            for y, data in sorted(by_year_area.items())
        },
        "by_year_platform": {
            str(y): {p: {**d, "pct": _pct(d)} for p, d in sorted(data.items())}
            for y, data in sorted(by_year_platform.items())
        },
        "by_conference": {k: {**v, "pct": _pct(v)} for k, v in sorted(by_conf.items())},
    }
    return summary


def main():
    parser = argparse.ArgumentParser(description="Check artifact URL availability and generate liveness report.")
    parser.add_argument(
        "--conf_regex",
        type=str,
        default=".*20[12][0-9]",
        help="Regular expression for conference names/years",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default=None,
        help="Output directory (website repo root)",
    )
    args = parser.parse_args()
    output_dir = args.output_dir

    # Try loading from cache first (same as generate_repo_stats.py)
    cache_path = None
    if output_dir:
        cache_path = Path(output_dir) / "_data" / "all_results_cache.yml"
    if not cache_path or not cache_path.exists():
        repo_root = Path(__file__).resolve().parent.parent.parent.parent
        cache_path = repo_root / ".cache" / "all_results_cache.yml"

    if cache_path.exists():
        logger.info(f"Loading cached results from {cache_path}...")
        all_results = load_yaml(cache_path, default={})
        all_results = {k: v for k, v in all_results.items() if re.search(args.conf_regex, k)}
        logger.info(
            f"Loaded {sum(len(v) for v in all_results.values())} artifacts "
            f"across {len(all_results)} conference-years (from cache)"
        )
    else:
        logger.info("Collecting artifact results (no cache found, scraping)...")
        sys_results = get_ae_results(args.conf_regex, "sys")
        sec_results = get_ae_results(args.conf_regex, "sec")
        all_results = {**sys_results, **sec_results}
        logger.info(
            f"Loaded {sum(len(v) for v in all_results.values())} artifacts from {len(all_results)} conference-years"
        )

    # Run availability checks
    records, counts, failed = generate_availability(all_results)

    # Build summary
    summary = build_summary(records)

    # Print overview
    logger.info(f"\nArtifact Availability Report ({summary['checked_at']})")
    logger.info(f"  Total URLs checked:  {summary['total_urls']}")
    logger.info(f"  Accessible:          {summary['accessible_urls']} ({summary['accessibility_pct']}%)")
    logger.info("\n  By platform:")
    for p, d in summary["by_platform"].items():
        logger.info(f"    {p:12s}: {d['accessible']:4d}/{d['total']:4d} ({d['pct']}%)")
    logger.info("\n  By area:")
    for a, d in summary["by_area"].items():
        logger.info(f"    {a:12s}: {d['accessible']:4d}/{d['total']:4d} ({d['pct']}%)")

    if failed:
        logger.error(f"\n  Failed URLs ({len(failed)}):")
        for url in failed[:20]:
            logger.info(f"    {url}")
        if len(failed) > 20:
            logger.error(f"    ... and {len(failed) - 20} more")

    # Write output
    if output_dir:
        out_path = Path(output_dir) / "assets/data/artifact_availability.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        output = {
            "summary": summary,
            "records": records,
        }
        save_json(out_path, output)
        logger.info(f"\n✓ Wrote {out_path}")


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()

    main()
