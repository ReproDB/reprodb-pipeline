"""Committee scraping/loading.

Fetches AE committee data from sysartifacts and secartifacts GitHub repos,
falls back to web scrapers / local YAML for conferences whose committees are
missing or incomplete, and cleans the resulting member lists (placeholder
removal, markdown/HTML stripping, affiliation normalization).
"""

from __future__ import annotations

import logging
import re

from src.scrapers.parse_committee_md import get_committees
from src.scrapers.repo_utils import get_conferences_from_prefix
from src.scrapers.scrape_committee_web import (
    _load_local_committees,
    get_alternative_committees,
)
from src.utils.normalization.affiliation import normalize_affiliation as _normalize_affiliation
from src.utils.normalization.conference import (
    PLACEHOLDER_NAMES,
    clean_member_name,
)
from src.utils.normalization.conference import (
    clean_name as _display_name,
)
from src.utils.normalization.conference import (
    conf_area as _conf_area,
)

logger = logging.getLogger(__name__)

# Minimum committee size to consider data valid from sysartifacts/secartifacts.
# Committees with fewer members are likely placeholder or chair-only entries.
# PETS secartifacts has only 3 (chairs); real committees have 13-172 members.
MIN_COMMITTEE_SIZE = 5


def _is_valid_committee(members: list[dict]) -> bool:
    """Check if committee data looks valid (not placeholder, has enough members)."""
    if not members:
        return False
    real_members = [
        m
        for m in members
        if m.get("name", "").strip().lower() not in PLACEHOLDER_NAMES and len(m.get("name", "").strip()) > 1
    ]
    return len(real_members) >= MIN_COMMITTEE_SIZE


def _clean_committee(members: list[dict]) -> list[dict]:
    """Remove placeholder members and clean up names/affiliations."""
    cleaned: list[dict] = []
    for m in members:
        name = clean_member_name(m.get("name", ""))
        if name is None:
            continue
        affiliation = m.get("affiliation", "").strip()
        affiliation = re.sub(r"<br\s*/?>$", "", affiliation).strip()
        affiliation = affiliation.strip("*_").strip()  # markdown bold/italic markers
        affiliation = _normalize_affiliation(affiliation)
        entry = {
            "name": name,
            "display_name": _display_name(name),
            "affiliation": affiliation,
        }
        if "role" in m:
            entry["role"] = m["role"]
        cleaned.append(entry)
    return cleaned


def scrape_committees(conf_regex: str) -> tuple[dict, dict]:
    """Scrape committees from sysartifacts/secartifacts and alternative sources.

    Parameters
    ----------
    conf_regex : str
        Regex matching conference-year names (e.g. ``.*20[12][0-9]``).

    Returns
    -------
    (all_results, conf_to_area)
        ``all_results`` maps ``conf_year`` to a list of cleaned member dicts.
        ``conf_to_area`` maps ``conf_year`` to ``"systems"`` / ``"security"`` /
        ``"unknown"``, preferring the source repo over the fallback heuristic.
    """
    logger.info("  Scraping systems committee data from sysartifacts...")
    sys_results = get_committees(conf_regex, "sys")
    logger.info(f"    Found {len(sys_results)} systems conference-years")

    logger.info("  Scraping security committee data from secartifacts...")
    sec_results = get_committees(conf_regex, "sec")
    logger.info(f"    Found {len(sec_results)} security conference-years")

    all_results: dict = {}
    all_results.update(sys_results)
    all_results.update(sec_results)

    # Clean all results (remove placeholders, fix markdown links)
    for cy in list(all_results.keys()):
        all_results[cy] = _clean_committee(all_results[cy])

    # Supplement with alternative sources for missing/invalid committees.
    logger.info("  Checking for conferences needing alternative sources...")
    conferences_needed: dict[str, str] = {}

    all_conf_dirs: set[str] = set()
    for prefix in ("sys", "sec"):
        for entry in get_conferences_from_prefix(prefix) or []:
            name = entry.get("name", "")
            if re.search(conf_regex, name):
                all_conf_dirs.add(name)

    # Also include conferences from local_committees.yaml so that conferences
    # not yet merged into sysartifacts/secartifacts are still discovered.
    for cy in _load_local_committees():
        if re.search(conf_regex, cy):
            all_conf_dirs.add(cy)

    for cy in sorted(all_conf_dirs):
        if cy in all_results and _is_valid_committee(all_results[cy]):
            continue
        conferences_needed[cy] = _conf_area(cy)

    if conferences_needed:
        logger.info(f"    Need alternative sources for {len(conferences_needed)} conference-years:")
        for cy in sorted(conferences_needed.keys()):
            existing = len(all_results.get(cy, []))
            logger.info(f"      {cy} (currently {existing} members)")

        alt_results = get_alternative_committees(conferences_needed)
        for cy, members in alt_results.items():
            cleaned = _clean_committee(members)
            if cleaned:
                existing_count = len(all_results.get(cy, []))
                all_results[cy] = cleaned
                logger.info(f"    ✓ {cy}: replaced {existing_count} → {len(cleaned)} members (alternative source)")
    else:
        logger.info("    All conference-years have valid committee data.")

    # Build area map – prefer the source prefix over the fallback list so that
    # newly added conferences are classified correctly.
    conf_to_area: dict[str, str] = {}
    for cy in all_results:
        if cy in sys_results:
            conf_to_area[cy] = "systems"
        elif cy in sec_results:
            conf_to_area[cy] = "security"
        else:
            conf_to_area[cy] = _conf_area(cy)

    return all_results, conf_to_area
