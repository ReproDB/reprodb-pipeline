#!/usr/bin/env python3
"""
Generate institution rankings by aggregating combined ranking data by affiliation.
Creates JSON files for overall, systems, and security institution rankings.
"""

import argparse
import logging
from collections import defaultdict
from pathlib import Path

from src.models.institutions.institution_rankings import InstitutionRanking
from src.utils.io.io import load_json, save_validated_json
from src.utils.normalization.affiliation import normalize_affiliation as _normalize_affiliation
from src.utils.normalization.country import country_name_to_iso2, iso2_to_country_name

logger = logging.getLogger(__name__)


def _country_to_iso(country_name: str) -> str | None:
    """Convert country name to ISO 3166-1 alpha-2 code."""
    code = country_name_to_iso2(country_name)
    if not code:
        logger.debug(f"Could not resolve country name to ISO code: {country_name}")
    return code


def _build_classifier():
    """Build the university name index and prefix trie for country classification.

    Reuses the same logic as committee_stats/classification.py.
    """
    from pytrie import Trie

    from src.generators.committee_stats.classification import _build_university_index

    name_index = _build_university_index()
    prefix_tree = Trie(**name_index)
    return prefix_tree, name_index


def _classify_country(affiliation: str, prefix_tree, name_index) -> tuple[str | None, str | None]:
    """Classify an affiliation to (country_name, country_code) or (None, None)."""
    from src.generators.committee_stats.classification import classify_member

    # Check manual overrides first (handles corrections and known institutions)
    code = _KNOWN_INSTITUTION_CODES.get(affiliation)
    if code:
        name = iso2_to_country_name(code)
        return name, code

    # Use university database + fuzzy matching
    country, _ = classify_member(affiliation, prefix_tree, name_index)
    if country:
        code = _country_to_iso(country)
        return country, code
    return None, None


# Manual overrides for well-known institutions/labs/companies missing from
# the world universities database. Keyed by *normalized* affiliation string.
_KNOWN_INSTITUTION_CODES: dict[str, str] = {
    "Max Planck Society": "DE",
    "IISc Bangalore": "IN",
    "Manipal Academy of Higher Education": "IN",
    "EURECOM": "FR",
    "SUSTech": "CN",
    "PQShield": "GB",
    "Academia Sinica": "TW",
    "LAAS-CNRS": "FR",
    "Snowflake": "US",
    "INESC TEC and University of Minho": "PT",
    "Palo Alto Networks": "US",
    "Independent Researcher": "US",
    "Zhongguancun Laboratory": "CN",
    "Samsung": "KR",
    "The Tor Project": "US",
    "CEA LIST": "FR",
    "BITS Pilani": "IN",
    "Univ Rennes": "FR",
    "Brave": "US",
    "Fraunhofer SIT": "DE",
    "University of Modena and Reggio Emilia": "IT",
    "UESTC": "CN",
    "NVIDIA": "US",
    "Oracle": "US",
    "Oak Ridge National Laboratory": "US",
    "Boğaziçi University": "TR",
    "Los Alamos National Laboratory": "US",
    "Qatar Computing Research Institute": "QA",
    "Visa Research": "US",
    "Pontifical Catholic University of Minas Gerais": "BR",
    "NEC Laboratories Europe": "DE",
    "Alibaba Group": "CN",
    "Ant Group": "CN",
    "Baidu": "CN",
    "ByteDance": "CN",
    "Tencent": "CN",
    "Huawei": "CN",
    "Google": "US",
    "Microsoft": "US",
    "Microsoft Research": "US",
    "Amazon": "US",
    "Meta": "US",
    "Apple": "US",
    "IBM Research": "US",
    "Intel": "US",
    "VMware": "US",
    "Cisco": "US",
    "Adobe Research": "US",
    "Netflix": "US",
    "Trail of Bits": "US",
    "Feldera": "US",
    "SAP": "DE",
    "CISPA Helmholtz Center for Information Security": "DE",
    "Inria": "FR",
    "CNRS": "FR",
    "KAIST": "KR",
    "KAUST": "SA",
    "Tsinghua University": "CN",
    "A*STAR": "SG",
    "NTT": "JP",
    "Arm": "GB",
    "Thales": "FR",
    "SkyPilot": "US",
    "UiT - The Arctic University of Norway": "NO",
    "TTI Chicago": "US",
    "Crusoe": "US",
    "NWPU": "CN",
    "Cloudflare": "US",
    "National Renewable Energy Lab": "US",
    "Feldera.com": "US",
    "Data61, CSIRO": "AU",
    "U.S. Naval Research Laboratory": "US",
    "Grenoble INP": "FR",
    "Hewlett Packard Labs": "US",
    "Institut for Internet Security": "DE",
    "VERIMAG": "FR",
    "UMD": "US",
    "Lab-STICC / ENSTA Bretagne": "FR",
    "Qualcomm": "US",
    "IIIT Bangalore": "IN",
    "NXP Semiconductors": "NL",
    "Ca' Foscari University of Venice": "IT",
    "Bosch": "DE",
    "Siemens": "DE",
    "Imec Belgium": "BE",
    "DGIST": "KR",
    "Trend Micro": "JP",
    "Juniper Networks": "US",
    "Databricks": "US",
    "armasuisse": "CH",
    "Cornell Tech": "US",
    "CSIRO Marsfield": "AU",
}


def load_combined_ranking(path):
    """Load combined ranking JSON."""
    return load_json(path)


def aggregate_by_institution(combined_data):
    """Aggregate individual rankings by institution affiliation."""
    inst_data = defaultdict(
        lambda: {
            "affiliation": "",
            "combined_score": 0,
            "artifact_score": 0,
            "artifact_citations": 0,
            "citation_score": 0,
            "ae_score": 0,
            "artifact_count": 0,
            "badges_functional": 0,
            "badges_reproducible": 0,
            "ae_memberships": 0,
            "chair_count": 0,
            "total_papers": 0,
            "author_count": 0,
            "conferences": set(),
            "years": defaultdict(int),
        }
    )

    for person in combined_data:
        affiliation = _normalize_affiliation(person.get("affiliation", "").strip())

        # Skip entries with no affiliation or placeholder affiliations
        if not affiliation or affiliation == "Unknown" or affiliation.startswith("_"):
            affiliation = "Unknown"

        inst = inst_data[affiliation]
        inst["affiliation"] = affiliation
        inst["combined_score"] += person.get("combined_score", 0)
        inst["artifact_score"] += person.get("artifact_score", 0)
        inst["artifact_citations"] += person.get("artifact_citations", 0)
        inst["citation_score"] += person.get("citation_score", 0)
        inst["ae_score"] += person.get("ae_score", 0)
        inst["artifact_count"] += person.get("artifact_count", 0)
        inst["badges_functional"] += person.get("badges_functional", 0)
        inst["badges_reproducible"] += person.get("badges_reproducible", 0)
        inst["ae_memberships"] += person.get("ae_memberships", 0)
        inst["chair_count"] += person.get("chair_count", 0)
        inst["total_papers"] += person.get("total_papers", 0)
        inst["author_count"] += 1

        # Aggregate conferences
        if person.get("conferences"):
            inst["conferences"].update(person["conferences"])

        # Aggregate years
        if person.get("years"):
            for year, count in person["years"].items():
                inst["years"][year] += count

    # Convert to list and calculate derived fields
    institutions = []
    for affiliation, data in inst_data.items():
        if data["artifact_count"] > data["total_papers"]:
            raise ValueError(
                f"Invariant violation for institution '{affiliation}': artifact_count ({data['artifact_count']}) > total_papers ({data['total_papers']})"
            )
        if data["badges_reproducible"] > data["artifact_count"]:
            raise ValueError(
                f"Invariant violation for institution '{affiliation}': reproduced_badges ({data['badges_reproducible']}) > artifact_count ({data['artifact_count']})"
            )
        if data["badges_functional"] > data["artifact_count"]:
            raise ValueError(
                f"Invariant violation for institution '{affiliation}': functional_badges ({data['badges_functional']}) > artifact_count ({data['artifact_count']})"
            )

        # Calculate artifact rate
        artifact_pct = 0
        if data["total_papers"] > 0:
            artifact_pct = round((data["artifact_count"] / data["total_papers"]) * 100, 1)

        # Calculate A:E ratio
        ae_ratio = None
        if data["ae_score"] > 0:
            ae_ratio = round(data["artifact_score"] / data["ae_score"], 2)
        elif data["artifact_score"] > 0:
            ae_ratio = None  # Artifact-only, will display as ∞
        else:
            ae_ratio = 0.0  # Neither artifacts nor AE service

        # Classify institution role based on A:E ratio
        if ae_ratio is None:
            # Artifact-only (ae_score == 0, artifact_score > 0) → creator
            role = "Producer"
        elif ae_ratio == 0.0:
            # AE-only or neither (artifact_score == 0) → evaluator
            role = "Consumer"
        elif ae_ratio > 2.0:
            role = "Producer"
        elif ae_ratio < 0.5:
            role = "Consumer"
        else:
            role = "Balanced"

        # Only include institutions with meaningful contributions, excluding incomplete affiliations
        if data["combined_score"] >= 3 and affiliation.strip() not in ("Univ", "University", "Unknown", "_"):
            institutions.append(
                {
                    "affiliation": data["affiliation"],
                    "combined_score": data["combined_score"],
                    "artifact_score": data["artifact_score"],
                    "artifact_citations": data["artifact_citations"],
                    "citation_score": data["citation_score"],
                    "ae_score": data["ae_score"],
                    "ae_ratio": ae_ratio,
                    "role": role,
                    "artifact_count": data["artifact_count"],
                    "badges_functional": data["badges_functional"],
                    "badges_reproducible": data["badges_reproducible"],
                    "ae_memberships": data["ae_memberships"],
                    "chair_count": data["chair_count"],
                    "total_papers": data["total_papers"],
                    "artifact_pct": artifact_pct,
                    "author_count": data["author_count"],
                    "conferences": sorted(list(data["conferences"])),
                    "years": {str(k): v for k, v in data["years"].items()},
                    "top_authors": [],
                }
            )

    # Sort by combined_score descending
    institutions.sort(key=lambda x: x["combined_score"], reverse=True)

    return institutions


def main():
    """Generate institution ranking JSON files."""
    parser = argparse.ArgumentParser(description="Generate institution rankings")
    parser.add_argument("--data_dir", type=str, default=None, help="Path to website root (reprodb.github.io)")
    args = parser.parse_args()

    if args.data_dir:
        website_path = Path(args.data_dir)
    else:
        base_path = Path(__file__).parent
        website_path = base_path.parent.parent.parent.parent / "reprodb.github.io" / "src"
    data_dir = website_path / "assets" / "data"

    # Build classifier once for country resolution
    logger.info("Building institution classifier...")
    prefix_tree, name_index = _build_classifier()

    def _enrich_with_country(institutions: list[dict]) -> None:
        """Add country and country_code fields to each institution dict in place."""
        matched = 0
        for inst in institutions:
            country, code = _classify_country(inst["affiliation"], prefix_tree, name_index)
            inst["country"] = country
            inst["country_code"] = code
            if code:
                matched += 1
        logger.info(f"    Country classification: {matched}/{len(institutions)} matched")

    # Process overall combined ranking
    logger.info("Processing overall combined ranking...")
    combined_path = data_dir / "combined_rankings.json"
    if combined_path.exists():
        combined_data = load_combined_ranking(combined_path)
        institutions = aggregate_by_institution(combined_data)
        _enrich_with_country(institutions)

        output_path = data_dir / "institution_rankings.json"
        save_validated_json(output_path, institutions, InstitutionRanking)
        logger.info(f"  ✓ Generated {output_path} ({len(institutions)} institutions)")
    else:
        logger.info(f"  ✗ {combined_path} not found")

    # Process per-area combined rankings (systems, security) into separate files.
    # Each gets country_code enrichment so the website doesn't need a lookup step.
    for area in ("systems", "security"):
        area_combined_path = data_dir / f"{area}_combined_rankings.json"
        if area_combined_path.exists():
            logger.info(f"Processing {area} institution rankings...")
            area_data = load_combined_ranking(area_combined_path)
            area_institutions = aggregate_by_institution(area_data)
            _enrich_with_country(area_institutions)

            area_output = data_dir / f"{area}_institution_rankings.json"
            save_validated_json(area_output, area_institutions, InstitutionRanking)
            logger.info(f"  ✓ Generated {area_output} ({len(area_institutions)} institutions)")
        else:
            logger.info(f"  ✗ {area_combined_path} not found")

    # Process per-conference combined rankings ({conf}_combined_rankings.json)
    # into {conf}_institution_rankings.json.  The conference pages load both
    # files, so a missing institution file shows "HTTP 404" on the page.
    # Conferences are discovered from the combined files, so new conferences
    # need no code change.
    logger.info("Processing per-conference institution rankings...")
    non_conf_prefixes = {"systems", "security"}
    for conf_path in sorted(data_dir.glob("*_combined_rankings.json")):
        prefix = conf_path.name[: -len("_combined_rankings.json")]
        if prefix in non_conf_prefixes:
            continue
        conf_institutions = aggregate_by_institution(load_combined_ranking(conf_path))
        _enrich_with_country(conf_institutions)
        conf_output = data_dir / f"{prefix}_institution_rankings.json"
        save_validated_json(conf_output, conf_institutions, InstitutionRanking)
        logger.info(f"  ✓ Generated {conf_output} ({len(conf_institutions)} institutions)")


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()

    main()
