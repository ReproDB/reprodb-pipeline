"""AEC Chair statistics computation.

Extracts chair-specific statistics from the AE member data:
- Chair rankings (by chair_count, cross-conference chairing, tenure)
- Member-to-chair pipeline analysis
- Per-conference-year chair team composition
- Chair retention and turnover metrics
- Geographic / institutional diversity of chairs
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict

logger = logging.getLogger(__name__)


def compute_chair_stats(
    all_members: list,
    systems_members: list,
    security_members: list,
    all_results: dict,
    conf_to_area: dict,
) -> dict:
    """Compute comprehensive chair statistics.

    Parameters
    ----------
    all_members : list
        Full member list (output from _compute_member_stats).
    systems_members : list
        Systems-area member list.
    security_members : list
        Security-area member list.
    all_results : dict
        {conf_year: [{name, affiliation, role?}, ...]}
    conf_to_area : dict
        {conf_year: 'systems'|'security'|'unknown'}

    Returns
    -------
    dict with keys:
        - chairs_all: list of chair records (all areas)
        - chairs_systems: list of chair records (systems only)
        - chairs_security: list of chair records (security only)
        - summary: dict of aggregate statistics
        - chair_teams: list of per-conference-year chair teams
        - pipeline: dict of member-to-chair promotion stats
        - retention: dict of retention / repeat-chairing stats
        - cross_conference: list of chairs who chaired different series
    """
    # ── Extract chairs from each member list ─────────────────────────────────
    chairs_all = _extract_chairs(all_members)
    chairs_systems = _extract_chairs(systems_members)
    chairs_security = _extract_chairs(security_members)

    # ── Per-conference-year chair teams ──────────────────────────────────────
    chair_teams = _compute_chair_teams(chairs_all)

    # ── Member-to-chair pipeline ─────────────────────────────────────────────
    pipeline = _compute_pipeline(chairs_all)

    # ── Retention / repeat chairing ──────────────────────────────────────────
    retention = _compute_retention(chairs_all)

    # ── Cross-conference chairs ──────────────────────────────────────────────
    cross_conference = _compute_cross_conference(chairs_all)

    # ── Year-over-year trends ────────────────────────────────────────────────
    year_trends = _compute_year_trends(chairs_all)

    # ── Summary ──────────────────────────────────────────────────────────────
    summary = {
        "total_chairs": len(chairs_all),
        "total_chairs_systems": len(chairs_systems),
        "total_chairs_security": len(chairs_security),
        "repeat_chairs": retention["repeat_count"],
        "repeat_chairs_pct": round(100 * retention["repeat_count"] / max(len(chairs_all), 1), 1),
        "cross_conference_chairs": len(cross_conference),
        "pipeline_promoted": pipeline["promoted_count"],
        "pipeline_promoted_pct": pipeline["promoted_pct"],
        "pipeline_avg_years": pipeline["avg_years_to_chair"],
        "avg_chairs_per_edition": round(sum(t["chair_count"] for t in chair_teams) / max(len(chair_teams), 1), 1),
        "year_trends": year_trends,
    }

    logger.info(
        f"    Chair stats: {len(chairs_all)} chairs "
        f"({len(chairs_systems)} sys, {len(chairs_security)} sec), "
        f"{retention['repeat_count']} repeat, {len(cross_conference)} cross-conference"
    )

    return {
        "chairs_all": chairs_all,
        "chairs_systems": chairs_systems,
        "chairs_security": chairs_security,
        "summary": summary,
        "chair_teams": chair_teams,
        "pipeline": pipeline,
        "retention": retention,
        "cross_conference": cross_conference,
    }


def _extract_chairs(members: list) -> list:
    """Filter and enrich chair records from a member list.

    Returns list sorted by (-chair_count, -total_memberships, name).
    Each record includes additional chair-specific fields.
    """
    chairs = []
    for m in members:
        if m.get("chair_count", 0) < 1:
            continue

        # Compute chair-specific fields
        chair_conferences = [c for c in m.get("conferences", []) if c["role"] == "chair"]
        member_conferences = [c for c in m.get("conferences", []) if c["role"] == "member"]
        chaired_series = sorted(set(c["conference"] for c in chair_conferences))

        chair_years = sorted(set(c["year"] for c in chair_conferences if c.get("year")))
        member_years = sorted(set(c["year"] for c in member_conferences if c.get("year")))

        first_chair_year = min(chair_years) if chair_years else None
        first_member_year = min(member_years) if member_years else None

        # Was this person a member before becoming a chair?
        promoted_from_member = (
            first_member_year is not None and first_chair_year is not None and first_member_year < first_chair_year
        )
        years_to_chair = (first_chair_year - first_member_year) if promoted_from_member else None

        entry = {
            "name": m["name"],
            "display_name": m.get("display_name", m["name"]),
            "affiliation": m.get("affiliation", ""),
            "total_memberships": m["total_memberships"],
            "chair_count": m["chair_count"],
            "member_count": m["total_memberships"] - m["chair_count"],
            "conferences": m.get("conferences", []),
            "area": m.get("area", "unknown"),
            "years": m.get("years", {}),
            "first_year": m.get("first_year"),
            "last_year": m.get("last_year"),
            "first_chair_year": first_chair_year,
            "chaired_series": chaired_series,
            "chaired_conferences": chair_conferences,
            "promoted_from_member": promoted_from_member,
            "years_to_chair": years_to_chair,
        }
        chairs.append(entry)

    chairs.sort(key=lambda x: (-x["chair_count"], -x["total_memberships"], x["name"]))
    return chairs


def _compute_chair_teams(chairs: list) -> list:
    """Build per-conference-year chair team data.

    Returns list of dicts: {conference, year, chair_count, chairs: [names]}
    sorted by (conference, year).
    """
    teams: dict = defaultdict(list)
    for c in chairs:
        for conf_entry in c.get("chaired_conferences", []):
            key = (conf_entry["conference"], conf_entry["year"])
            teams[key].append(c["display_name"])

    result = []
    for (conf, year), names in sorted(teams.items()):
        result.append(
            {
                "conference": conf,
                "year": year,
                "chair_count": len(names),
                "chairs": sorted(names),
            }
        )
    return result


def _compute_pipeline(chairs: list) -> dict:
    """Analyze the member-to-chair promotion pipeline.

    Returns dict with:
    - promoted_count: number of chairs who served as members first
    - promoted_pct: percentage
    - avg_years_to_chair: average gap (years) from first member role to first chair
    - min_years / max_years: extremes
    - promotions: list of {name, first_member_year, first_chair_year, gap}
    """
    promotions = []
    for c in chairs:
        if c.get("promoted_from_member") and c.get("years_to_chair") is not None:
            promotions.append(
                {
                    "name": c["display_name"],
                    "first_member_year": c["first_year"],
                    "first_chair_year": c["first_chair_year"],
                    "gap": c["years_to_chair"],
                }
            )

    gaps = [p["gap"] for p in promotions]
    return {
        "promoted_count": len(promotions),
        "promoted_pct": round(100 * len(promotions) / max(len(chairs), 1), 1),
        "avg_years_to_chair": round(sum(gaps) / max(len(gaps), 1), 1),
        "min_years": min(gaps) if gaps else 0,
        "max_years": max(gaps) if gaps else 0,
        "promotions": sorted(promotions, key=lambda x: x["gap"]),
    }


def _compute_retention(chairs: list) -> dict:
    """Analyze chair retention: how many chaired more than once.

    Returns dict with:
    - repeat_count: chairs who chaired >=2 times
    - distribution: {chair_count: number_of_people}
    - tenure_spans: list of {name, span_years, chair_count}
    """
    repeat = [c for c in chairs if c["chair_count"] > 1]
    distribution = Counter(c["chair_count"] for c in chairs)

    tenure_spans = []
    for c in chairs:
        first = c.get("first_year")
        last = c.get("last_year")
        if first and last:
            tenure_spans.append(
                {
                    "name": c["display_name"],
                    "span_years": last - first + 1,
                    "chair_count": c["chair_count"],
                    "first_year": first,
                    "last_year": last,
                }
            )

    return {
        "repeat_count": len(repeat),
        "distribution": {str(k): v for k, v in sorted(distribution.items())},
        "tenure_spans": sorted(tenure_spans, key=lambda x: -x["span_years"]),
    }


def _compute_cross_conference(chairs: list) -> list:
    """Find chairs who chaired different conference series.

    Returns list of {name, series: [conf1, conf2, ...], chair_count}.
    """
    cross = []
    for c in chairs:
        if len(c.get("chaired_series", [])) > 1:
            cross.append(
                {
                    "name": c["display_name"],
                    "affiliation": c.get("affiliation", ""),
                    "series": c["chaired_series"],
                    "chair_count": c["chair_count"],
                }
            )
    return sorted(cross, key=lambda x: (-x["chair_count"], x["name"]))


def _compute_year_trends(chairs: list) -> list:
    """Compute new chairs entering per year.

    Returns list of {year, new_chairs, total_active_chairs}.
    """
    # Track first chair year for each person
    first_chair_years: dict = defaultdict(int)
    active_by_year: dict = defaultdict(int)

    for c in chairs:
        first_yr = c.get("first_chair_year")
        if first_yr:
            first_chair_years[first_yr] += 1
        # Count active chairs per year
        for conf_entry in c.get("chaired_conferences", []):
            yr = conf_entry.get("year")
            if yr:
                active_by_year[yr] += 1

    all_years = sorted(set(list(first_chair_years.keys()) + list(active_by_year.keys())))
    return [
        {
            "year": y,
            "new_chairs": first_chair_years.get(y, 0),
            "active_chairs": active_by_year.get(y, 0),
        }
        for y in all_years
    ]
