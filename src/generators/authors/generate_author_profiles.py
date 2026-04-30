#!/usr/bin/env python3
"""
Generate author_profiles.json by merging data from:
  - assets/data/authors.json       (per-paper artifact details)
  - assets/data/ae_members.json    (AE committee service details)
  - assets/data/combined_rankings.json (weighted scores & ranks)

Output:
  assets/data/author_profiles.json — one entry per author with full profile data,
  suitable for client-side rendering of individual author profile pages.

Usage:
  python generate_author_profiles.py --data_dir ../reprodb.github.io/src
"""

import argparse
import logging
from pathlib import Path

from src.utils.io.io import load_json, save_json
from src.utils.normalization.affiliation import normalize_affiliation as _normalize_affiliation
from src.utils.normalization.conference import canonicalize_name
from src.utils.normalization.conference import normalize_name as _base_normalize_name

logger = logging.getLogger(__name__)


def generate_profiles(data_dir: str) -> None:
    data_dir = Path(data_dir)
    authors_path = data_dir / "assets/data/authors.json"
    ae_path = data_dir / "assets/data/ae_members.json"
    cr_path = data_dir / "assets/data/combined_rankings.json"
    out_path = data_dir / "assets/data/author_profiles.json"

    # Load data sources
    authors = load_json(authors_path)
    ae_members = load_json(ae_path)
    combined = load_json(cr_path)

    # Scoring weights — must match generate_combined_rankings.py
    W_AVAILABLE = 1
    W_FUNCTIONAL = 1
    W_REPRODUCIBLE = 1
    W_AE_MEMBERSHIP = 3
    W_AE_CHAIR = 2

    # Index by cleaned name for lookups
    def clean(s: str) -> str:
        """Normalize whitespace (tabs, double spaces, etc.) to single space."""
        return " ".join(s.split())

    def _normalize_name(name: str) -> str:
        return _base_normalize_name(name, strip_initials=True)

    ae_by_name = {clean(canonicalize_name(m["name"])): m for m in ae_members}
    # Also index AE members by normalised name for DBLP-suffix matching
    ae_by_norm: dict[str, dict] = {}
    for m in ae_members:
        norm = _normalize_name(m["name"])
        if norm not in ae_by_norm or m.get("total_memberships", 0) > ae_by_norm[norm].get("total_memberships", 0):
            ae_by_norm[norm] = m
    cr_by_name = {clean(c["name"]): c for c in combined}

    profiles: dict[str, dict] = {}

    # Build profiles from artifact authors
    for a in authors:
        name = clean(a["name"])
        cr = cr_by_name.get(name)
        ae = ae_by_name.get(name) or ae_by_norm.get(_normalize_name(name))

        profile: dict = {
            "name": name,
            "affiliation": _normalize_affiliation(
                clean(
                    (cr.get("affiliation", "") if cr else "")
                    or a.get("affiliation")
                    or (ae.get("affiliation", "") if ae else "")
                )
            ),
            "papers": a.get("papers", []),
            "papers_without_artifacts": a.get("papers_without_artifacts", []),
            "conferences": a.get("conferences", []),
            "years": a.get("years", []),
            "artifact_count": a.get("artifact_count", 0),
            "total_papers": a.get("total_papers", 0),
            "artifact_pct": a.get("artifact_pct", 0),
            "artifact_citations": a.get("artifact_citations", 0),
            "badges_available": a.get("badges_available", 0),
            "badges_functional": a.get("badges_functional", 0),
            "badges_reproducible": a.get("badges_reproducible", 0),
            "category": a.get("category", "unknown"),
        }

        if cr:
            # Use combined_rankings data for consistency with ranking tables
            profile["combined_score"] = cr.get("combined_score", 0)
            profile["artifact_score"] = cr.get("artifact_score", 0)
            profile["citation_score"] = cr.get("citation_score", 0)
            profile["ae_score"] = cr.get("ae_score", 0)
            profile["rank"] = cr.get("rank", 0)
            profile["artifact_count"] = cr.get("artifact_count", profile["artifact_count"])
            profile["total_papers"] = cr.get("total_papers", profile["total_papers"])
            profile["artifact_pct"] = cr.get("artifact_pct", profile["artifact_pct"])
            profile["artifact_citations"] = cr.get("artifact_citations", profile["artifact_citations"])
            profile["badges_available"] = cr.get("badges_available", profile["badges_available"])
            profile["badges_functional"] = cr.get("badges_functional", profile["badges_functional"])
            profile["badges_reproducible"] = cr.get("badges_reproducible", profile["badges_reproducible"])
        else:
            # Not in combined_rankings (score < threshold) — compute directly
            ba = profile["badges_available"]
            bf = profile["badges_functional"]
            br = profile["badges_reproducible"]
            ae_mem = 0
            chairs = 0
            if ae:
                ae_mem = ae.get("total_memberships", 0)
                chairs = ae.get("chair_count", 0)
            profile["artifact_score"] = ba * W_AVAILABLE + bf * W_FUNCTIONAL + br * W_REPRODUCIBLE
            profile["ae_score"] = ae_mem * W_AE_MEMBERSHIP + chairs * W_AE_CHAIR
            profile["citation_score"] = 0
            profile["combined_score"] = profile["artifact_score"] + profile["ae_score"]

        if ae:
            profile["ae_memberships"] = ae.get("total_memberships", 0)
            profile["chair_count"] = ae.get("chair_count", 0)
            profile["ae_conferences"] = ae.get("conferences", [])
            profile["ae_years"] = ae.get("years", {})
        elif cr:
            # AE data merged into combined_rankings via normalised name but
            # ae_members lookup missed (e.g. DBLP suffix mismatch) — pull
            # membership/chair counts from the combined ranking entry.
            if cr.get("ae_memberships"):
                profile["ae_memberships"] = cr["ae_memberships"]
                profile["chair_count"] = cr.get("chair_count", 0)

        profiles[name] = profile

    # Add AE-only members not in authors
    for m in ae_members:
        cname = clean(canonicalize_name(m["name"]))
        if cname in profiles:
            continue
        cr = cr_by_name.get(cname)
        profile = {
            "name": cname,
            "affiliation": _normalize_affiliation(
                clean((cr.get("affiliation", "") if cr else "") or m.get("affiliation", ""))
            ),
            "papers": [],
            "conferences": list(
                dict.fromkeys(
                    c[0] if isinstance(c, list) else c["conference"] if isinstance(c, dict) else c
                    for c in m.get("conferences", [])
                )
            ),
            "years": sorted(int(y) for y in m.get("years", {})),
            "artifact_count": 0,
            "total_papers": 0,
            "artifact_pct": 0,
            "artifact_citations": 0,
            "badges_available": 0,
            "badges_functional": 0,
            "badges_reproducible": 0,
            "category": m.get("area", "unknown"),
            "ae_memberships": m.get("total_memberships", 0),
            "chair_count": m.get("chair_count", 0),
            "ae_conferences": m.get("conferences", []),
            "ae_years": m.get("years", {}),
        }
        if cr:
            profile["combined_score"] = cr.get("combined_score", 0)
            profile["artifact_score"] = cr.get("artifact_score", 0)
            profile["citation_score"] = cr.get("citation_score", 0)
            profile["ae_score"] = cr.get("ae_score", 0)
            profile["rank"] = cr.get("rank", 0)
            profile["artifact_citations"] = cr.get("artifact_citations", 0)
            # Use combined_rankings totals (they include cross-area merges)
            if cr.get("ae_memberships", 0) > profile["ae_memberships"]:
                profile["ae_memberships"] = cr["ae_memberships"]
            if cr.get("chair_count", 0) > profile["chair_count"]:
                profile["chair_count"] = cr["chair_count"]
        else:
            ae_mem = m.get("total_memberships", 0)
            chairs = m.get("chair_count", 0)
            profile["artifact_score"] = 0
            profile["ae_score"] = ae_mem * W_AE_MEMBERSHIP + chairs * W_AE_CHAIR
            profile["citation_score"] = 0
            profile["combined_score"] = profile["ae_score"]
        profiles[cname] = profile

    # Sort by combined_score desc, then artifact_count desc, then name asc
    profile_list = sorted(
        profiles.values(), key=lambda x: (-x.get("combined_score", 0), -x.get("artifact_count", 0), x["name"])
    )

    # Inject author_id from the canonical index
    try:
        from src.utils.normalization.author_index import build_name_to_id

        name_to_id = build_name_to_id(data_dir)
        if name_to_id:
            for profile in profile_list:
                aid = name_to_id.get(profile["name"])
                if aid is not None:
                    profile["author_id"] = aid
    except ImportError:
        logger.debug("Optional module not available, skipping enrichment")

    # Write compact JSON
    save_json(out_path, profile_list, compact=True)

    logger.info(f"Wrote {out_path} ({len(profile_list)} profiles, {out_path.stat().st_size / 1024:.0f}KB)")
    logger.info(f"  Authors with papers: {sum(1 for p in profile_list if p['papers'])}")
    logger.info(f"  Authors with AE service: {sum(1 for p in profile_list if p.get('ae_memberships', 0) > 0)}")


def main():
    parser = argparse.ArgumentParser(description="Generate author profile JSON for the website")
    parser.add_argument("--data_dir", type=str, required=True, help="Path to the reprodb.github.io directory")
    args = parser.parse_args()
    generate_profiles(args.data_dir)


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()

    main()
