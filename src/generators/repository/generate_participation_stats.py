#!/usr/bin/env python3
"""
Generate AE participation statistics using the local DBLP XML file.

For each conference/year in the pipeline's artifacts data, this script:
1. Parses the DBLP XML to count ALL papers published at that venue/year.
2. Computes AE participation rate (ae_papers / total_papers).
3. Computes badge rates as % of all papers (not just AE papers).

Requires: data/dblp/dblp.xml.gz (downloaded by src/utils/download_dblp.py)

Writes:
  _data/participation_stats.yml     — per-conference/year participation data
  assets/data/participation_stats.json — same data in JSON

Usage:
  python -m src.generators.generate_participation_stats \
      --dblp_file data/dblp/dblp.xml.gz \
      --output_dir ../reprodb.github.io/src
"""

import argparse
import logging
from collections import defaultdict
from pathlib import Path

from src.utils.apis.dblp_extract import paper_count_by_venue_year
from src.utils.io.io import load_yaml, save_json, save_yaml
from src.utils.normalization.conference import conf_area

logger = logging.getLogger(__name__)


def _count_papers_from_dblp(dblp_file, target_conf_years):
    """Count papers per (conference, year) from pre-extracted DBLP data."""
    all_counts = paper_count_by_venue_year()
    counts = {k: v for k, v in all_counts.items() if k in target_conf_years}
    logger.info(f"Loaded DBLP paper counts from extraction cache ({len(counts)} conference/years)")
    return counts


def generate_participation_stats(dblp_file, output_dir):
    """Generate AE participation and badge-rate statistics."""

    if not Path(dblp_file).exists():
        logger.error(f"Error: DBLP file not found: {dblp_file}")
        logger.info("  Run python -m src.utils.download_dblp first.")
        return None

    output_dir = Path(output_dir)
    # Load artifacts_by_conference for per-conference badge counts
    abc_path = output_dir / "_data/artifacts_by_conference.yml"
    if not abc_path.exists():
        logger.error(f"Error: {abc_path} not found — run generate_statistics first")
        return None

    by_conference = load_yaml(abc_path)

    # Build per-conference/year AE counts and badge counts from pipeline data
    ae_data = {}  # (conf, year) -> {ae_papers, available, functional, ...}
    for conf in by_conference:
        name = conf["name"]
        for yd in conf["years"]:
            key = (name, yd["year"])
            ae_data[key] = {
                "ae_papers": yd["total"],
                "available": yd.get("available", 0),
                "functional": yd.get("functional", 0),
                "reproduced": yd.get("reproducible", 0),
                "category": conf["category"],
                "venue_type": conf.get("venue_type", "conference"),
            }

    # Parse DBLP XML for total paper counts
    dblp_counts = _count_papers_from_dblp(dblp_file, set(ae_data.keys()))

    # Build stats entries
    stats = []
    for (conf, year), info in sorted(ae_data.items()):
        total_papers = dblp_counts.get((conf, year))
        if not total_papers:
            logger.warning(f"  ⚠ {conf} {year}: no DBLP data — skipping")
            continue

        ae_papers = info["ae_papers"]
        if ae_papers > total_papers:
            logger.info(
                f"  ⚠ {conf} {year}: DBLP data incomplete ({total_papers} papers in DBLP, {ae_papers} AE papers) — skipping"
            )
            continue
        participation_rate = round(ae_papers / total_papers * 100, 1)
        area = conf_area(conf)

        entry = {
            "conference": conf,
            "year": year,
            "category": area,
            "venue_type": info["venue_type"],
            "total_papers": total_papers,
            "ae_papers": ae_papers,
            "participation_pct": participation_rate,
            "available": info["available"],
            "functional": info["functional"],
            "reproduced": info["reproduced"],
            "available_pct": round(info["available"] / total_papers * 100, 1),
            "functional_pct": round(info["functional"] / total_papers * 100, 1),
            "reproduced_pct": round(info["reproduced"] / total_papers * 100, 1),
        }
        stats.append(entry)
        logger.info(f"  {conf} {year}: {ae_papers}/{total_papers} = {participation_rate}% AE participation")

    if not stats:
        logger.info("No participation data generated")
        return None

    # Sort by conference then year
    stats.sort(key=lambda x: (x["conference"], x["year"]))

    # Compute area-level summaries
    area_year = defaultdict(lambda: defaultdict(lambda: {"ae": 0, "total": 0, "avail": 0, "func": 0, "repro": 0}))
    for s in stats:
        ay = area_year[s["category"]][s["year"]]
        ay["ae"] += s["ae_papers"]
        ay["total"] += s["total_papers"]
        ay["avail"] += s["available"]
        ay["func"] += s["functional"]
        ay["repro"] += s["reproduced"]

    area_summaries = {}
    for area in sorted(area_year):
        years = sorted(area_year[area])
        area_summaries[area] = {
            "years": years,
            "participation_pct": [
                round(area_year[area][y]["ae"] / area_year[area][y]["total"] * 100, 1) for y in years
            ],
            "available_pct": [round(area_year[area][y]["avail"] / area_year[area][y]["total"] * 100, 1) for y in years],
            "functional_pct": [round(area_year[area][y]["func"] / area_year[area][y]["total"] * 100, 1) for y in years],
            "reproduced_pct": [
                round(area_year[area][y]["repro"] / area_year[area][y]["total"] * 100, 1) for y in years
            ],
        }

    output = {
        "by_conference_year": stats,
        "by_area": area_summaries,
    }

    # Write outputs
    (output_dir / "_data").mkdir(parents=True, exist_ok=True)
    (output_dir / "assets/data").mkdir(parents=True, exist_ok=True)

    yml_path = output_dir / "_data/participation_stats.yml"
    save_yaml(yml_path, output)

    json_path = output_dir / "assets/data/participation_stats.json"
    save_json(json_path, output)

    logger.info(f"\n✅ Participation stats: {len(stats)} conference/year entries")
    logger.info(f"   → {yml_path}")
    logger.info(f"   → {json_path}")

    return output


def main():
    parser = argparse.ArgumentParser(description="Generate AE participation statistics from DBLP XML paper counts")
    parser.add_argument(
        "--dblp_file",
        type=str,
        default="data/dblp/dblp.xml.gz",
        help="Path to dblp.xml.gz",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        required=True,
        help="Output directory (same as generate_statistics output_dir)",
    )
    args = parser.parse_args()
    generate_participation_stats(args.dblp_file, args.output_dir)


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()

    main()
