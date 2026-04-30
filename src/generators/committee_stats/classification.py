"""Committee classification & aggregation.

Builds the institution name index, classifies each committee member's
affiliation to a country / continent / institution, and computes per-area
aggregates, yearly time-series, recurring-member rankings and institution
timelines.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path

from pytrie import Trie
from thefuzz import fuzz

from src.scrapers.repo_utils import download_file
from src.utils.io.io import load_yaml
from src.utils.normalization.affiliation import normalize_affiliation as _normalize_affiliation
from src.utils.normalization.conference import (
    clean_name as _display_name,
)
from src.utils.normalization.conference import (
    normalize_name as _normalize_name,
)
from src.utils.normalization.conference import (
    parse_conf_year as _extract_conf_year,
)

logger = logging.getLogger(__name__)

# ── Country → Continent mapping ──────────────────────────────────────────────

COUNTRY_TO_CONTINENT: dict[str, str] = {}
_DATA_DIR = Path(__file__).parent.parent.parent / "data"
_CONTINENT_FILE = _DATA_DIR / "country_to_continent.yml"
if _CONTINENT_FILE.exists():
    COUNTRY_TO_CONTINENT = load_yaml(_CONTINENT_FILE, default={})


def _build_university_index() -> dict:
    """Download and build the university name → info index (with manual overrides)."""
    university_info = json.loads(
        download_file(
            "https://github.com/Hipo/university-domains-list/raw/refs/heads/master/world_universities_and_domains.json"
        )
    )
    # Manual overrides for institutions not in the database
    university_info.extend(
        [
            {"name": "télécom sudparis", "country": "France"},
            {"name": "ku leuven", "country": "Belgium"},
            {"name": "imec-distrinet, ku leuven", "country": "Belgium"},
            {"name": "university of crete", "country": "Greece"},
            {"name": "ucla", "country": "United States"},
            {"name": "tu munich", "country": "Germany"},
            {"name": "inesc-id & ist u. lisboa in Portugal", "country": "Portugal"},
            {"name": "ist lisbon & inesc-id", "country": "Portugal"},
            {"name": "mpi-sws", "country": "Germany"},
            {"name": "hkust", "country": "Hong Kong"},
            {"name": "uc irvine", "country": "United States"},
            {"name": "uiuc", "country": "United States"},
            {"name": "school of computer science, university college dublin", "country": "Ireland"},
            {"name": "imdea software institute", "country": "Spain"},
            {"name": "university of chinese academy of sciences", "country": "China"},
            {"name": "zhengqing", "country": "China"},
            {"name": "the university of utah", "country": "United States"},
            {
                "name": "institute of parallel and distributed systems, shanghai jiao tong university",
                "country": "China",
            },
            {
                "name": "computing and imaging institute - the university of utah",
                "country": "United States",
            },
            {"name": "university of crete & ics-forth", "country": "Greece"},
            {"name": "ics-forth", "country": "Greece"},
            {"name": "kaust", "country": "Saudi Arabia"},
            {"name": "lrz", "country": "Germany"},
            {"name": "ensta bretagne", "country": "France"},
            {
                "name": "institute of computing technology chinese academy of sciences",
                "country": "China",
            },
            {"name": "imdea networks institute & uc3m", "country": "Spain"},
            {"name": "hasso plattner institute", "country": "Germany"},
            {"name": "unist", "country": "South Korea"},
            {"name": "niccolò cusano university", "country": "Italy"},
            {"name": "uc irvine & mpi-sp", "country": "United States"},
            {"name": "univ. toulouse iii, irit", "country": "France"},
            {"name": "university of telepegaso,rome,italy", "country": "Italy"},
            {"name": "leibniz supercomputing center", "country": "Germany"},
            {"name": "inesc tec & u. minho", "country": "Portugal"},
            {"name": "barkhausen institut", "country": "Germany"},
            {"name": "the ohio state university", "country": "United States"},
            # Additional overrides for common unmatched institutions
            {"name": "cispa helmholtz center for information security", "country": "Germany"},
            {"name": "cispa", "country": "Germany"},
            {"name": "cuhk", "country": "Hong Kong"},
            {"name": "cuhk-shenzhen", "country": "China"},
            {"name": "kaist", "country": "South Korea"},
            {"name": "epfl", "country": "Switzerland"},
            {"name": "eth zurich", "country": "Switzerland"},
            {"name": "ethz", "country": "Switzerland"},
            {"name": "google", "country": "United States"},
            {"name": "microsoft research", "country": "United States"},
            {"name": "microsoft", "country": "United States"},
            {"name": "meta", "country": "United States"},
            {"name": "amazon", "country": "United States"},
            {"name": "ibm research", "country": "United States"},
            {"name": "intel labs", "country": "United States"},
            {"name": "vmware research", "country": "United States"},
            {"name": "bytedance", "country": "China"},
            {"name": "tencent", "country": "China"},
            {"name": "alibaba", "country": "China"},
            {"name": "huawei", "country": "China"},
            {"name": "inesc-id and instituto superior técnico", "country": "Portugal"},
            {"name": "inesc-id", "country": "Portugal"},
            {"name": "max planck institute for informatics", "country": "Germany"},
            {"name": "max planck institute for software systems", "country": "Germany"},
            {"name": "mpi-sp", "country": "Germany"},
            {"name": "mpi-inf", "country": "Germany"},
            {"name": "institute of software", "country": "China"},
            {"name": "institute of software, chinese academy of sciences", "country": "China"},
            {"name": "snu", "country": "South Korea"},
            {"name": "postech", "country": "South Korea"},
            {"name": "ntu", "country": "Singapore"},
            {"name": "nus", "country": "Singapore"},
            {"name": "sutd", "country": "Singapore"},
            {"name": "tu delft", "country": "Netherlands"},
            {"name": "tu darmstadt", "country": "Germany"},
            {"name": "tu berlin", "country": "Germany"},
            {"name": "tu wien", "country": "Austria"},
            {"name": "rwth aachen", "country": "Germany"},
            {"name": "rwth aachen university", "country": "Germany"},
            {"name": "inria", "country": "France"},
            {"name": "cea", "country": "France"},
            {"name": "cnrs", "country": "France"},
            {"name": "vrije universiteit amsterdam", "country": "Netherlands"},
            {"name": "vu amsterdam", "country": "Netherlands"},
            {"name": "sapienza university of rome", "country": "Italy"},
            {"name": "politecnico di milano", "country": "Italy"},
            {"name": "iisc", "country": "India"},
            {"name": "iit bombay", "country": "India"},
            {"name": "iit delhi", "country": "India"},
            {"name": "iit kanpur", "country": "India"},
            {"name": "iit madras", "country": "India"},
            {"name": "ucl", "country": "United Kingdom"},
            {"name": "imperial college london", "country": "United Kingdom"},
            # Industry & government labs
            {"name": "akamai technologies", "country": "United States"},
            {"name": "sandia national laboratories", "country": "United States"},
            {"name": "lawrence berkeley national laboratory", "country": "United States"},
            {"name": "pnnl", "country": "United States"},
            {"name": "pacific northwest national laboratory", "country": "United States"},
            {"name": "hewlett packard enterprise", "country": "United States"},
            {"name": "hewlett packard enterprise labs", "country": "United States"},
            {"name": "netflix", "country": "United States"},
            {"name": "linkedin", "country": "United States"},
            {"name": "blackberry", "country": "Canada"},
            {"name": "accenture labs", "country": "United States"},
            {"name": "mit csail", "country": "United States"},
            {"name": "baidu security", "country": "China"},
            {"name": "huawei technologies co.", "country": "China"},
            {"name": "orange labs", "country": "France"},
            {"name": "telefonica research", "country": "Spain"},
            {"name": "csiro's data61", "country": "Australia"},
            {"name": "csiro data61", "country": "Australia"},
            # Additional overrides for AE chairs
            {"name": "max planck society", "country": "Germany"},
            {"name": "databricks", "country": "United States"},
            {"name": "university of modena and reggio emilia", "country": "Italy"},
            {"name": "exostellar", "country": "United States"},
            {"name": "grenoble inp", "country": "France"},
            {"name": "qualcomm", "country": "United States"},
            {"name": "isovalent", "country": "Switzerland"},
            {"name": "intel", "country": "United States"},
        ]
    )

    name_index: dict = {}
    for uni in university_info:
        name_index[uni["name"].lower()] = uni
        splitted = uni["name"].split(" ")
        if len(splitted) > 1:
            for part in splitted:
                name_index[part.lower()] = uni
            if len(splitted) > 2:
                for s_cnt in range(1, len(splitted) - 1):
                    name_index[" ".join(splitted[s_cnt:]).lower()] = uni

    return name_index


def _clean_affiliation(aff: str) -> str:
    """Strip HTML tags, markdown formatting, and whitespace from affiliation."""
    import re as _re

    aff = _re.sub(r"<[^>]+>", "", aff)  # remove HTML tags like <br>
    aff = aff.strip("_* \t\n\r")  # remove markdown bold/italic markers
    aff = _re.sub(r"\s+", " ", aff).strip()  # collapse whitespace
    return aff


def classify_member(affiliation, prefix_tree, name_index):
    """Classify a single member's affiliation to a country.

    Returns (country, institution_name) or (None, None) on failure.
    """
    aff_lower = affiliation.lower().strip()
    if not aff_lower:
        return None, None

    # Try prefix-tree match first
    matches = prefix_tree.values(prefix=aff_lower)
    if matches:
        uni = matches[0]
        return uni["country"], uni.get("name", affiliation)

    # Fall back to fuzzy matching
    best_match = None
    best_ratio = 0
    for name, uni in name_index.items():
        ratio = fuzz.ratio(name, aff_lower)
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = uni

    if best_ratio > 80 and best_match:
        return best_match["country"], best_match.get("name", affiliation)

    return None, None


def classify_committees(all_results: dict) -> dict:
    """Classify all committee members by country, continent, and institution.

    Parameters
    ----------
    all_results : dict
        {conf_year: [{name, affiliation}, ...]}

    Returns
    -------
    dict with keys: by_country, by_continent, by_institution, failed
    """
    name_index = _build_university_index()
    prefix_tree = Trie(**name_index)

    by_conf_country: dict = {}
    by_conf_continent: dict = {}
    by_conf_institution: dict = {}
    failed: list = []

    for conf_year, members in all_results.items():
        by_conf_country[conf_year] = defaultdict(int)
        by_conf_continent[conf_year] = defaultdict(int)
        by_conf_institution[conf_year] = defaultdict(int)

        for member in members:
            affiliation = _clean_affiliation(member["affiliation"])
            country, inst_name = classify_member(affiliation, prefix_tree, name_index)
            if country:
                by_conf_country[conf_year][country] += 1
                continent = COUNTRY_TO_CONTINENT.get(country, "Unknown")
                by_conf_continent[conf_year][continent] += 1
                by_conf_institution[conf_year][inst_name or member["affiliation"]] += 1
            else:
                failed.append(
                    {
                        "conference": conf_year,
                        "name": member["name"],
                        "affiliation": affiliation,
                    }
                )

    return {
        "by_country": by_conf_country,
        "by_continent": by_conf_continent,
        "by_institution": by_conf_institution,
        "failed": failed,
    }


def _aggregate_across_conferences(per_conf: dict, conf_to_area: dict):
    """Aggregate per-conference-year dicts into overall + per-area totals.

    Returns
    -------
    (overall, systems, security) — each is {key: total_count}
    """
    overall: dict = defaultdict(int)
    systems: dict = defaultdict(int)
    security: dict = defaultdict(int)
    for conf_year, counts in per_conf.items():
        area = conf_to_area.get(conf_year, "unknown")
        for key, count in counts.items():
            overall[key] += count
            if area == "systems":
                systems[key] += count
            elif area == "security":
                security[key] += count
    return dict(overall), dict(systems), dict(security)


def _build_yearly_series(per_conf: dict, conf_to_area: dict):
    """Build year-level time-series for charting.

    Returns
    -------
    (all_years, systems_years, security_years), each ``{year: {key: count}}``.
    """
    all_years: dict = defaultdict(lambda: defaultdict(int))
    sys_years: dict = defaultdict(lambda: defaultdict(int))
    sec_years: dict = defaultdict(lambda: defaultdict(int))

    for conf_year, counts in per_conf.items():
        _, year = _extract_conf_year(conf_year)
        if year is None:
            continue
        area = conf_to_area.get(conf_year, "unknown")
        for key, count in counts.items():
            all_years[year][key] += count
            if area == "systems":
                sys_years[year][key] += count
            elif area == "security":
                sec_years[year][key] += count

    return dict(all_years), dict(sys_years), dict(sec_years)


def _top_n(d: dict, n: int = 20) -> list:
    """Return top-N items from a dict sorted by value descending."""
    return sorted(d.items(), key=lambda x: x[1], reverse=True)[:n]


def _compute_member_stats(all_results: dict, conf_to_area: dict, classified: dict):
    """Compute statistics for all AE committee members.

    For each unique person (matched by normalized name), track:
    - Total memberships across all conference-years
    - Number of times served as chair
    - Conferences and years served
    - Most recent affiliation
    - Area (systems/security/both)

    Parameters
    ----------
    all_results : dict
        {conf_year: [{name, affiliation, role?}, ...]}
    conf_to_area : dict
        {conf_year: 'systems'|'security'|'unknown'}
    classified : dict
        Classification data (kept for API compatibility, not yet consumed).

    Returns
    -------
    (members_list, systems_members, security_members, summary_dict)
    """
    # Build a per-person country lookup from the classified data.
    # classified["by_country"] is {conf_year: {country: count}} — not per-person.
    # We need to re-classify each member's affiliation to get their country.
    name_index = _build_university_index()
    prefix_tree = Trie(**name_index)

    member_map: dict = {}

    for conf_year, members in all_results.items():
        conf_name, year = _extract_conf_year(conf_year)
        area = conf_to_area.get(conf_year, "unknown")

        for m in members:
            name_raw = m.get("name", "").strip()
            if not name_raw:
                continue
            norm = _normalize_name(name_raw)
            role = m.get("role", "member")
            affiliation = _normalize_affiliation(m.get("affiliation", "").strip("*_ \t"))

            if norm not in member_map:
                member_map[norm] = {
                    "name": name_raw,
                    "display_name": _display_name(name_raw),
                    "affiliation": affiliation,
                    "total_memberships": 0,
                    "chair_count": 0,
                    "conferences": set(),
                    "conference_years": [],
                    "years": set(),
                    "years_count": {},
                    "areas": set(),
                    "roles_by_conf": {},
                    "sys_memberships": 0,
                    "sys_chair_count": 0,
                    "sys_conferences": set(),
                    "sys_conference_years": [],
                    "sys_years": set(),
                    "sys_years_count": {},
                    "sec_memberships": 0,
                    "sec_chair_count": 0,
                    "sec_conferences": set(),
                    "sec_conference_years": [],
                    "sec_years": set(),
                    "sec_years_count": {},
                }

            rec = member_map[norm]
            rec["total_memberships"] += 1
            if role == "chair":
                rec["chair_count"] += 1
            rec["conferences"].add(conf_name)
            rec["conference_years"].append(conf_year)
            if year:
                rec["years"].add(year)
                rec["years_count"][year] = rec["years_count"].get(year, 0) + 1
            if area in ("systems", "security"):
                rec["areas"].add(area)
            rec["roles_by_conf"][conf_year] = role
            # Keep most recent affiliation (higher year = more recent)
            if affiliation and (not rec["affiliation"] or (year and max(rec["years"]) == year)):
                rec["affiliation"] = affiliation
                rec["name"] = name_raw  # prefer most recent spelling
                rec["display_name"] = _display_name(name_raw)

            if area == "systems":
                rec["sys_memberships"] += 1
                if role == "chair":
                    rec["sys_chair_count"] += 1
                rec["sys_conferences"].add(conf_name)
                rec["sys_conference_years"].append(conf_year)
                if year:
                    rec["sys_years"].add(year)
                    rec["sys_years_count"][year] = rec["sys_years_count"].get(year, 0) + 1
            elif area == "security":
                rec["sec_memberships"] += 1
                if role == "chair":
                    rec["sec_chair_count"] += 1
                rec["sec_conferences"].add(conf_name)
                rec["sec_conference_years"].append(conf_year)
                if year:
                    rec["sec_years"].add(year)
                    rec["sec_years_count"][year] = rec["sec_years_count"].get(year, 0) + 1

    # Include all members (≥1 membership) for complete statistics.
    all_members = list(member_map.values())

    for rec in all_members:
        if "systems" in rec["areas"] and "security" in rec["areas"]:
            rec["area"] = "both"
        elif "systems" in rec["areas"]:
            rec["area"] = "systems"
        elif "security" in rec["areas"]:
            rec["area"] = "security"
        else:
            rec["area"] = "unknown"

    # Classify each member's affiliation to country/continent (single place).
    for rec in all_members:
        aff = rec.get("affiliation", "")
        country, _ = classify_member(_clean_affiliation(aff), prefix_tree, name_index) if aff else (None, None)
        rec["country"] = country
        rec["continent"] = COUNTRY_TO_CONTINENT.get(country, None) if country else None

    # Combined (all areas)
    members_list: list = []
    for rec in all_members:
        entry = {
            "name": rec["name"],
            "display_name": rec.get("display_name", _display_name(rec["name"])),
            "affiliation": rec["affiliation"],
            "country": rec["country"],
            "continent": rec["continent"],
            "total_memberships": rec["total_memberships"],
            "chair_count": rec["chair_count"],
            "conferences": sorted(
                [
                    {"conference": c, "year": y, "role": rec["roles_by_conf"].get(cy, "member")}
                    for cy in rec["conference_years"]
                    for c, y in [_extract_conf_year(cy)]
                ],
                key=lambda x: (x["conference"], x["year"] or 0),
            ),
            "area": rec["area"],
            "years": {str(y): rec["years_count"][y] for y in sorted(rec["years"])},
            "first_year": min(rec["years"]) if rec["years"] else None,
            "last_year": max(rec["years"]) if rec["years"] else None,
        }
        members_list.append(entry)

    members_list.sort(key=lambda x: (-x["total_memberships"], -x["chair_count"], x["name"]))

    systems_members: list = []
    for rec in all_members:
        if rec["sys_memberships"] < 1:
            continue
        entry = {
            "name": rec["name"],
            "display_name": rec.get("display_name", _display_name(rec["name"])),
            "affiliation": rec["affiliation"],
            "country": rec["country"],
            "continent": rec["continent"],
            "total_memberships": rec["sys_memberships"],
            "chair_count": rec["sys_chair_count"],
            "conferences": sorted(
                [
                    {"conference": c, "year": y, "role": rec["roles_by_conf"].get(cy, "member")}
                    for cy in rec["sys_conference_years"]
                    for c, y in [_extract_conf_year(cy)]
                ],
                key=lambda x: (x["conference"], x["year"] or 0),
            ),
            "area": rec["area"],
            "years": {str(y): rec["sys_years_count"][y] for y in sorted(rec["sys_years"])},
            "first_year": min(rec["sys_years"]) if rec["sys_years"] else None,
            "last_year": max(rec["sys_years"]) if rec["sys_years"] else None,
        }
        systems_members.append(entry)
    systems_members.sort(key=lambda x: (-x["total_memberships"], -x["chair_count"], x["name"]))

    security_members: list = []
    for rec in all_members:
        if rec["sec_memberships"] < 1:
            continue
        entry = {
            "name": rec["name"],
            "display_name": rec.get("display_name", _display_name(rec["name"])),
            "affiliation": rec["affiliation"],
            "country": rec["country"],
            "continent": rec["continent"],
            "total_memberships": rec["sec_memberships"],
            "chair_count": rec["sec_chair_count"],
            "conferences": sorted(
                [
                    {"conference": c, "year": y, "role": rec["roles_by_conf"].get(cy, "member")}
                    for cy in rec["sec_conference_years"]
                    for c, y in [_extract_conf_year(cy)]
                ],
                key=lambda x: (x["conference"], x["year"] or 0),
            ),
            "area": rec["area"],
            "years": {str(y): rec["sec_years_count"][y] for y in sorted(rec["sec_years"])},
            "first_year": min(rec["sec_years"]) if rec["sec_years"] else None,
            "last_year": max(rec["sec_years"]) if rec["sec_years"] else None,
        }
        security_members.append(entry)
    security_members.sort(key=lambda x: (-x["total_memberships"], -x["chair_count"], x["name"]))

    summary = {
        "total_members": len(members_list),
        "total_members_systems": len(systems_members),
        "total_members_security": len(security_members),
        "total_members_both": sum(1 for m in members_list if m["area"] == "both"),
        "total_chairs": sum(1 for m in members_list if m["chair_count"] > 0),
        "max_memberships": max((m["total_memberships"] for m in members_list), default=0),
    }

    return members_list, systems_members, security_members, summary


def _compute_institution_timeline(classified: dict, conf_to_area: dict) -> dict:
    """Compute institution participation over years.

    Returns dict with keys ``all`` / ``systems`` / ``security`` (per-year
    counts), ``top_by_year`` (top-15 institutions per year for charting), and
    ``unique_by_year`` (count of unique institutions per year, by area).
    """
    inst_years_all: dict = defaultdict(lambda: defaultdict(int))
    inst_years_sys: dict = defaultdict(lambda: defaultdict(int))
    inst_years_sec: dict = defaultdict(lambda: defaultdict(int))

    for conf_year, inst_counts in classified["by_institution"].items():
        _, year = _extract_conf_year(conf_year)
        if year is None:
            continue
        area = conf_to_area.get(conf_year, "unknown")
        for inst, count in inst_counts.items():
            inst_years_all[year][inst] += count
            if area == "systems":
                inst_years_sys[year][inst] += count
            elif area == "security":
                inst_years_sec[year][inst] += count

    top_by_year: list = []
    for year in sorted(inst_years_all.keys()):
        top = sorted(inst_years_all[year].items(), key=lambda x: -x[1])[:15]
        top_by_year.append({"year": year, "institutions": [{"name": k, "count": v} for k, v in top]})

    unique_by_year: list = []
    for year in sorted(inst_years_all.keys()):
        unique_by_year.append(
            {
                "year": year,
                "total": len(inst_years_all[year]),
                "systems": len(inst_years_sys.get(year, {})),
                "security": len(inst_years_sec.get(year, {})),
            }
        )

    return {
        "all": {str(y): dict(c) for y, c in sorted(inst_years_all.items())},
        "systems": {str(y): dict(c) for y, c in sorted(inst_years_sys.items())},
        "security": {str(y): dict(c) for y, c in sorted(inst_years_sec.items())},
        "top_by_year": top_by_year,
        "unique_by_year": unique_by_year,
    }
