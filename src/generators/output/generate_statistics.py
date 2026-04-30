#!/usr/bin/env python3
"""
Generate comprehensive statistics and data files for the ReproDB website.
This script collects data from both sysartifacts and secartifacts, processes it,
and generates YAML/JSON files for Jekyll to render.
"""

import argparse
import logging
import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from src.utils.io.io import save_validated_json, save_yaml

from src.models import SCHEMA_VERSION
from src.models.artifacts.artifacts import Artifact
from src.models.aggregates.summary import Summary
from src.scrapers.acm_scrape import (
    get_acm_conferences,
)
from src.scrapers.acm_scrape import (
    scrape_conference_year as acm_scrape_conference_year,
)
from src.scrapers.acm_scrape import (
    to_pipeline_format as acm_to_pipeline_format,
)
from src.scrapers.parse_results_md import get_ae_results
from src.scrapers.repo_utils import get_conferences_from_prefix
from src.scrapers.usenix_scrape import scrape_conference_year, to_pipeline_format
from src.utils.normalization.conference import CONF_DISPLAY_NAMES, ensure_conference_pages
from src.utils.normalization.conference import parse_conf_year as extract_conference_name

logger = logging.getLogger(__name__)


# ── Auto-generated conference pages ─────────────────────────────────────────

_CONFERENCE_PAGE_TEMPLATE = """\
---
title: "{display_name}"
permalink: /{area}/{slug}.html
conf_name: "{conf_upper}"
conf_display_name: "{display_name}"
conf_category: "{area}"
---

{{%- include conference_page.html -%}}
"""


def _generate_conference_pages(
    output_dir: str,
    systems_confs: list[str],
    security_confs: list[str],
) -> None:
    """Create conference ``.md`` pages for every known conference.

    Pages are written to ``{output_dir}/content/systems/`` and
    ``{output_dir}/content/security/``.  Existing pages are only overwritten when
    their content differs so that manual edits *not* covered by the
    template are preserved for non-auto-generated pages.
    """
    for area, confs in [("systems", systems_confs), ("security", security_confs)]:
        area_dir = Path(output_dir) / "content" / area
        area_dir.mkdir(parents=True, exist_ok=True)
        for conf_upper in confs:
            slug = conf_upper.lower()
            display = CONF_DISPLAY_NAMES.get(conf_upper, conf_upper)
            content = _CONFERENCE_PAGE_TEMPLATE.format(
                display_name=display,
                area=area,
                slug=slug,
                conf_upper=conf_upper,
            )
            path = area_dir / f"{slug}.md"
            # Only write when the file is absent or differs.
            if path.exists():
                with open(path) as fh:
                    if fh.read() == content:
                        continue
            with open(path, "w") as fh:
                fh.write(content)
            logger.info("Auto-generated conference page %s/%s.md", area, slug)


# Workshops (as opposed to conferences) — used for visual distinction
WORKSHOPS = {"woot", "systex"}

# Mapping from sysartifacts/secartifacts conference prefix to the USENIX URL
# conference short-name and category.  When a conference directory exists on
# sysartifacts or secartifacts but has NO results.md, the pipeline will
# automatically try to scrape it from usenix.org using this mapping.
USENIX_CONF_MAP = {
    "fast": ("fast", "systems"),
    "osdi": ("osdi", "systems"),
    "atc": ("atc", "systems"),
    "usenixsec": ("usenixsecurity", "security"),
}


def count_badges(artifacts):
    """Count occurrences of each badge type across *artifacts*.

    Handles both list-valued and comma-separated string badge fields.
    Returns a dict with keys: available, functional, reproducible, reusable, replicated.
    """
    badges = {"available": 0, "functional": 0, "reproducible": 0, "reusable": 0, "replicated": 0}

    for artifact in artifacts:
        if "badges" in artifact and artifact["badges"]:
            badge_list = artifact["badges"]
            if isinstance(badge_list, str):
                badge_list = [b.strip() for b in badge_list.split(",")]
            for badge in badge_list:
                badge_lower = badge.lower()
                if "available" in badge_lower:
                    badges["available"] += 1
                if "functional" in badge_lower:
                    badges["functional"] += 1
                if "reproduc" in badge_lower or "replicated" in badge_lower or "reusable" in badge_lower:
                    badges["reproducible"] += 1
                if "reusable" in badge_lower:
                    badges["reusable"] += 1

    return badges


_DOI_RE = re.compile(r"10\.[0-9]{4,9}/[-._;()/:A-Za-z0-9]+")
_ARTIFACT_DOI_PREFIXES = ("10.5281/zenodo.", "10.6084/m9.figshare.")


def _extract_artifact_doi(urls: list[str]) -> str:
    """Return the first artifact-repository DOI found in *urls*, or ``""``."""
    for url in urls:
        m = _DOI_RE.search(url)
        if m:
            doi = m.group(0).rstrip(".,);").lower()
            if doi.startswith(_ARTIFACT_DOI_PREFIXES):
                return doi
        # Zenodo record URL without explicit DOI
        zm = re.search(r"zenodo\.org/(?:record|records)/(\d+)", url, re.I)
        if zm:
            return f"10.5281/zenodo.{zm.group(1)}"
    return ""


def _collect_artifact_urls(artifact: dict) -> list[str]:
    """Collect and deduplicate all artifact-related URLs from a raw artifact dict."""
    urls: list[str] = []
    # Repository URLs (GitHub, GitLab, Bitbucket, etc.)
    for repo_key in ("repository_url", "github_url", "second_repository_url", "bitbucket_url"):
        url = artifact.get(repo_key, "")
        if url:
            urls.append(url)
    # Primary artifact URL
    if artifact.get("artifact_url"):
        urls.append(artifact["artifact_url"])
    # List-valued artifact URL fields
    if isinstance(artifact.get("artifact_urls"), list):
        urls.extend([u for u in artifact["artifact_urls"] if u])
    if isinstance(artifact.get("additional_urls"), list):
        urls.extend([u for u in artifact["additional_urls"] if u])
    # Normalize artifact_doi -> DOI URL
    artifact_doi = artifact.get("artifact_doi", "")
    if artifact_doi:
        if not artifact_doi.startswith("http"):
            artifact_doi = f"https://doi.org/{artifact_doi}"
        urls.append(artifact_doi)
    # Collect miscellaneous URL fields
    for url_key in (
        "cloudlab_url",
        "web_url",
        "scripts_url",
        "jupyter_url",
        "vm_url",
        "proof_url",
        "data_url",
    ):
        extra_url = artifact.get(url_key, "")
        if extra_url:
            urls.append(extra_url)
    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for u in urls:
        if isinstance(u, str) and u and u not in seen:
            seen.add(u)
            deduped.append(u)
    return deduped


def _build_artifact_entry(
    artifact: dict,
    conf_name: str,
    category: str,
    year: int,
    conf_year: str,
    sec_results: dict,
    sys_results: dict,
) -> dict:
    """Build a normalized artifact entry dict from a raw scraped artifact."""
    raw_badges = artifact.get("badges", [])
    if isinstance(raw_badges, str):
        raw_badges = [b.strip() for b in raw_badges.split(",") if b.strip()]

    entry = {
        "conference": conf_name.upper(),
        "category": category,
        "year": year,
        "title": artifact.get("title", "Unknown"),
        "badges": raw_badges,
        "artifact_urls": _collect_artifact_urls(artifact),
        "doi": _extract_artifact_doi(_collect_artifact_urls(artifact)),
    }

    # Normalize paper_url: merge doi and paper_doi fields
    paper_url = artifact.get("paper_url", "")
    if not paper_url:
        raw_doi = artifact.get("doi", "")
        if raw_doi:
            paper_url = f"https://doi.org/{raw_doi}" if not raw_doi.startswith("http") else raw_doi
    if not paper_url:
        raw_paper_doi = artifact.get("paper_doi", "")
        if raw_paper_doi:
            paper_url = (
                f"https://doi.org/10.1145/{raw_paper_doi}"
                if not raw_paper_doi.startswith("10.")
                else f"https://doi.org/{raw_paper_doi}"
            )
    if paper_url:
        entry["paper_url"] = paper_url

    appendix_url = artifact.get("appendix_url", "")
    if appendix_url:
        if not appendix_url.startswith("http"):
            if conf_year in sec_results:
                appendix_url = f"https://secartifacts.github.io/{conf_year}/{appendix_url}"
            elif conf_year in sys_results:
                appendix_url = f"https://sysartifacts.github.io/{conf_year}/{appendix_url}"
        entry["appendix_url"] = appendix_url

    award = artifact.get("award", "")
    if award:
        entry["award"] = award
    return entry


def generate_statistics(conf_regex=".*20[12][0-9]", output_dir=None):
    """
    Generate comprehensive statistics from sys and sec artifacts.

    Args:
        conf_regex: Regex to match conference names
        output_dir: Directory to write output files (default: current directory)

    Returns:
        Dictionary with all generated data
    """

    skip_usenix = os.getenv("SKIP_USENIX_SCRAPE", "").strip().lower() in {"1", "true", "yes"}

    # Collecting artifact data from both sources
    sys_results = get_ae_results(conf_regex, "sys")
    sec_results = get_ae_results(conf_regex, "sec")

    # Track all discovered conference dirs (for coverage table)
    sys_all_dirs = {item["name"] for item in get_conferences_from_prefix("sys") if re.search(conf_regex, item["name"])}
    sec_all_dirs = {item["name"] for item in get_conferences_from_prefix("sec") if re.search(conf_regex, item["name"])}

    # Auto-create website pages for newly discovered conferences
    ensure_conference_pages(sys_dirs=sys_all_dirs, sec_dirs=sec_all_dirs)

    # --- Automatic USENIX fallback ---
    # For every sysartifacts / secartifacts directory that has NO results,
    # check if it maps to a USENIX conference and try scraping from usenix.org.
    usenix_results = {}
    usenix_categories = {}  # conf_year -> category
    parsed_keys = set(sys_results.keys()) | set(sec_results.keys())

    for dir_name in sorted(sys_all_dirs | sec_all_dirs):
        if dir_name in parsed_keys:
            continue  # already have results from sysartifacts/secartifacts
        conf_name, year = extract_conference_name(dir_name)
        if year is None:
            continue
        conf_lower = conf_name.lower()
        if conf_lower not in USENIX_CONF_MAP:
            continue
        usenix_short, category = USENIX_CONF_MAP[conf_lower]
        if not re.search(conf_regex, dir_name):
            continue
        if skip_usenix:
            logger.warning(f"Skipping USENIX {conf_name.upper()} {year} (SKIP_USENIX_SCRAPE set)")
            continue
        logger.info(f"Scraping USENIX {conf_name.upper()} {year} (fallback for missing sysartifacts results)...")
        try:
            artifacts = scrape_conference_year(usenix_short, year, max_workers=4, delay=0.3)
            pipeline_arts = to_pipeline_format(artifacts)
            if pipeline_arts:
                usenix_results[dir_name] = pipeline_arts
                usenix_categories[dir_name] = category
                logger.info(f"  Got {len(pipeline_arts)} artifacts with badges")
            else:
                logger.info("  No artifacts with badges found")
        except Exception as e:
            logger.error(f"  Error scraping {conf_name.upper()} {year}: {e}")

    # --- ACM conference scraping (independent of sysartifacts/secartifacts) ---
    # ACM conferences like CCS are not tracked on sysartifacts or secartifacts.
    # We scrape them directly via DBLP + ACM DL.
    acm_results = {}
    acm_categories = {}  # conf_year -> category
    acm_confs = get_acm_conferences()
    # Don't scrape conferences already handled by sysartifacts (e.g. SOSP)
    handled_confs = set()
    for d in sys_all_dirs | sec_all_dirs | set(usenix_results.keys()):
        cn, _ = extract_conference_name(d)
        if cn:
            handled_confs.add(cn.lower())

    for acm_key, acm_meta in acm_confs.items():
        if acm_key in handled_confs:
            continue  # already handled by sysartifacts/secartifacts
        category = acm_meta["category"]
        for year in sorted(acm_meta.get("proceedings_dois", {}).keys()):
            conf_year_key = f"{acm_key}{year}"
            if not re.search(conf_regex, conf_year_key):
                continue
            logger.info(f"Scraping ACM {acm_meta['display_name']} {year} (DBLP + ACM DL)...")
            try:
                artifacts = acm_scrape_conference_year(acm_key, year, max_workers=4, delay=0.5)
                pipeline_arts = acm_to_pipeline_format(artifacts)
                if pipeline_arts:
                    acm_results[conf_year_key] = pipeline_arts
                    acm_categories[conf_year_key] = category
                    logger.info(f"  Got {len(pipeline_arts)} artifacts with badges")
                else:
                    # Even without badges, record the conference as discovered
                    acm_results[conf_year_key] = []
                    acm_categories[conf_year_key] = category
                    logger.info("  No artifacts with badges found (ACM DL may be blocked)")
            except Exception as e:
                logger.error(f"  Error scraping {acm_meta['display_name']} {year}: {e}")

    # Tag each result by source
    sys_conf_years = set(sys_results.keys())
    usenix_conf_years = set(usenix_results.keys())
    acm_conf_years = set(acm_results.keys())

    # Combine results (sys + sec + usenix + acm)
    all_results = {**sys_results, **sec_results, **usenix_results, **acm_results}

    # Persist raw results so downstream steps (e.g. generate_repo_stats) can
    # skip re-scraping.  The cache file is written next to the other _data/
    # YAML files when an output_dir is given, otherwise to a local .cache dir.
    _cache_dir = Path(output_dir) / "_data" if output_dir else Path(".cache")
    _cache_dir.mkdir(parents=True, exist_ok=True)
    _cache_path = _cache_dir / "all_results_cache.yml"
    save_yaml(_cache_path, all_results)
    logger.info(f"Cached raw results ({sum(len(v) for v in all_results.values())} artifacts) → {_cache_path}")

    # Organize by conference
    by_conference = defaultdict(lambda: {"years": [], "total_artifacts": 0, "category": "unknown"})
    all_artifacts = []
    years_set = set()
    conferences_set = set()
    systems_artifacts_count = 0
    security_artifacts_count = 0

    for conf_year, artifacts in all_results.items():
        conf_name, year = extract_conference_name(conf_year)

        # Determine category by source
        if conf_year in sys_conf_years:
            category = "systems"
            systems_artifacts_count += len(artifacts)
        elif conf_year in usenix_conf_years:
            category = usenix_categories.get(conf_year, "systems")
            if category == "systems":
                systems_artifacts_count += len(artifacts)
            else:
                security_artifacts_count += len(artifacts)
        elif conf_year in acm_conf_years:
            category = acm_categories.get(conf_year, "security")
            if category == "systems":
                systems_artifacts_count += len(artifacts)
            else:
                security_artifacts_count += len(artifacts)
        else:
            category = "security"
            security_artifacts_count += len(artifacts)

        if year:
            years_set.add(int(year))
            conferences_set.add(conf_name.upper())

            badges = count_badges(artifacts)

            year_data = {
                "year": int(year),
                "total": len(artifacts),
                "functional": badges["functional"],
                "reproducible": badges["reproducible"],
                "available": badges["available"],
                "reusable": badges["reusable"],
            }

            venue_type = "workshop" if conf_name.lower() in WORKSHOPS else "conference"

            by_conference[conf_name.upper()]["years"].append(year_data)
            by_conference[conf_name.upper()]["total_artifacts"] += len(artifacts)
            by_conference[conf_name.upper()]["category"] = category
            by_conference[conf_name.upper()]["venue_type"] = venue_type

            # Collect all artifacts with metadata
            for artifact in artifacts:
                all_artifacts.append(
                    _build_artifact_entry(
                        artifact,
                        conf_name,
                        category,
                        int(year),
                        conf_year,
                        sec_results,
                        sys_results,
                    )
                )

    # Sort years for each conference
    for conf in by_conference.values():
        conf["years"] = sorted(conf["years"], key=lambda x: x["year"])

    # Separate conferences by category
    systems_confs = sorted([c for c, d in by_conference.items() if d["category"] == "systems"])
    security_confs = sorted([c for c, d in by_conference.items() if d["category"] == "security"])

    # Generate summary statistics
    summary = {
        "schema_version": SCHEMA_VERSION,
        "total_artifacts": len(all_artifacts),
        "total_conferences": len(conferences_set),
        "systems_artifacts": systems_artifacts_count,
        "security_artifacts": security_artifacts_count,
        "conferences_list": sorted(list(conferences_set)),
        "systems_conferences": systems_confs,
        "security_conferences": security_confs,
        "year_range": f"{min(years_set)}-{max(years_set)}" if years_set else "N/A",
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC"),
    }

    # Format for Jekyll
    artifacts_by_conference = []
    for conf_name in sorted(by_conference.keys()):
        conf_data = by_conference[conf_name]
        artifacts_by_conference.append(
            {
                "name": conf_name,
                "category": conf_data["category"],
                "venue_type": conf_data.get("venue_type", "conference"),
                "total_artifacts": conf_data["total_artifacts"],
                "years": conf_data["years"],
            }
        )

    # Calculate yearly totals (overall and by category)
    yearly_totals = defaultdict(lambda: {"total": 0, "systems": 0, "security": 0})
    for artifact in all_artifacts:
        year = artifact["year"]
        category = artifact["category"]
        yearly_totals[year]["total"] += 1
        if category == "systems":
            yearly_totals[year]["systems"] += 1
        elif category == "security":
            yearly_totals[year]["security"] += 1

    artifacts_by_year = [
        {"year": year, "count": data["total"], "systems": data["systems"], "security": data["security"]}
        for year, data in sorted(yearly_totals.items())
    ]

    # Build coverage table: which conference/year combos were discovered vs parsed
    all_discovered = {}
    for d in sys_all_dirs | sec_all_dirs:
        cname, cyear = extract_conference_name(d)
        if cyear:
            category = "systems" if d in sys_all_dirs else "security"
            all_discovered[d] = {
                "conference": cname,
                "year": cyear,
                "category": category,
                "parsed": d in all_results and len(all_results[d]) > 0,
                "artifact_count": len(all_results.get(d, [])),
            }
    # Add USENIX conferences to coverage
    for d in usenix_conf_years:
        cname, cyear = extract_conference_name(d)
        if cyear:
            category = usenix_categories.get(d, "systems")
            all_discovered[d] = {
                "conference": cname,
                "year": cyear,
                "category": category,
                "parsed": d in all_results and len(all_results[d]) > 0,
                "artifact_count": len(all_results.get(d, [])),
            }
    # Add ACM conferences to coverage
    for d in acm_conf_years:
        cname, cyear = extract_conference_name(d)
        if cyear:
            category = acm_categories.get(d, "security")
            all_discovered[d] = {
                "conference": cname,
                "year": cyear,
                "category": category,
                "parsed": d in all_results and len(all_results[d]) > 0,
                "artifact_count": len(all_results.get(d, [])),
            }

    coverage = sorted(all_discovered.values(), key=lambda x: (x["conference"], x["year"]))

    # Prepare output data
    output_data = {
        "summary": summary,
        "artifacts_by_conference": artifacts_by_conference,
        "artifacts_by_year": artifacts_by_year,
        "all_artifacts": all_artifacts,
        "coverage": coverage,
    }

    # Write output files
    if output_dir:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "assets/data").mkdir(parents=True, exist_ok=True)

        # Write YAML files for Jekyll _data directory
        save_yaml(output_dir / "_data/summary.yml", summary)

        save_yaml(output_dir / "_data/artifacts_by_conference.yml", artifacts_by_conference)

        save_yaml(output_dir / "_data/artifacts_by_year.yml", artifacts_by_year)

        # Write JSON files for download
        save_validated_json(output_dir / "assets/data/artifacts.json", all_artifacts, Artifact)

        save_validated_json(output_dir / "assets/data/summary.json", summary, Summary)

        # Auto-generate per-conference .md pages for Jekyll
        _generate_conference_pages(output_dir, systems_confs, security_confs)

        logger.info(f"Data files written to {output_dir}")

    return output_data


def main():
    parser = argparse.ArgumentParser(description="Generate statistics for research artifacts website")
    parser.add_argument(
        "--conf_regex", type=str, default=".*20[12][0-9]", help="Regular expression for conference names/years"
    )
    parser.add_argument("--output_dir", type=str, default=None, help="Output directory for generated files")

    args = parser.parse_args()

    data = generate_statistics(args.conf_regex, args.output_dir)

    logger.info(
        f"\nStatistics generated: {data['summary']['total_artifacts']} artifacts from {data['summary']['total_conferences']} conferences ({data['summary']['year_range']})"
    )


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()
    main()

logger = logging.getLogger(__name__)
