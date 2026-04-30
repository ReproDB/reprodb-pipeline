#!/usr/bin/env python3
"""
Generate combined rankings that merge artifact authorship with AE committee
service.  Reads the per-area author JSON and AE member JSON produced by
earlier pipeline stages and writes combined JSON files for the Jekyll site.

Outputs:
  assets/data/combined_rankings.json
  assets/data/systems_combined_rankings.json
  assets/data/security_combined_rankings.json
  _data/combined_summary.yml

Usage:
  python generate_combined_rankings.py --data_dir ../reprodb.github.io/src
"""

import argparse
import logging
import re
from collections import defaultdict
from pathlib import Path

from src.models.authors.combined_rankings import AuthorRanking
from src.utils.io.io import load_json, save_validated_json, save_yaml
from src.utils.normalization.affiliation import normalize_affiliation as _normalize_affiliation
from src.utils.normalization.conference import canonicalize_name
from src.utils.normalization.conference import normalize_name as _base_normalize_name

# ── Name normalisation ────────────────────────────────────────────────────────


logger = logging.getLogger(__name__)


def _normalize_name(name: str) -> str:
    """Normalise a name for cross-dataset matching (strips initials)."""
    return _base_normalize_name(name, strip_initials=True)


# ── Merge logic ───────────────────────────────────────────────────────────────


def _merge_rankings(authors: list, ae_members: list) -> list:
    """Merge author and AE-member lists into a combined ranking.

    Algorithm:
      1. **Index** AE members by normalised name (dedup by highest memberships).
      2. **Group** DBLP authors by normalised name.
      3. **Disambiguate** — when multiple DBLP authors share the same normalised
         name as an AE member (common for East-Asian names with DBLP suffixes),
         pick the best match via conference overlap:
         - If one candidate has strictly more overlapping conferences → winner.
         - If tied or no overlap → ambiguous; AE member appears standalone.
      4. **Walk authors** — for each DBLP author, attach AE data only if they
         are the designated winner for that normalised name. Merge years (max of
         both), merge conferences (union).
      5. **Walk unlinked AE members** — emit standalone entries for members not
         matched to any DBLP author.
      6. **Score** every entry via ``_build_entry`` (see scoring weights below),
         sort by combined_score descending, and assign dense ranks with ties.

    Returns a list of dicts sorted by combined_score descending.
    """

    # Index AE members by normalised name
    member_by_norm: dict[str, dict] = {}
    for m in ae_members:
        norm = _normalize_name(m["name"])
        # If multiple AE entries map to the same norm (e.g. name aliases),
        # merge their memberships, chairs, conferences, and years.
        if norm in member_by_norm:
            existing = member_by_norm[norm]
            existing["total_memberships"] = existing.get("total_memberships", 0) + m.get("total_memberships", 0)
            existing["chair_count"] = existing.get("chair_count", 0) + m.get("chair_count", 0)
            # Merge conferences (union)
            seen = {
                tuple(sorted(c.items())) if isinstance(c, dict) else tuple(c) if isinstance(c, list) else c
                for c in existing.get("conferences", [])
            }
            for c in m.get("conferences", []):
                key = tuple(sorted(c.items())) if isinstance(c, dict) else tuple(c) if isinstance(c, list) else c
                if key not in seen:
                    existing.setdefault("conferences", []).append(c)
                    seen.add(key)
            # Merge years (sum counts)
            for yr, cnt in m.get("years", {}).items():
                yr_key = int(yr) if not isinstance(yr, int) else yr
                existing.setdefault("years", {})[yr_key] = existing.get("years", {}).get(yr_key, 0) + cnt
            # Use canonical name as display name
            existing["name"] = canonicalize_name(existing["name"])
            logger.info(f"  Merged AE entry '{m['name']}' into '{existing['name']}' (norm: {norm})")
        else:
            merged = dict(m)
            merged["name"] = canonicalize_name(m["name"])
            member_by_norm[norm] = merged

    # ── Disambiguation: when several DBLP authors share the same normalised
    #    name as an AE member, pick the best match via conference overlap. ────
    author_groups: dict[str, list[dict]] = defaultdict(list)
    for a in authors:
        author_groups[_normalize_name(a["name"])].append(a)

    # Maps normalised name → the single DBLP author name that should receive
    # AE data, or None if no safe match could be determined.
    _ae_winner: dict[str, str | None] = {}
    _ambiguous_norms: set[str] = set()

    for norm, group in author_groups.items():
        if norm not in member_by_norm:
            continue  # no AE member to match

        if len(group) == 1:
            _ae_winner[norm] = group[0]["name"]  # unambiguous
            continue

        # Multiple DBLP authors share this normalised name — disambiguate
        raw_ae_confs = member_by_norm[norm].get("conferences", [])
        ae_confs = set(
            c[0] if isinstance(c, list) else c["conference"] if isinstance(c, dict) else c for c in raw_ae_confs
        )
        scored = []
        for a in group:
            overlap = len(set(a.get("conferences", [])) & ae_confs)
            scored.append((overlap, a["name"]))
        scored.sort(key=lambda x: -x[0])

        if scored[0][0] > 0 and (len(scored) < 2 or scored[0][0] > scored[1][0]):
            # Clear winner — unique best conference overlap
            _ae_winner[norm] = scored[0][1]
            logger.info(
                f"  Disambiguated '{norm}': {scored[0][1]} "
                f"(conf overlap {scored[0][0]}) wins over "
                f"{[s[1] for s in scored[1:]]}"
            )
        else:
            # Ambiguous — don't link anyone; AE member appears standalone
            _ae_winner[norm] = None
            _ambiguous_norms.add(norm)
            logger.info(
                f"  AMBIGUOUS '{norm}': {[s[1] for s in scored]} "
                f"(overlaps: {[s[0] for s in scored]}) — AE member unlinked"
            )

    if _ambiguous_norms:
        logger.info(f"  ⚠ {len(_ambiguous_norms)} AE members could not be unambiguously linked to a DBLP author")

    linked_ae_norms: set[str] = set()
    combined: list[dict] = []

    # 1. Walk authors — attach AE data only to the designated winner
    for a in authors:
        norm = _normalize_name(a["name"])

        is_winner = norm in _ae_winner and _ae_winner[norm] == a["name"]
        m = member_by_norm.get(norm) if is_winner else None

        if m is not None:
            linked_ae_norms.add(norm)

        artifacts = a.get("total", a.get("artifact_count", 0)) or 0
        ae_memberships = m["total_memberships"] if m else 0
        chair_count = m["chair_count"] if m else 0

        # Merge year activity – author years can be a dict {year: count}
        # or a list [2020, 2022, …]
        raw_years = a.get("years", {})
        if isinstance(raw_years, list):
            years = {int(y): 1 for y in raw_years}
        else:
            years = {int(k): v for k, v in raw_years.items()}
        if m:
            for yr, cnt in m.get("years", {}).items():
                yr_key = int(yr) if not isinstance(yr, int) else yr
                # Keep max of both (they track different things, but for the
                # combined view we just want to know the person was active).
                years[yr_key] = max(years.get(yr_key, 0), cnt)

        # Merge conferences
        a_confs = set(a.get("conferences", []))
        raw_m_confs = m.get("conferences", []) if m else []
        m_confs = set(
            c[0] if isinstance(c, list) else c["conference"] if isinstance(c, dict) else c for c in raw_m_confs
        )

        entry = _build_entry(
            name=a["name"],
            affiliation=_normalize_affiliation(
                (a.get("affiliation", "") or m.get("affiliation", "")) if m else a.get("affiliation", "")
            ),
            artifact_count=artifacts,
            total_papers=a.get("total_papers", 0) or 0,
            artifact_pct=a.get("artifact_pct", 0) or 0,
            ae_memberships=ae_memberships,
            chair_count=chair_count,
            conferences=sorted(a_confs | m_confs),
            years=years,
            artifact_citations=a.get("artifact_citations", 0) or 0,
            badges_available=a.get("badges_available", 0) or 0,
            badges_functional=a.get("badges_functional", 0) or 0,
            badges_reproducible=a.get("badges_reproducible", 0) or 0,
        )
        combined.append(entry)

    # 2. Walk AE members not already linked to a winning author
    for norm, m in member_by_norm.items():
        if norm in linked_ae_norms:
            continue

        years = {}
        for yr, cnt in m.get("years", {}).items():
            yr_key = int(yr) if not isinstance(yr, int) else yr
            years[yr_key] = cnt

        entry = _build_entry(
            name=m["name"],
            affiliation=_normalize_affiliation(m.get("affiliation", "")),
            artifact_count=0,
            total_papers=0,
            artifact_pct=0,
            ae_memberships=m.get("total_memberships", 0),
            chair_count=m.get("chair_count", 0),
            conferences=sorted(
                set(
                    c[0] if isinstance(c, list) else c["conference"] if isinstance(c, dict) else c
                    for c in m.get("conferences", [])
                )
            ),
            years=years,
            artifact_citations=0,
            badges_available=0,
            badges_functional=0,
            badges_reproducible=0,
        )
        combined.append(entry)

    # Sort by combined_score desc, then artifact_count desc, then name asc
    combined.sort(key=lambda x: (-x["combined_score"], -x["artifact_count"], x["name"]))

    # Assign ranks (with ties on combined_score)
    rank = 1
    for i, c in enumerate(combined):
        if i > 0 and c["combined_score"] < combined[i - 1]["combined_score"]:
            rank = i + 1
        c["rank"] = rank

    return combined


# ── Scoring weights ───────────────────────────────────────────────────────────
# Artifact badges (additive – each level adds 1 pt, max 3 per artifact):
#   Available = 1 pt,  +Functional = +1 pt (total 2),  +Reproducible = +1 pt (total 3)
# AE service:  Each membership = 3,  Each chair role = +2  (on top of membership)
W_AVAILABLE = 1
W_FUNCTIONAL = 1  # additional point for functional badge
W_REPRODUCIBLE = 1  # additional point for reproducible badge
W_AE_MEMBERSHIP = 3
W_AE_CHAIR = 2  # bonus on top of membership
W_CITATION = 0  # DISABLED: OpenAlex citations unreliable for artifact DOIs
# (all 43 reported citations in March 2026 were false positives
# or self-citations; see verify_artifact_citations.py)


def _build_entry(
    *,
    name,
    affiliation,
    artifact_count,
    total_papers,
    artifact_pct,
    ae_memberships,
    chair_count,
    conferences,
    years,
    artifact_citations,
    badges_available,
    badges_functional,
    badges_reproducible,
) -> dict:
    """Build a single combined-ranking entry dict with weighted scoring.

    Scoring (additive – each badge level adds 1 pt, max 3 per artifact):
      artifact_score = available*1 + functional*1 + reproducible*1
      ae_score       = memberships*3  + chairs*2
            combined_score = artifact_score + citation_score + ae_score
    """
    # Compute weighted artifact score (additive: each badge level adds 1 pt)
    artifact_score = (
        badges_available * W_AVAILABLE + badges_functional * W_FUNCTIONAL + badges_reproducible * W_REPRODUCIBLE
    )

    # Citation score (per-citation)
    citation_score = (artifact_citations or 0) * W_CITATION

    # Compute weighted AE score
    ae_score = ae_memberships * W_AE_MEMBERSHIP + chair_count * W_AE_CHAIR

    combined_score = artifact_score + citation_score + ae_score

    # Compute artifact to evaluation ratio
    ae_ratio = None
    if ae_score > 0:
        ae_ratio = round(artifact_score / ae_score, 2)

    yr_keys = [int(y) for y in years] if years else []

    if artifact_count > total_papers:
        logger.info(
            f"  ⚠ DBLP undercount for '{name}': artifact_count ({artifact_count}) > total_papers ({total_papers}), clamping"
        )
        total_papers = artifact_count
    if badges_reproducible > artifact_count:
        raise ValueError(
            f"Invariant violation for '{name}': reproduced_badges ({badges_reproducible}) > artifact_count ({artifact_count})"
        )
    if badges_functional > artifact_count:
        raise ValueError(
            f"Invariant violation for '{name}': functional_badges ({badges_functional}) > artifact_count ({artifact_count})"
        )

    # Calculate reproducibility rate (% of artifacts that are reproducible)
    repro_pct = 0
    if artifact_count > 0:
        repro_pct = int(round((badges_reproducible / artifact_count) * 100))

    # Sanitise raw name for storage/matching stability
    name = re.sub(r"[\t\n\r]+", " ", name)
    name = re.sub(r"  +", " ", name).strip()

    # Canonical display fields used by website tables
    display_name = re.sub(r"\s+\d{4}$", "", name).strip()
    display_affiliation = _normalize_affiliation(affiliation)

    return {
        "name": name,
        "display_name": display_name,
        "affiliation": affiliation,
        "display_affiliation": display_affiliation,
        "artifact_count": artifact_count,
        "artifact_score": artifact_score,
        "artifact_citations": artifact_citations or 0,
        "citation_score": citation_score,
        "total_papers": total_papers,
        "artifact_pct": artifact_pct,
        "repro_pct": repro_pct,
        "ae_memberships": ae_memberships,
        "chair_count": chair_count,
        "ae_score": ae_score,
        "ae_ratio": ae_ratio,
        "combined_score": combined_score,
        "badges_available": badges_available,
        "badges_functional": badges_functional,
        "badges_reproducible": badges_reproducible,
        "conferences": conferences,
        "years": {str(k): v for k, v in years.items()},
        "first_year": min(yr_keys) if yr_keys else None,
        "last_year": max(yr_keys) if yr_keys else None,
    }


# ── Main entry ────────────────────────────────────────────────────────────────


def generate_combined_rankings(data_dir: str) -> None:
    """Read author + AE data, write combined ranking files."""

    assets_data = Path(data_dir) / "assets" / "data"
    yaml_dir = Path(data_dir) / "_data"

    # Load author data
    def _load_json(name):
        path = assets_data / name
        if not path.exists():
            logger.warning(f"  Warning: {name} not found, skipping")
            return []
        return load_json(path)

    all_authors = _load_json("authors.json")
    sys_authors = _load_json("systems_authors.json")
    sec_authors = _load_json("security_authors.json")
    all_ae_members = _load_json("ae_members.json")
    sys_members = _load_json("systems_ae_members.json")
    sec_members = _load_json("security_ae_members.json")

    # Load citation data and merge into authors
    cited_by_author = _load_json("cited_artifacts_by_author.json")

    # Create a mapping of normalized author names to citation counts
    def _normalize_for_citation(name):
        """Match names to citation data (uses same normalization as our name matching)."""
        norm = _normalize_name(name)
        return norm

    citation_by_norm = {}
    if cited_by_author:
        for author_name, author_data in cited_by_author.items():
            # author_data is either the old format (list) or new format (dict with cited_artifacts)
            if isinstance(author_data, dict):
                total_citations = author_data.get("total_citations", 0)
            elif isinstance(author_data, list):
                # Old format: just a list of artifacts
                total_citations = sum(int(a.get("citations", 0)) for a in author_data)
            else:
                total_citations = 0

            norm = _normalize_for_citation(author_name)
            citation_by_norm[norm] = total_citations

    # Merge citation data into author lists
    def _add_citations_to_authors(authors_list):
        """Add artifact_citations field to each author from citation data."""
        for author in authors_list:
            norm = _normalize_for_citation(author.get("name", ""))
            author["artifact_citations"] = citation_by_norm.get(norm, 0)

    _add_citations_to_authors(all_authors)
    _add_citations_to_authors(sys_authors)
    _add_citations_to_authors(sec_authors)

    # Generate combined rankings for systems and security
    combined_sys = _merge_rankings(sys_authors, sys_members)
    combined_sec = _merge_rankings(sec_authors, sec_members)

    # Create combined_all as the union of systems and security to enforce
    # monotonic totals (all >= systems, all >= security)
    logger.info("Merging systems and security rankings into combined all...")
    combined_all_dict = {}

    # Add all people from systems
    for person in combined_sys:
        # Use raw name (preserves DBLP suffix like '0017') so that
        # distinct people who share the same base name are not merged.
        key = person["name"]
        combined_all_dict[key] = person.copy()

    # Merge in people from security
    for person in combined_sec:
        key = person["name"]
        if key in combined_all_dict:
            # Person is in both - merge their data by SUMMING contributions
            # Systems and security track different conferences, so artifacts,
            # papers, and AE memberships should be additive
            existing = combined_all_dict[key]

            # Sum all contribution metrics
            existing["artifact_count"] += person["artifact_count"]
            existing["artifact_score"] += person["artifact_score"]
            existing["artifact_citations"] += person.get("artifact_citations", 0)
            existing["citation_score"] += person.get("citation_score", 0)
            existing["badges_available"] += person.get("badges_available", 0)
            existing["badges_functional"] += person.get("badges_functional", 0)
            existing["badges_reproducible"] += person.get("badges_reproducible", 0)
            existing["ae_memberships"] += person["ae_memberships"]
            existing["chair_count"] += person["chair_count"]
            existing["ae_score"] += person["ae_score"]
            existing["combined_score"] += person["combined_score"]
            existing["total_papers"] += person["total_papers"]

            # Merge conferences and years (union)
            existing_confs = set(existing.get("conferences", []))
            person_confs = set(person.get("conferences", []))
            existing["conferences"] = sorted(existing_confs | person_confs)

            existing_years = existing.get("years", {})
            person_years = person.get("years", {})
            merged_years = existing_years.copy()
            for yr, cnt in person_years.items():
                # For years, sum the activity counts
                merged_years[yr] = merged_years.get(yr, 0) + cnt
            existing["years"] = merged_years

            # Update year range
            all_years = list(merged_years.keys())
            if all_years:
                existing["first_year"] = min(all_years)
                existing["last_year"] = max(all_years)

            if existing["artifact_count"] > existing["total_papers"]:
                logger.info(
                    f"  ⚠ DBLP undercount after merge for '{existing['name']}': "
                    f"artifact_count ({existing['artifact_count']}) > total_papers ({existing['total_papers']}), clamping"
                )
                existing["total_papers"] = existing["artifact_count"]
            if existing["badges_reproducible"] > existing["artifact_count"]:
                raise ValueError(
                    f"Invariant violation after systems+security merge for '{existing['name']}': reproduced_badges ({existing['badges_reproducible']}) > artifact_count ({existing['artifact_count']})"
                )
            if existing["badges_functional"] > existing["artifact_count"]:
                raise ValueError(
                    f"Invariant violation after systems+security merge for '{existing['name']}': functional_badges ({existing['badges_functional']}) > artifact_count ({existing['artifact_count']})"
                )

            # Recalculate rates based on summed totals
            if existing["total_papers"] > 0:
                existing["artifact_pct"] = int(round((existing["artifact_count"] / existing["total_papers"]) * 100))
            if existing["artifact_count"] > 0:
                existing["repro_pct"] = int(round((existing["badges_reproducible"] / existing["artifact_count"]) * 100))
            # Recalculate ae_ratio based on merged scores
            if existing["ae_score"] > 0:
                existing["ae_ratio"] = round(existing["artifact_score"] / existing["ae_score"], 2)
            else:
                existing["ae_ratio"] = None
        else:
            # Person only in security - add them
            combined_all_dict[key] = person.copy()

    # Convert back to list and sort by combined_score descending
    combined_all = sorted(combined_all_dict.values(), key=lambda x: x["combined_score"], reverse=True)

    # Filter: only include people with combined_score >= 3
    # With additive scoring (each badge level=+1, max 3 per artifact,
    # AE membership=3, AE chair=+2), a score of 3 means at least one
    # reproducible artifact, or one AE membership, or meaningful contribution.
    combined_all = [c for c in combined_all if c["combined_score"] >= 3]
    combined_sys = [c for c in combined_sys if c["combined_score"] >= 3]
    combined_sec = [c for c in combined_sec if c["combined_score"] >= 3]

    # Re-rank after filtering
    for lst in (combined_all, combined_sys, combined_sec):
        rank = 1
        for i, c in enumerate(lst):
            if i > 0 and c["combined_score"] < lst[i - 1]["combined_score"]:
                rank = i + 1
            c["rank"] = rank

    # Inject author_id from the canonical index
    try:
        from src.utils.normalization.author_index import build_name_to_id

        name_to_id = build_name_to_id(data_dir)
        if name_to_id:
            for lst in (combined_all, combined_sys, combined_sec):
                for entry in lst:
                    aid = name_to_id.get(entry["name"])
                    if aid is not None:
                        entry["author_id"] = aid
            logger.info("  Author IDs injected from index")
    except ImportError:
        logger.debug("Optional module not available, skipping enrichment")

    # Write JSON
    assets_data.mkdir(parents=True, exist_ok=True)
    for fname, data in [
        ("combined_rankings.json", combined_all),
        ("systems_combined_rankings.json", combined_sys),
        ("security_combined_rankings.json", combined_sec),
    ]:
        path = assets_data / fname
        save_validated_json(path, data, AuthorRanking, indent=None)
        logger.info(f"  Wrote {path} ({len(data)} entries)")

    # ── Per-conference combined rankings ──────────────────────────────────
    # Discover conferences from {conf}_conf_authors.json files in _build/
    import glob

    build_dir = assets_data.parent / "_build"
    conf_author_files = glob.glob(str(build_dir / "*_conf_authors.json"))
    # Fall back to legacy location (assets/data/) for backward compatibility
    if not conf_author_files:
        conf_author_files = glob.glob(str(assets_data / "*_conf_authors.json"))
    for conf_author_path in sorted(conf_author_files):
        conf_lower = Path(conf_author_path).name.replace("_conf_authors.json", "")
        conf_upper = conf_lower.upper()

        # Load per-conference authors (already filtered & scored for this conf)
        conf_authors_data = load_json(conf_author_path)

        # Add citation data
        _add_citations_to_authors(conf_authors_data)

        # Filter AE members to this conference, recompute per-conf AE stats
        conf_ae_members = []
        for m in all_ae_members:
            entries = [
                c
                for c in (m.get("conferences") or [])
                if (isinstance(c, list) and c[0] == conf_upper)
                or (isinstance(c, dict) and c.get("conference") == conf_upper)
            ]
            if not entries:
                continue

            def _conf_role(e):
                return e[2] if isinstance(e, list) else e.get("role", "member")

            def _conf_year(e):
                return e[1] if isinstance(e, list) else e.get("year")

            conf_m = {
                "name": m["name"],
                "display_name": m.get("display_name", m["name"]),
                "affiliation": m.get("affiliation", ""),
                "total_memberships": len(entries),
                "chair_count": sum(1 for e in entries if _conf_role(e) == "chair"),
                "conferences": entries,
                "years": {},
            }
            for e in entries:
                yr = str(_conf_year(e))
                conf_m["years"][yr] = conf_m["years"].get(yr, 0) + 1
            conf_ae_members.append(conf_m)

        # Merge using the same logic as area-level rankings
        conf_combined = _merge_rankings(conf_authors_data, conf_ae_members)
        conf_combined = [c for c in conf_combined if c["combined_score"] >= 3]

        # Re-rank
        rank = 1
        for i, c in enumerate(conf_combined):
            if i > 0 and c["combined_score"] < conf_combined[i - 1]["combined_score"]:
                rank = i + 1
            c["rank"] = rank

        # Inject author_id
        try:
            if name_to_id:
                for entry in conf_combined:
                    aid = name_to_id.get(entry["name"])
                    if aid is not None:
                        entry["author_id"] = aid
        except NameError:
            pass

        fname = f"{conf_lower}_combined_rankings.json"
        path = assets_data / fname
        save_validated_json(path, conf_combined, AuthorRanking, indent=None)
        logger.info(f"  Wrote {path} ({len(conf_combined)} entries)")

    # Summary YAML
    # Count people who have both artifacts AND AE service
    both_all = sum(1 for c in combined_all if c["artifact_count"] > 0 and c["ae_memberships"] > 0)
    both_sys = sum(1 for c in combined_sys if c["artifact_count"] > 0 and c["ae_memberships"] > 0)
    both_sec = sum(1 for c in combined_sec if c["artifact_count"] > 0 and c["ae_memberships"] > 0)

    summary = {
        "combined_total": len(combined_all),
        "combined_systems": len(combined_sys),
        "combined_security": len(combined_sec),
        "both_artifacts_and_ae": both_all,
        "both_artifacts_and_ae_systems": both_sys,
        "both_artifacts_and_ae_security": both_sec,
        "top_combined_score": combined_all[0]["combined_score"] if combined_all else 0,
    }
    yml_path = yaml_dir / "combined_summary.yml"
    save_yaml(yml_path, summary)
    logger.info(f"  Wrote {yml_path}")

    logger.info(
        f"  Combined rankings: {len(combined_all)} total, {len(combined_sys)} systems, {len(combined_sec)} security"
    )
    logger.info(f"  People with both artifacts and AE service: {both_all}")


def main():
    parser = argparse.ArgumentParser(description="Generate combined artifact-author + AE-member rankings")
    parser.add_argument(
        "--data_dir", type=str, default="../reprodb.github.io/src", help="Path to the website repo root"
    )
    args = parser.parse_args()
    generate_combined_rankings(args.data_dir)


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()

    main()
