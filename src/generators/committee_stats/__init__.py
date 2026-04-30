"""Committee statistics package.

Three submodules:
- :mod:`scraping` — fetch & clean committee data from sysartifacts/secartifacts
  with web/local fallbacks.
- :mod:`classification` — country/continent/institution classification and
  per-area aggregation, member rankings, institution timelines.
- :mod:`charting` — matplotlib SVG chart generation.

The :func:`generate_committee_data` function ties them together and is the
public entry point used by the CLI orchestrator
:mod:`src.generators.generate_committee_stats`.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from src.utils.io.io import save_json, save_yaml
from src.utils.normalization.conference import parse_conf_year as _extract_conf_year

from .chair_stats import compute_chair_stats
from .charting import generate_committee_charts
from .classification import (
    _aggregate_across_conferences,
    _build_yearly_series,
    _compute_institution_timeline,
    _compute_member_stats,
    _top_n,
    classify_committees,
    classify_member,
)
from .classification import (
    _build_university_index as _build_university_index,  # noqa: PLC0414
)
from .classification import (
    _clean_affiliation as _clean_affiliation,  # noqa: PLC0414
)
from .scraping import (
    _clean_committee as _clean_committee,  # noqa: PLC0414  (re-export for legacy use)
)
from .scraping import scrape_committees

logger = logging.getLogger(__name__)

__all__ = [
    "classify_committees",
    "classify_member",
    "generate_committee_charts",
    "generate_committee_data",
]


def generate_committee_data(conf_regex: str, output_dir):
    """Scrape committees, classify, write outputs, and render charts."""

    # ── 1. Scrape committees (sysartifacts + secartifacts + alt sources) ─────
    all_results, conf_to_area = scrape_committees(conf_regex)

    if not all_results:
        logger.warning("  No committee data found — skipping committee stats.")
        return None

    # ── 2. Classify by country / continent / institution ─────────────────────
    logger.info("  Classifying committee members...")
    classified = classify_committees(all_results)

    if classified["failed"]:
        logger.error(f"  ⚠️  Could not classify {len(classified['failed'])} members")

    # ── 3. Aggregate statistics ──────────────────────────────────────────────
    country_all, country_sys, country_sec = _aggregate_across_conferences(classified["by_country"], conf_to_area)
    continent_all, continent_sys, continent_sec = _aggregate_across_conferences(
        classified["by_continent"], conf_to_area
    )
    inst_all, inst_sys, inst_sec = _aggregate_across_conferences(classified["by_institution"], conf_to_area)

    country_years_all, country_years_sys, country_years_sec = _build_yearly_series(
        classified["by_country"], conf_to_area
    )
    continent_years_all, continent_years_sys, continent_years_sec = _build_yearly_series(
        classified["by_continent"], conf_to_area
    )

    committee_sizes: list = []
    for conf_year in sorted(all_results.keys()):
        conf_name, year = _extract_conf_year(conf_year)
        area = conf_to_area.get(conf_year, "unknown")
        committee_sizes.append(
            {
                "conference": conf_name,
                "year": year,
                "conf_year": conf_year,
                "area": area,
                "size": len(all_results[conf_year]),
            }
        )

    total_members = sum(len(m) for m in all_results.values())
    total_systems = sum(len(m) for cy, m in all_results.items() if conf_to_area.get(cy) == "systems")
    total_security = sum(len(m) for cy, m in all_results.items() if conf_to_area.get(cy) == "security")

    # ── 3b. AE member rankings ────────────────────────────────────────────
    logger.info("  Computing AE member rankings...")
    all_members, sys_members, sec_members, member_summary = _compute_member_stats(all_results, conf_to_area, classified)
    logger.info(
        f"    Found {member_summary['total_members']} unique members "
        f"({member_summary['total_chairs']} include chair roles)"
    )

    # ── 3c. AE chair statistics ─────────────────────────────────────────────
    logger.info("  Computing AE chair statistics...")
    chair_data = compute_chair_stats(all_members, sys_members, sec_members, all_results, conf_to_area)
    logger.info(
        f"    Found {chair_data['summary']['total_chairs']} unique chairs "
        f"({chair_data['summary']['repeat_chairs']} repeat, "
        f"{chair_data['summary']['cross_conference_chairs']} cross-conference)"
    )

    # ── 3d. Institution timeline ─────────────────────────────────────────────
    logger.info("  Computing institution timeline...")
    inst_timeline = _compute_institution_timeline(classified, conf_to_area)
    logger.info(f"    Tracked {len(inst_timeline['unique_by_year'])} years of institution data")

    # ── 4. Build output structures ───────────────────────────────────────────
    committee_summary = {
        "last_updated": datetime.now().strftime("%Y-%m-%d"),
        "total_members": total_members,
        "total_systems": total_systems,
        "total_security": total_security,
        "total_conferences": len(all_results),
        "total_countries": len(country_all),
        "total_continents": len(continent_all),
        "total_institutions": len(inst_all),
        "unique_members": member_summary["total_members"],
        "unique_members_systems": member_summary["total_members_systems"],
        "unique_members_security": member_summary["total_members_security"],
        "unique_members_both": member_summary["total_members_both"],
        "recurring_chairs": member_summary["total_chairs"],
        "top_countries": [{"name": k, "count": v} for k, v in _top_n(country_all, 15)],
        "top_countries_systems": [{"name": k, "count": v} for k, v in _top_n(country_sys, 15)],
        "top_countries_security": [{"name": k, "count": v} for k, v in _top_n(country_sec, 15)],
        "top_continents": [{"name": k, "count": v} for k, v in _top_n(continent_all, 10)],
        "top_continents_systems": [{"name": k, "count": v} for k, v in _top_n(continent_sys, 10)],
        "top_continents_security": [{"name": k, "count": v} for k, v in _top_n(continent_sec, 10)],
        "top_institutions": [{"name": k, "count": v} for k, v in _top_n(inst_all, 20)],
        "top_institutions_systems": [{"name": k, "count": v} for k, v in _top_n(inst_sys, 20)],
        "top_institutions_security": [{"name": k, "count": v} for k, v in _top_n(inst_sec, 20)],
        "institution_timeline": inst_timeline["unique_by_year"],
        "committee_sizes": committee_sizes,
    }

    detail_json = {
        "summary": {
            "total_members": total_members,
            "total_systems": total_systems,
            "total_security": total_security,
            "total_countries": len(country_all),
            "total_continents": len(continent_all),
            "total_institutions": len(inst_all),
        },
        "by_country": {
            "overall": [{"name": k, "count": v} for k, v in sorted(country_all.items(), key=lambda x: -x[1])],
            "systems": [{"name": k, "count": v} for k, v in sorted(country_sys.items(), key=lambda x: -x[1])],
            "security": [{"name": k, "count": v} for k, v in sorted(country_sec.items(), key=lambda x: -x[1])],
        },
        "by_continent": {
            "overall": [{"name": k, "count": v} for k, v in sorted(continent_all.items(), key=lambda x: -x[1])],
            "systems": [{"name": k, "count": v} for k, v in sorted(continent_sys.items(), key=lambda x: -x[1])],
            "security": [{"name": k, "count": v} for k, v in sorted(continent_sec.items(), key=lambda x: -x[1])],
        },
        "by_institution": {
            "overall": [{"name": k, "count": v} for k, v in sorted(inst_all.items(), key=lambda x: -x[1])],
            "systems": [{"name": k, "count": v} for k, v in sorted(inst_sys.items(), key=lambda x: -x[1])],
            "security": [{"name": k, "count": v} for k, v in sorted(inst_sec.items(), key=lambda x: -x[1])],
        },
        "by_year": {
            "country": {str(y): dict(c) for y, c in sorted(country_years_all.items())},
            "country_systems": {str(y): dict(c) for y, c in sorted(country_years_sys.items())},
            "country_security": {str(y): dict(c) for y, c in sorted(country_years_sec.items())},
            "continent": {str(y): dict(c) for y, c in sorted(continent_years_all.items())},
            "continent_systems": {str(y): dict(c) for y, c in sorted(continent_years_sys.items())},
            "continent_security": {str(y): dict(c) for y, c in sorted(continent_years_sec.items())},
        },
        "committee_sizes": committee_sizes,
        "failed_classifications": classified["failed"],
    }

    # ── 5. Write output files ────────────────────────────────────────────────
    if output_dir:
        output_dir = Path(output_dir)
        (output_dir / "_data").mkdir(parents=True, exist_ok=True)
        (output_dir / "assets/data").mkdir(parents=True, exist_ok=True)
        (output_dir / "assets/charts").mkdir(parents=True, exist_ok=True)

        yml_path = output_dir / "_data/committee_stats.yml"
        save_yaml(yml_path, committee_summary)
        logger.info(f"  Wrote {yml_path}")

        json_path = output_dir / "assets/data/committee_stats.json"
        save_json(json_path, detail_json)
        logger.info(f"  Wrote {json_path}")

        ae_all_path = output_dir / "assets/data/ae_members.json"
        save_json(ae_all_path, all_members)
        logger.info(f"  Wrote {ae_all_path} ({len(all_members)} members)")

        ae_sys_path = output_dir / "assets/data/systems_ae_members.json"
        save_json(ae_sys_path, sys_members)
        logger.info(f"  Wrote {ae_sys_path} ({len(sys_members)} members)")

        ae_sec_path = output_dir / "assets/data/security_ae_members.json"
        save_json(ae_sec_path, sec_members)
        logger.info(f"  Wrote {ae_sec_path} ({len(sec_members)} members)")

        # ── 5b. Chair statistics JSON files ──────────────────────────────────
        chairs_all_path = output_dir / "assets/data/ae_chairs.json"
        save_json(chairs_all_path, chair_data["chairs_all"])
        logger.info(f"  Wrote {chairs_all_path} ({len(chair_data['chairs_all'])} chairs)")

        chairs_sys_path = output_dir / "assets/data/systems_ae_chairs.json"
        save_json(chairs_sys_path, chair_data["chairs_systems"])
        logger.info(f"  Wrote {chairs_sys_path} ({len(chair_data['chairs_systems'])} chairs)")

        chairs_sec_path = output_dir / "assets/data/security_ae_chairs.json"
        save_json(chairs_sec_path, chair_data["chairs_security"])
        logger.info(f"  Wrote {chairs_sec_path} ({len(chair_data['chairs_security'])} chairs)")

        chair_stats_path = output_dir / "assets/data/chair_stats.json"
        save_json(
            chair_stats_path,
            {
                "summary": chair_data["summary"],
                "chair_teams": chair_data["chair_teams"],
                "pipeline": chair_data["pipeline"],
                "retention": chair_data["retention"],
                "cross_conference": chair_data["cross_conference"],
            },
        )
        logger.info(f"  Wrote {chair_stats_path}")

        # Add chair summary to the YAML data for Jekyll templates
        committee_summary["chair_stats"] = chair_data["summary"]

        # Re-write the YAML with chair stats included
        save_yaml(yml_path, committee_summary)

        build_dir = output_dir / "_build"
        build_dir.mkdir(parents=True, exist_ok=True)
        inst_timeline_path = build_dir / "institution_timeline.json"
        save_json(inst_timeline_path, inst_timeline)
        logger.info(f"  Wrote {inst_timeline_path}")

        # ── 6. Generate charts ───────────────────────────────────────────────
        generate_committee_charts(committee_summary, detail_json, output_dir, inst_timeline=inst_timeline)

    logger.info(
        f"  Committee stats: {total_members} members from "
        f"{len(country_all)} countries, {len(continent_all)} continents, "
        f"{len(inst_all)} institutions"
    )
    logger.info(
        f"  Unique members: {member_summary['total_members']} "
        f"(sys: {member_summary['total_members_systems']}, "
        f"sec: {member_summary['total_members_security']}, "
        f"chairs: {member_summary['total_chairs']})"
    )

    return detail_json
