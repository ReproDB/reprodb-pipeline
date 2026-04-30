#!/usr/bin/env python3
"""
Generate per-area (systems/security) author data files for the Jekyll site.
Reads _data/authors.yml and _data/summary.yml, outputs:
  - _data/systems_authors.yml
  - _data/security_authors.yml

Usage:
  python generate_area_authors.py --data_dir ../reprodb.github.io/src
"""

import argparse
import logging
from collections import defaultdict
from pathlib import Path

from src.utils.io.io import load_json, save_json
from src.utils.io.io import load_yaml as _load_yaml
from src.utils.io.io import save_yaml as _save_yaml
from src.utils.normalization.affiliation import normalize_affiliation as _normalize_affiliation
from src.utils.normalization.conference import canonicalize_name, clean_name

logger = logging.getLogger(__name__)
DATA_DIR = None  # Set via CLI


def load_yaml(filename):
    return _load_yaml(DATA_DIR / filename)


def save_yaml(filename, data):
    _save_yaml(DATA_DIR / filename, data)


def _normalize_name_key(name):
    return clean_name(canonicalize_name(name)).lower()


def _load_ae_affiliation_fallback():
    """Load AE affiliation fallback map by normalized name, if available."""
    ae_path = DATA_DIR.parent / "assets" / "data" / "ae_members.json"
    if not ae_path.exists():
        return {}
    ae_members = load_json(ae_path)

    fallback = {}
    for member in ae_members:
        key = _normalize_name_key(member.get("name", ""))
        aff = _normalize_affiliation(member.get("affiliation", "") or "")
        if key and aff and key not in fallback:
            fallback[key] = aff
    return fallback


def _load_author_index_affiliations():
    """Load canonical affiliations from author_index.json (primary source)."""
    try:
        from src.utils.normalization.author_index import load_author_index

        website_root = DATA_DIR.parent
        _, index_by_name = load_author_index(website_root)
        return {name: entry.get("affiliation", "") for name, entry in index_by_name.items() if entry.get("affiliation")}
    except (ImportError, Exception):
        return {}


def _load_authors():
    """Load authors from authors.json (has inline papers) with YAML fallback."""
    json_path = DATA_DIR.parent / "assets" / "data" / "authors.json"
    if json_path.exists():
        data = load_json(json_path)
        # Verify the JSON actually has papers embedded
        if data and data[0].get("papers"):
            logger.info(f"Loaded {len(data)} authors from authors.json (with inline papers)")
            return data
        logger.warning("authors.json exists but has no inline papers, falling back to authors.yml")
    return load_yaml("authors.yml")


def generate_area_authors():
    summary = load_yaml("summary.yml")
    authors = _load_authors()
    ae_aff_fallback = _load_ae_affiliation_fallback()
    index_affiliations = _load_author_index_affiliations()

    systems_confs = set(summary.get("systems_conferences", []))
    security_confs = set(summary.get("security_conferences", []))

    # Determine year range from artifacts_by_year (global fallback)
    by_year = load_yaml("artifacts_by_year.yml")
    all_years = sorted([entry["year"] for entry in by_year], reverse=True)
    min_year = min(all_years)
    max_year = max(all_years)

    # Compute per-conference AE year ranges from actual artifact data
    artifacts_by_conf = load_yaml("artifacts_by_conference.yml")
    conf_ae_years = {}  # conf_name -> set of years with AE data
    for conf_data in artifacts_by_conf:
        name = conf_data["name"]
        ae_years = set()
        for yr_data in conf_data.get("years", []):
            ae_years.add(yr_data["year"])
        if ae_years:
            conf_ae_years[name] = ae_years

    def process_authors_for_area(authors_list, area_confs, area_name):
        """Extract per-area author stats."""
        # Compute year range from actual AE data for THIS area's conferences only
        area_ae_years = set()
        for conf in area_confs:
            area_ae_years |= conf_ae_years.get(conf, set())
        area_min_year = min(area_ae_years) if area_ae_years else min_year
        area_max_year = max(area_ae_years) if area_ae_years else max_year
        area_last_5_start = area_max_year - 4

        area_authors = []
        for author in authors_list:
            papers = author.get("papers", [])
            # Filter papers to this area's conferences
            area_papers = [p for p in papers if p.get("conference", "") in area_confs]
            if not area_papers:
                continue

            total = len(area_papers)
            # Count per year
            year_counts = defaultdict(int)
            for p in area_papers:
                yr = p.get("year")
                if yr:
                    year_counts[yr] += 1

            last_5 = sum(year_counts.get(y, 0) for y in range(area_last_5_start, area_max_year + 1))

            # Build per-year list (only area's AE year range)
            years_data = {}
            for y in range(area_max_year, area_min_year - 1, -1):
                years_data[y] = year_counts.get(y, 0)

            # Count badges in this area
            badges_available = 0
            badges_functional = 0
            badges_reproducible = 0
            for p in area_papers:
                badge_list = p.get("badges", [])
                if isinstance(badge_list, str):
                    badge_list = [b.strip() for b in badge_list.split(",")]

                has_available = False
                has_functional = False
                has_repro = False
                if not badge_list or len(badge_list) == 0:
                    has_available = True
                else:
                    for b in badge_list:
                        bl = b.lower() if isinstance(b, str) else ""
                        if "reproduc" in bl or "reusable" in bl:
                            has_repro = True
                        elif "functional" in bl:
                            has_functional = True
                        elif "available" in bl:
                            has_available = True

                if has_available:
                    badges_available += 1
                if has_functional:
                    badges_functional += 1
                if has_repro:
                    badges_reproducible += 1

            # Sum citations for artifacts in this area
            artifact_citations = 0
            for p in area_papers:
                artifact_citations += int(p.get("artifact_citations", 0) or 0)

            # --- Compute total papers at area conferences (AE years only) ---
            # Only count papers published in years where AE existed for that conference.
            area_total_papers = 0
            per_conf_year_totals = author.get("total_papers_by_conf_year", {})
            per_conf_totals = author.get("total_papers_by_conf", {})
            for conf in area_confs:
                ae_years = conf_ae_years.get(conf, set())
                conf_year_data = per_conf_year_totals.get(conf, {})
                if conf_year_data and ae_years:
                    # Sum only papers from AE years
                    for yr, cnt in conf_year_data.items():
                        yr_int = int(yr) if not isinstance(yr, int) else yr
                        if yr_int in ae_years:
                            area_total_papers += cnt
                elif ae_years:
                    # Fallback: per-conf total (no year breakdown available)
                    area_total_papers += per_conf_totals.get(conf, 0)
                else:
                    # No AE years known, use full count
                    area_total_papers += per_conf_totals.get(conf, 0)

            if total > area_total_papers:
                logger.info(
                    f"  ⚠ DBLP undercount for '{author['name']}' in {area_name}: "
                    f"artifacts ({total}) > total_papers ({area_total_papers}), clamping"
                )
                area_total_papers = total
            if badges_reproducible > total:
                raise ValueError(
                    f"Invariant violation for '{author['name']}' in {area_name}: reproduced_badges ({badges_reproducible}) > artifacts ({total})"
                )
            if badges_functional > total:
                raise ValueError(
                    f"Invariant violation for '{author['name']}' in {area_name}: functional_badges ({badges_functional}) > artifacts ({total})"
                )

            # Rates
            artifact_pct = round(total / area_total_papers * 100, 1) if area_total_papers > 0 else 0.0
            repro_pct = round(badges_reproducible / total * 100, 1) if total > 0 else 0.0
            functional_pct = round(badges_functional / total * 100, 1) if total > 0 else 0.0

            # Additive artifact score (same as combined rankings):
            #   Each badge level adds 1 pt: Available=1, Functional=1, Reproducible=1 (max 3 per artifact)
            artifact_score = total * 1 + badges_functional * 1 + badges_reproducible * 1

            author_name = author["name"]
            norm_key = _normalize_name_key(author_name)
            # Affiliation priority: author_index (enricher-sourced) > authors.yml (DBLP) > AE fallback
            aff = index_affiliations.get(author_name, "")
            if not aff:
                aff = _normalize_affiliation(author.get("affiliation", "") or "")
            if not aff:
                aff = ae_aff_fallback.get(norm_key, "")

            entry = {
                "name": author_name,
                "display_name": author.get("display_name", author_name),
                "affiliation": aff,
                "artifact_score": artifact_score,
                "artifact_count": total,
                "total": total,
                "total_papers": area_total_papers,
                "artifact_pct": artifact_pct,
                "repro_pct": repro_pct,
                "functional_pct": functional_pct,
                "last_5_years": last_5,
                "artifact_citations": artifact_citations,
                "badges_available": badges_available,
                "badges_functional": badges_functional,
                "badges_reproducible": badges_reproducible,
                "conferences": sorted(set(p.get("conference", "") for p in area_papers)),
                "years": years_data,
            }
            area_authors.append(entry)

        # Sort by artifact_score descending, then by total descending, then by name
        area_authors.sort(key=lambda x: (-x["artifact_score"], -x["total"], x["name"]))

        # Assign ranks (with ties on artifact_score)
        rank = 1
        for i, a in enumerate(area_authors):
            if i > 0 and a["artifact_score"] < area_authors[i - 1]["artifact_score"]:
                rank = i + 1
            a["rank"] = rank

        return area_authors

    systems_authors = process_authors_for_area(authors, systems_confs, "systems")
    security_authors = process_authors_for_area(authors, security_confs, "security")

    # Inject author_id from the canonical index
    name_to_id = {}
    try:
        from src.utils.normalization.author_index import build_name_to_id

        website_root = DATA_DIR.parent
        name_to_id = build_name_to_id(website_root)
        if name_to_id:
            for lst in (systems_authors, security_authors):
                for entry in lst:
                    aid = name_to_id.get(entry["name"])
                    if aid is not None:
                        entry["author_id"] = aid
    except (ImportError, NameError):
        logger.debug("Optional module not available, skipping enrichment")

    # Save YAML for Jekyll (kept for backwards compat, but pages now load JSON)
    save_yaml("systems_authors.yml", systems_authors)
    save_yaml("security_authors.yml", security_authors)

    # Save JSON for dynamic client-side loading (much faster page load)
    assets_data = DATA_DIR.parent / "assets" / "data"
    assets_data.mkdir(parents=True, exist_ok=True)
    save_json(assets_data / "systems_authors.json", systems_authors, indent=None)
    save_json(assets_data / "security_authors.json", security_authors, indent=None)

    # --- Generate per-conference author JSON files (intermediate, not deployed) ---
    build_dir = DATA_DIR.parent / "_build"
    build_dir.mkdir(parents=True, exist_ok=True)
    all_confs = systems_confs | security_confs
    for conf in sorted(all_confs):
        conf_authors = process_authors_for_area(authors, {conf}, conf)
        # Inject author_id
        if name_to_id:
            for entry in conf_authors:
                aid = name_to_id.get(entry["name"])
                if aid is not None:
                    entry["author_id"] = aid
        fname = f"{conf.lower()}_conf_authors.json"
        save_json(build_dir / fname, conf_authors, indent=None)
        logger.info(f"  {conf}: {len(conf_authors)} authors -> _build/{fname}")

    # Update author_summary with correct counts
    author_summary = load_yaml("author_summary.yml")
    author_summary["systems_authors"] = len(systems_authors)
    author_summary["security_authors"] = len(security_authors)

    # Count cross-domain authors
    sys_names = set(a["name"] for a in systems_authors)
    sec_names = set(a["name"] for a in security_authors)
    author_summary["cross_domain_authors"] = len(sys_names & sec_names)
    save_yaml("author_summary.yml", author_summary)

    logger.info(f"Generated {len(systems_authors)} systems authors -> _data/systems_authors.yml")
    logger.info(f"Generated {len(security_authors)} security authors -> _data/security_authors.yml")
    logger.info(f"Cross-domain authors: {len(sys_names & sec_names)}")
    logger.info(f"Global year range from artifacts_by_year: {min_year}-{max_year}")


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()

    parser = argparse.ArgumentParser(description="Generate per-area author data files.")
    parser.add_argument(
        "--data_dir",
        type=str,
        default="../reprodb.github.io/src",
        help="Path to the website repo root (containing _data/)",
    )
    args = parser.parse_args()
    DATA_DIR = Path(args.data_dir) / "_data"
    generate_area_authors()
