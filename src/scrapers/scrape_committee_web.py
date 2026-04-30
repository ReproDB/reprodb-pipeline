#!/usr/bin/env python3
"""
Scrape AE committee data from alternative sources when sysartifacts/secartifacts
GitHub repos don't have the information.

Supported sources:
- USENIX website (FAST, OSDI, ATC, USENIX Security, WOOT)
- CHES website (ches.iacr.org)
- PETS website (petsymposium.org)
- ACSAC website (acsac.org)
"""

import logging
import os
import re
import sys
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

from src.utils.io.cache import CACHE_ROOT, SECONDS_PER_DAY

# ── USENIX conference URL patterns ──────────────────────────────────────────


logger = logging.getLogger(__name__)
# Maps our internal conference name to the USENIX URL slug.
# Key: lowercase conference name as used in sysartifacts (e.g. "fast", "osdi")
# Value: USENIX slug prefix (year suffix is appended as 2-digit)
USENIX_CONF_SLUGS = {
    "fast": "fast",
    "osdi": "osdi",
    "atc": "atc",
    "usenixsec": "usenixsecurity",
    "woot": "woot",
}

# Known years where call-for-artifacts pages exist
USENIX_KNOWN_YEARS = {
    "fast": range(2024, 2027),  # 2024-2026
    "osdi": range(2020, 2027),  # 2020-2026  (some may 404)
    "atc": range(2020, 2027),  # 2020-2026  (some may 404)
    "usenixsec": range(2020, 2027),  # 2020-2026
    # WOOT: 2019 USENIX, 2020 no AE, 2021-2023 IEEE (local_committees.yaml),
    # 2024-2026 back to USENIX
    "woot": [2019, 2021, 2022, 2023, 2024, 2025, 2026],
}

BASE_USENIX = "https://www.usenix.org"


def _get_session():
    """Return a requests session with a polite user-agent."""
    from src.utils.apis.http import create_session

    return create_session()


# ── Cached HTTP fetch for committee scraping ────────────────────────────────
# Uses the shared disk cache (src/.cache/) so that scraped HTML persists
# across CI runs.  When *cache_only* is True (SKIP_USENIX_SCRAPE mode),
# no live HTTP requests are made — only cached responses are returned.
# This means the first full run populates the cache and all subsequent
# CI runs re-use it, eliminating the need for live scraping entirely.

_SCRAPE_CACHE_DIR = str(CACHE_ROOT)
# Committee pages rarely change — 90-day TTL.
_SCRAPE_CACHE_TTL = 90 * SECONDS_PER_DAY


def _cached_fetch(url, session=None, cache_only=False, timeout=30):
    """Fetch *url* with disk-cache backing.

    Returns the response text (str) on success, or ``None`` on failure / 404.

    Parameters
    ----------
    url : str
        URL to fetch.
    session : requests.Session, optional
        Reusable session.  Created on demand if not provided.
    cache_only : bool
        If True, only return data already in the cache — never make a live
        HTTP request.  Used when ``SKIP_USENIX_SCRAPE`` is set.
    timeout : int
        HTTP timeout in seconds.
    """
    from src.utils.io.cache import _MISSING, read_cache, write_cache

    cached = read_cache(_SCRAPE_CACHE_DIR, url, ttl=_SCRAPE_CACHE_TTL, namespace="committee_scrape")
    if cached is not _MISSING:
        return cached  # may be str (success) or None (cached 404)

    if cache_only:
        return None  # not in cache and live fetch disabled

    sess = session or _get_session()
    try:
        resp = sess.get(url, timeout=timeout)
        if resp.status_code == 404:
            # Cache the 404 so we don't retry every run (short TTL: 7 days)
            write_cache(_SCRAPE_CACHE_DIR, url, None, namespace="committee_scrape")
            return None
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning(f"  Failed to fetch {url}: {e}")
        return None

    write_cache(_SCRAPE_CACHE_DIR, url, resp.text, namespace="committee_scrape")
    return resp.text


def _parse_usenix_views_rows(heading):
    """Parse committee from newer USENIX format using views-row divs.

    Format: <div class="views-row ...">
              <div class="views-field views-field-field-speakers-institution">
                <div class="field-content">Name, <em>Affiliation</em></div>
              </div>
            </div>
    """
    members = []
    for sib in heading.next_siblings:
        if not hasattr(sib, "name"):
            continue
        # Stop at next heading
        if sib.name in ("h2", "h3", "h4"):
            break
        if sib.name == "div" and "views-row" in " ".join(sib.get("class", [])):
            # Find field-content div
            fc = sib.find("div", class_="field-content")
            if fc:
                # Parse "Name, <em>Affiliation</em>"
                em = fc.find("em")
                affiliation = em.get_text().strip() if em else ""
                # Name is the text before the <em>
                name_parts = []
                for child in fc.children:
                    if hasattr(child, "name") and child.name == "em":
                        break
                    text = str(child) if not hasattr(child, "name") else child.get_text()
                    name_parts.append(text)
                name = "".join(name_parts).strip().rstrip(",").strip()
                name = re.sub(r"\s+", " ", name).strip("*_").strip()
                affiliation = re.sub(r"\s+", " ", affiliation).strip("*_").strip()
                if name and len(name) > 1:
                    members.append({"name": name, "affiliation": affiliation, "role": "member"})
    return members


def _parse_usenix_committee_html(soup):
    """Parse committee members from a USENIX call-for-artifacts page.

    The USENIX format uses:
      <h2|h3>Artifact Evaluation Committee</h2|h3>
      <p style="line-height: 24px;">
        Name, <em>Affiliation</em><br/>
        Name, <em>Affiliation</em><br/>
        ...
      </p>

    Returns list of {name, affiliation} dicts.
    """
    members = []

    # Find headings containing "Artifact Evaluation Committee"
    # We want the one that is exactly "Artifact Evaluation Committee"
    # (not "Co-Chairs", not "Membership")
    target_heading = None
    for h in soup.find_all(["h2", "h3", "h4"]):
        txt = h.get_text().strip().lower()
        if txt == "artifact evaluation committee" or txt == "artifact evaluation committee (aec)":
            target_heading = h

    if target_heading is None:
        return members

    # Try two formats:
    # Format A (older): <p> with <br/> separated entries
    # Format B (newer): <div class="views-row"> divs with nested field-content

    next_sib = target_heading.find_next_sibling()

    # Check if Format B (views-row divs)
    if (
        next_sib
        and hasattr(next_sib, "name")
        and next_sib.name == "div"
        and "views-row" in " ".join(next_sib.get("class", []))
    ):
        return _parse_usenix_views_rows(target_heading)

    # Format A: find the next <p> sibling
    p_tag = target_heading.find_next_sibling("p")
    if p_tag is None:
        for sib in target_heading.next_siblings:
            if hasattr(sib, "name") and sib.name == "p":
                p_tag = sib
                break
            if hasattr(sib, "name") and sib.name in ("h2", "h3", "h4"):
                break

    if p_tag is None:
        return members

    # Split the <p> inner HTML on <br> tags using string splitting.
    # We avoid DOM-based iteration because html.parser sometimes nests
    # content inside non-self-closing <br> tags (e.g. OSDI/ATC 2023).
    inner_html = p_tag.decode_contents()
    raw_segments = re.split(r"<br\s*/?>", inner_html)

    for segment in raw_segments:
        segment = segment.strip()
        if not segment:
            continue
        # Skip non-member lines
        if segment.startswith(("http", "[", "<a")) or "@" in segment:
            continue

        # Parse name and affiliation from "Name, <em>Affiliation</em>"
        seg_soup = BeautifulSoup(segment, "html.parser")
        em = seg_soup.find("em")
        if em:
            affiliation = em.get_text().strip()
            # Name is the text before the <em> tag
            name_parts = []
            for child in seg_soup.children:
                if hasattr(child, "name") and child.name == "em":
                    break
                text = str(child) if not hasattr(child, "name") else child.get_text()
                name_parts.append(text)
            name = "".join(name_parts).strip().rstrip(",").strip()
        else:
            # Plain text: "Name, Affiliation"
            text = seg_soup.get_text().strip()
            if "," in text:
                parts = text.split(",", 1)
                name = parts[0].strip()
                affiliation = parts[1].strip()
            else:
                name = text
                affiliation = ""

        # Clean up
        name = re.sub(r"\s+", " ", name).strip().strip("*_").strip()
        affiliation = re.sub(r"\s+", " ", affiliation).strip().strip("*_").strip()

        if name and len(name) > 1:
            members.append({"name": name, "affiliation": affiliation, "role": "member"})

    return members


def _parse_usenix_cochairs_html(soup):
    """Parse co-chairs from a USENIX call-for-artifacts page.

    Returns list of {name, affiliation} dicts.
    """
    members = []

    for h in soup.find_all(["h2", "h3", "h4"]):
        txt = h.get_text().strip().lower()
        if "co-chair" in txt and "artifact" in txt:
            # Check for views-row format first
            next_sib = h.find_next_sibling()
            if (
                next_sib
                and hasattr(next_sib, "name")
                and next_sib.name == "div"
                and "views-row" in " ".join(next_sib.get("class", []))
            ):
                members.extend(_parse_usenix_views_rows(h))
                break
            p_tag = h.find_next_sibling("p")
            if p_tag:
                # Parse same format
                lines = []
                current_parts = []
                for child in p_tag.children:
                    if hasattr(child, "name") and child.name == "br":
                        if current_parts:
                            lines.append("".join(current_parts).strip())
                            current_parts = []
                    elif hasattr(child, "name") and child.name == "em":
                        current_parts.append(child.get_text())
                    else:
                        text = str(child) if not hasattr(child, "name") else child.get_text()
                        current_parts.append(text)
                if current_parts:
                    last = "".join(current_parts).strip()
                    if last:
                        lines.append(last)
                for line in lines:
                    line = line.strip()
                    if not line or "@" in line:
                        continue
                    if "," in line:
                        parts = line.split(",", 1)
                        name = parts[0].strip()
                        affiliation = parts[1].strip()
                    else:
                        name = line
                        affiliation = ""
                    name = re.sub(r"\s+", " ", name).strip().strip("*_").strip()
                    affiliation = re.sub(r"\s+", " ", affiliation).strip().strip("*_").strip()
                    if name and len(name) > 1:
                        members.append({"name": name, "affiliation": affiliation, "role": "chair"})
            break  # only process first co-chair heading

    return members


def scrape_usenix_committee(conference, year, session=None, cache_only=False):
    """Scrape AE committee from a USENIX conference call-for-artifacts page.

    Parameters
    ----------
    conference : str
        Conference name (e.g. 'fast', 'osdi', 'usenixsec', 'woot')
    year : int
        4-digit year
    session : requests.Session, optional
    cache_only : bool
        If True, only return data from the disk cache.

    Returns
    -------
    list of {name, affiliation} dicts, or None if page not found
    """
    slug = USENIX_CONF_SLUGS.get(conference.lower())
    if slug is None:
        return None

    yy = str(year)[2:]  # e.g. 2024 -> "24"
    url = f"{BASE_USENIX}/conference/{slug}{yy}/call-for-artifacts"

    html = _cached_fetch(url, session=session, cache_only=cache_only)
    if html is None:
        return None

    soup = BeautifulSoup(html, "html.parser")

    # Parse co-chairs and regular committee members
    chairs = _parse_usenix_cochairs_html(soup)
    members = _parse_usenix_committee_html(soup)

    # Mark roles
    for m in chairs:
        m["role"] = "chair"
    for m in members:
        m["role"] = "member"

    # Combine (chairs + members, dedup by name)
    all_members = chairs + members
    seen = set()
    deduped = []
    for m in all_members:
        if m["name"].lower() not in seen:
            seen.add(m["name"].lower())
            deduped.append(m)

    if deduped:
        logger.info(f"  USENIX: Found {len(deduped)} members for {conference}{year}")
    return deduped if deduped else None


# ── CHES scraper ─────────────────────────────────────────────────────────────

# CHES publishes committee data as JSON (loaded dynamically on the HTML page)
# for 2023+. Chair info is only available in the static HTML page.
# CHES 2021 uses a different JSON path (comm2.json instead of artifact.json).
# CHES 2022 has no JSON API; members must be scraped from HTML.
CHES_KNOWN_YEARS = range(2021, 2027)


def _scrape_ches_chairs_html(soup):
    """Parse chair(s) from a CHES artifacts HTML page.

    Looks for an <h3> heading containing "Artifact Review Chair" or
    "Artifact Evaluation Co-Chairs", then extracts <h4>/<p> pairs from the
    following ``<div class="row">`` element.

    Returns list of {name, affiliation, role:'chair'} dicts.
    """
    chairs = []
    for h3 in soup.find_all("h3"):
        txt = h3.get_text().strip().lower()
        if "chair" in txt and "artifact" in txt:
            row_div = h3.find_next_sibling("div", class_="row")
            if row_div:
                for aside in row_div.find_all("aside"):
                    h4 = aside.find("h4")
                    p = aside.find("p")
                    if h4:
                        name = re.sub(r"\s+", " ", h4.get_text()).strip()
                        affiliation = re.sub(r"\s+", " ", p.get_text()).strip() if p else ""
                        if name and len(name) > 1:
                            chairs.append(
                                {
                                    "name": name,
                                    "affiliation": affiliation,
                                    "role": "chair",
                                }
                            )
            break  # only process the first matching heading
    return chairs


def _scrape_ches_members_html(soup):
    """Parse committee members from the static HTML list (CHES 2022 format).

    Format::

        <h3>Artifact Review Committee Members</h3>
        <ul>
          <li>Name (Affiliation, Country)</li>
          ...
        </ul>

    Returns list of {name, affiliation, role:'member'} dicts.
    """
    members = []
    for h3 in soup.find_all("h3"):
        txt = h3.get_text().strip().lower()
        if "committee member" in txt and "artifact" in txt:
            ul = h3.find_next_sibling("ul")
            if ul:
                for li in ul.find_all("li"):
                    text = li.get_text().strip()
                    # Parse "Name (Affiliation, Country)"
                    m = re.match(r"^(.+?)\s*\((.+)\)\s*$", text)
                    if m:
                        name = re.sub(r"\s+", " ", m.group(1)).strip()
                        affiliation = re.sub(r"\s+", " ", m.group(2)).strip()
                    else:
                        name = re.sub(r"\s+", " ", text).strip()
                        affiliation = ""
                    if name and len(name) > 1:
                        members.append(
                            {
                                "name": name,
                                "affiliation": affiliation,
                                "role": "member",
                            }
                        )
            break
    return members


def scrape_ches_committee(year, session=None, cache_only=False):
    """Scrape AE committee from the CHES website.

    Members are fetched from the JSON API
    (``ches.iacr.org/{year}/json/artifact.json``).  If the JSON endpoint is
    unavailable (e.g. CHES 2022), members are parsed from the static HTML.
    Chairs are parsed from the JSON ``artifact_chairs`` field when available
    (2025+), or from ``(Chair)`` annotations in JSON member names (2024), or
    from the HTML page (``ches.iacr.org/{year}/artifacts.php``).

    Returns list of {name, affiliation, role} dicts, or None if not found.
    """
    members = []
    json_chairs = []

    # 1. Try JSON API for members (and chairs when available)
    json_url = f"https://ches.iacr.org/{year}/json/artifact.json"
    json_text = _cached_fetch(json_url, session=session, cache_only=cache_only)
    # Fallback: CHES 2021 uses comm2.json instead of artifact.json
    if json_text is None:
        json_url = f"https://ches.iacr.org/{year}/json/comm2.json"
        json_text = _cached_fetch(json_url, session=session, cache_only=cache_only)
    if json_text is not None:
        try:
            import json as _json

            data = _json.loads(json_text)
            for entry in data.get("committee", []):
                name = re.sub(r"\s+", " ", entry.get("name", "")).strip()
                affiliation = re.sub(r"\s+", " ", entry.get("affiliation", "")).strip()
                # Detect "(Chair)" / "(Co-Chair)" embedded in the name (e.g. CHES 2024)
                chair_match = re.search(r"\s*\((?:Co-)?Chair\)\s*$", name, re.IGNORECASE)
                if chair_match:
                    name = name[: chair_match.start()].strip()
                    role = "chair"
                else:
                    role = "member"
                if name and len(name) > 1:
                    members.append({"name": name, "affiliation": affiliation, "role": role})
            # Parse artifact_chairs field (2025+)
            for entry in data.get("artifact_chairs", []):
                name = re.sub(r"\s+", " ", entry.get("name", "")).strip()
                affiliation = re.sub(r"\s+", " ", entry.get("affiliation", "")).strip()
                if name and len(name) > 1:
                    json_chairs.append({"name": name, "affiliation": affiliation, "role": "chair"})
        except (ValueError, KeyError):
            logger.warning("Failed to parse CHES committee JSON, skipping JSON source")

    # 2. Fetch HTML page for chairs (and fallback members if JSON failed)
    html_url = f"https://ches.iacr.org/{year}/artifacts.php"
    html_text = _cached_fetch(html_url, session=session, cache_only=cache_only)
    if html_text is not None:
        soup = BeautifulSoup(html_text, "html.parser")

        # Parse chairs from HTML (fallback when JSON has no chair info)
        html_chairs = _scrape_ches_chairs_html(soup)

        # If JSON didn't return members, try HTML fallback (CHES 2022)
        if not members:
            members = _scrape_ches_members_html(soup)

        # Combine: JSON chairs > HTML chairs > members (dedup by name)
        all_members = json_chairs + html_chairs + members
        seen = set()
        deduped = []
        for m in all_members:
            key = m["name"].lower()
            if key not in seen:
                seen.add(key)
                deduped.append(m)

        if deduped:
            chair_count = sum(1 for m in deduped if m["role"] == "chair")
            member_count = len(deduped) - chair_count
            logger.info(f"  CHES: Found {member_count} members + {chair_count} chair(s) for ches{year}")
        return deduped if deduped else None

    # If only JSON data was found (HTML failed), return those
    combined = json_chairs + members
    if combined:
        logger.info(f"  CHES: Found {len(combined)} entries for ches{year} (JSON only)")
        return combined

    return None


# ── PETS scraper ─────────────────────────────────────────────────────────────

# PETS publishes ARC on cfp pages for each year.
# Available for 2020-2026.
PETS_KNOWN_YEARS = range(2020, 2027)


def scrape_pets_committee(year, session=None, cache_only=False):
    """Scrape artifact review committee from PETS/PoPETs website.

    PETS publishes ARC on: petsymposium.org/cfp{YY}.php
    Format: <dt><font><b>Artifact Review Committee:</b></font></dt>
            <dd>Name, <i>Affiliation</i></dd>
            <dd>Name, <i>Affiliation</i></dd>
            ...

    Returns list of {name, affiliation} dicts, or None if not found.
    """
    yy = str(year)[2:]
    url = f"https://petsymposium.org/cfp{yy}.php"

    html = _cached_fetch(url, session=session, cache_only=cache_only)
    if html is None:
        return None

    soup = BeautifulSoup(html, "html.parser")
    members = []

    # Find the <dt> element containing "Artifact Review Committee"
    arc_dt = None
    for dt in soup.find_all("dt"):
        txt = dt.get_text().lower()
        if "artifact" in txt and "committee" in txt:
            arc_dt = dt
            break

    if arc_dt is None:
        return None

    # Collect all <dd> siblings following the <dt> until the next <dt>
    for sib in arc_dt.next_siblings:
        if not hasattr(sib, "name"):
            continue
        if sib.name == "dt":
            break  # reached the next definition term
        if sib.name == "dd":
            text = sib.get_text().strip()
            if not text or len(text) < 3:
                continue
            # Parse "Name, Affiliation"
            if "," in text:
                parts = text.split(",", 1)
                name = parts[0].strip()
                affiliation = parts[1].strip()
            else:
                name = text
                affiliation = ""
            name = re.sub(r"\s+", " ", name).strip().strip("*_").strip()
            affiliation = re.sub(r"\s+", " ", affiliation).strip().strip("*_").strip()
            if name and len(name) > 2:
                members.append({"name": name, "affiliation": affiliation, "role": "member"})

    if members:
        logger.info(f"  PETS: Found {len(members)} members for pets{year}")
    return members if members else None


# ── ACSAC scraper ────────────────────────────────────────────────────────────

# ACSAC publishes artifact committee data at:
#   https://www.acsac.org/<YYYY>/committees/artifact/   (2019-2022)
#   https://www.acsac.org/<YYYY>/committees/artifacts/   (2023+)
# The URL slug changed from singular to plural in 2023.
# Page structure: <h1>Artifact(s) (Evaluation) Committee</h1>
#   followed by chair text, then <h3>Students|Reviewers</h3> and <h3>Mentors</h3>
#   with members as plain text lines "Name, Affiliation" separated by newlines.
# 2019 uses a flat list (no section headings) after the chair line.
ACSAC_KNOWN_YEARS = range(2019, 2026)


def _find_acsac_heading(soup):
    """Find the <h1> heading for the ACSAC artifact committee page."""
    for h in soup.find_all("h1"):
        txt = h.get_text().strip().lower()
        if "artifact" in txt and "committee" in txt:
            return h
    return None


def _parse_acsac_chairs(soup):
    """Parse co-chairs from the ACSAC artifact committee page.

    Chairs appear as plain text right after the <h1> heading, in the format:
        "Artifact Evaluation Co-Chair: Name, Affiliation"
    or  "Artifact Evaluation Chair: Name, Affiliation"

    Returns list of {name, affiliation, role:'chair'} dicts.
    """
    chairs = []
    h1 = _find_acsac_heading(soup)
    if h1 is None:
        return chairs

    # The chair info is typically in the text between <h1> and the first <h3>.
    # It may be in the parent container as NavigableStrings or in <p> tags.
    # Collect all text until we hit the first <h3>.
    text_parts = []
    for sib in h1.next_siblings:
        if hasattr(sib, "name") and sib.name in ("h2", "h3", "h4"):
            break
        if hasattr(sib, "name") and sib.name == "p":
            text_parts.append(sib.get_text())
        elif isinstance(sib, str):
            text_parts.append(sib)

    raw_text = "\n".join(text_parts)

    # Extract chair lines: "Artifact Evaluation (Co-)Chair: Name, Affiliation"
    for match in re.finditer(
        r"(?:Artifact[s]? Evaluation )?(?:Co-)?Chair:\s*(.+?)(?:\n|$)",
        raw_text,
        re.IGNORECASE,
    ):
        entry = match.group(1).strip()
        if "," in entry:
            parts = entry.split(",", 1)
            name = parts[0].strip()
            affiliation = parts[1].strip()
        else:
            name = entry
            affiliation = ""
        name = re.sub(r"\s+", " ", name).strip()
        affiliation = re.sub(r"\s+", " ", affiliation).strip()
        if name and len(name) > 1:
            chairs.append({"name": name, "affiliation": affiliation, "role": "chair"})

    return chairs


def _parse_acsac_section_members(soup, section_keywords):
    """Parse members from an ACSAC committee section (Students, Mentors, Reviewers).

    Finds <h3> headings matching *section_keywords*, then collects the text
    between that heading and the next heading.  Members are listed as plain text
    lines "Name, Affiliation" separated by whitespace/newlines.

    Returns list of {name, affiliation, role:'member'} dicts.
    """
    members = []
    for h3 in soup.find_all("h3"):
        txt = h3.get_text().strip().lower()
        if not any(kw in txt for kw in section_keywords):
            continue

        # Collect all text until next heading
        text_parts = []
        for sib in h3.next_siblings:
            if hasattr(sib, "name") and sib.name in ("h1", "h2", "h3", "h4"):
                break
            if hasattr(sib, "name"):
                text_parts.append(sib.get_text())
            elif isinstance(sib, str):
                text_parts.append(sib)

        raw_text = "\n".join(text_parts)

        # Split into individual entries.  The ACSAC pages concatenate names
        # without clear delimiters; they appear as "Name, AffiliationName, Affiliation...".
        # We split on patterns where an affiliation is followed by a name (uppercase letter
        # after a lowercase/space sequence), or on explicit newlines.
        # First, try splitting by newlines.
        lines = [ln.strip() for ln in raw_text.split("\n") if ln.strip()]

        for line in lines:
            # Each line may still have multiple "Name, Affiliation" entries
            # concatenated without a separator.  The ACSAC HTML renders them
            # with <br/> or spacing, but get_text() may merge them.
            # Use a heuristic: split where we see AffiliationName pattern
            # i.e. a lowercase/closing-paren followed by an uppercase letter.
            entries = re.split(r"(?<=[a-z)\]])(?=[A-Z])", line)
            for entry in entries:
                entry = entry.strip()
                if not entry or len(entry) < 3:
                    continue
                # Skip footer/boilerplate text
                if any(
                    skip in entry.lower()
                    for skip in [
                        "return to top",
                        "event by",
                        "sponsor",
                        "additional link",
                        "code of conduct",
                        "privacy policy",
                        "contact us",
                        "mailing list",
                        "artifact committee",
                        "applied computer",
                    ]
                ):
                    continue
                if "," in entry:
                    parts = entry.split(",", 1)
                    name = parts[0].strip()
                    affiliation = parts[1].strip()
                else:
                    name = entry
                    affiliation = ""
                name = re.sub(r"\s+", " ", name).strip()
                affiliation = re.sub(r"\s+", " ", affiliation).strip()
                if name and len(name) > 1 and not name.startswith("http"):
                    members.append({"name": name, "affiliation": affiliation, "role": "member"})

    return members


def _parse_acsac_flat_members(soup, chairs):
    """Parse members from a flat ACSAC committee page (no section headings).

    Used for pages like 2019 where all members are listed directly after the
    chair line with no ``<h3>`` section headings.  Collects text between the
    ``<h1>`` heading and the footer, excluding the already-parsed chair names.

    Returns list of {name, affiliation, role:'member'} dicts.
    """
    h1 = _find_acsac_heading(soup)
    if h1 is None:
        return []

    chair_names = {c["name"].lower() for c in chairs}

    # Collect all text between <h1> and footer-like headings
    text_parts = []
    for sib in h1.next_siblings:
        if hasattr(sib, "name") and sib.name in ("h2", "h3", "h4"):
            # Stop at footer headings (sponsors, etc.)
            heading_text = sib.get_text().strip().lower()
            if any(kw in heading_text for kw in ["event by", "sponsor", "additional"]):
                break
            # Also stop at unknown headings
            break
        if hasattr(sib, "name") and sib.name == "p":
            text_parts.append(sib.get_text())
        elif isinstance(sib, str):
            text_parts.append(sib)

    raw_text = "\n".join(text_parts)

    members = []
    lines = [ln.strip() for ln in raw_text.split("\n") if ln.strip()]
    for line in lines:
        entries = re.split(r"(?<=[a-z)\]])(?=[A-Z])", line)
        for entry in entries:
            entry = entry.strip()
            if not entry or len(entry) < 3:
                continue
            # Skip chair lines and boilerplate
            if re.match(r"(?:Artifact|The ACSAC)", entry, re.IGNORECASE):
                continue
            if any(
                skip in entry.lower()
                for skip in [
                    "return to top",
                    "event by",
                    "sponsor",
                    "additional link",
                    "code of conduct",
                    "privacy policy",
                    "contact us",
                    "mailing list",
                    "applied computer",
                    "committee was divided",
                ]
            ):
                continue
            if "," in entry:
                parts = entry.split(",", 1)
                name = parts[0].strip()
                affiliation = parts[1].strip()
            else:
                name = entry
                affiliation = ""
            name = re.sub(r"\s+", " ", name).strip()
            affiliation = re.sub(r"\s+", " ", affiliation).strip()
            if name and len(name) > 1 and not name.startswith("http") and name.lower() not in chair_names:
                members.append({"name": name, "affiliation": affiliation, "role": "member"})

    return members


def scrape_acsac_committee(year, session=None, cache_only=False):
    """Scrape AE committee from the ACSAC website.

    ACSAC publishes artifact committee data at two URL patterns:
    - ``https://www.acsac.org/{year}/committees/artifact/``  (2020-2022)
    - ``https://www.acsac.org/{year}/committees/artifacts/`` (2023+)

    Returns list of {name, affiliation, role} dicts, or None if not found.
    """
    # Try both URL patterns (slug changed from singular to plural in 2023)
    urls = [
        f"https://www.acsac.org/{year}/committees/artifacts/",
        f"https://www.acsac.org/{year}/committees/artifact/",
    ]

    soup = None
    for url in urls:
        html = _cached_fetch(url, session=session, cache_only=cache_only)
        if html is not None:
            soup = BeautifulSoup(html, "html.parser")
            break

    if soup is None:
        logger.info("  ACSAC: No committee page found for acsac%d", year)
        return None

    # Parse chairs
    chairs = _parse_acsac_chairs(soup)

    # Parse members from all relevant sections
    # 2020-2022: "Students" and "Mentors"
    # 2023+: "Reviewers" and "Mentors"
    members = _parse_acsac_section_members(soup, ["student", "reviewer"])
    mentors = _parse_acsac_section_members(soup, ["mentor"])

    # Fallback for flat-list pages (e.g. 2019) that have no section headings:
    # collect members from the text between <h1> and the footer.
    if not members and not mentors:
        members = _parse_acsac_flat_members(soup, chairs)

    all_members = chairs + mentors + members

    # Deduplicate by name (case-insensitive)
    seen = set()
    deduped = []
    for m in all_members:
        key = m["name"].lower()
        if key not in seen:
            seen.add(key)
            deduped.append(m)

    if deduped:
        chair_count = sum(1 for m in deduped if m["role"] == "chair")
        member_count = len(deduped) - chair_count
        logger.info("  ACSAC: Found %d members + %d chair(s) for acsac%d", member_count, chair_count, year)
    return deduped if deduped else None


# ── HotCRP scraper ───────────────────────────────────────────────────────────

# Maps (conference, year) to HotCRP PC-list URLs.
# URL pattern: https://{conf}{yy}ae.hotcrp.com/u/0/users/pc
# Only conferences with public HotCRP AE sites are listed here.
HOTCRP_URLS = {
    ("sosp", 2024): "https://sosp24ae.hotcrp.com/u/0/users/pc",
    ("sosp", 2025): "https://sosp25ae.hotcrp.com/u/0/users/pc",
}


def scrape_hotcrp_committee(conference, year, session=None, cache_only=False):
    """Scrape AE committee from a public HotCRP PC-list page.

    Parameters
    ----------
    conference : str
        Conference name (e.g. 'sosp')
    year : int
        4-digit year
    session : requests.Session, optional
    cache_only : bool
        If True, only return data from the disk cache.

    Returns
    -------
    list of {name, affiliation, role} dicts, or None if page not found
    """
    url = HOTCRP_URLS.get((conference.lower(), year))
    if url is None:
        return None

    html = _cached_fetch(url, session=session, cache_only=cache_only)
    if html is None:
        return None

    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        return None

    members = []
    for row in table.find_all("tr")[1:]:  # skip header row
        cells = row.find_all("td")
        if len(cells) < 2:
            continue

        name_cell = cells[0]
        affil_cell = cells[1]

        # Detect chair role from <span class="pcrole">chair</span>
        role_span = name_cell.find("span", class_="pcrole")
        is_chair = role_span is not None and "chair" in role_span.get_text(strip=True).lower()

        # Extract name: prefer <span class="taghl"> (highlighted name),
        # otherwise fall back to full cell text minus the role span
        name_span = name_cell.find("span", class_="taghl")
        if name_span:
            name = name_span.get_text(strip=True)
        else:
            # Remove the role span text from the cell text
            name = name_cell.get_text(strip=True)
            if role_span:
                name = name.replace(role_span.get_text(strip=True), "").strip()

        affiliation = affil_cell.get_text(strip=True)

        # Clean up
        name = re.sub(r"\s+", " ", name).strip()
        affiliation = re.sub(r"\s+", " ", affiliation).strip()

        # Skip placeholder entries ("[No name]", empty, "None")
        if not name or name == "[No name]" or len(name) < 2:
            continue

        role = "chair" if is_chair else "member"
        members.append({"name": name, "affiliation": affiliation, "role": role})

    if members:
        chair_count = sum(1 for m in members if m["role"] == "chair")
        member_count = len(members) - chair_count
        logger.info(
            "  HotCRP: Found %d members + %d chair(s) for %s%d",
            member_count,
            chair_count,
            conference,
            year,
        )
    return members if members else None


# ── Local / static committees ────────────────────────────────────────────────

_LOCAL_COMMITTEES_PATH = Path(__file__).resolve().parents[2] / "data" / "local_committees.yaml"


def _load_local_committees():
    """Load local committee data from data/local_committees.yaml.

    Returns dict of {conf_year_str: [{name, affiliation, role}, ...]}.
    """
    if not _LOCAL_COMMITTEES_PATH.exists():
        return {}
    with _LOCAL_COMMITTEES_PATH.open() as f:
        data = yaml.safe_load(f) or {}
    return data


# ── Public API ───────────────────────────────────────────────────────────────


def get_alternative_committees(conferences_needed):
    """Fetch committees from alternative sources for conferences not in sysartifacts/secartifacts.

    Parameters
    ----------
    conferences_needed : dict
        {conf_year_str: 'systems'|'security'} — conferences that need data.
        e.g. {'fast2024': 'systems', 'usenixsec2022': 'security'}

    Returns
    -------
    dict of {conf_year_str: [{name, affiliation}, ...]}
    """
    cache_only = os.getenv("SKIP_USENIX_SCRAPE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    results = {}
    local = _load_local_committees()
    sess = None if cache_only else _get_session()

    if cache_only:
        logger.info("  SKIP_USENIX_SCRAPE set — using cached scraping results only (no live HTTP)")

    for conf_year_str, _area in conferences_needed.items():
        m = re.match(r"^([a-zA-Z]+)(\d{4})$", conf_year_str)
        if not m:
            continue
        conf = m.group(1).lower()
        year = int(m.group(2))

        committee = None

        # Try web scraper (uses disk cache; cache_only=True means no live HTTP)
        if conf in USENIX_CONF_SLUGS:
            committee = scrape_usenix_committee(conf, year, session=sess, cache_only=cache_only)
        elif conf == "ches":
            committee = scrape_ches_committee(year, session=sess, cache_only=cache_only)
        elif conf == "pets":
            committee = scrape_pets_committee(year, session=sess, cache_only=cache_only)
        elif conf == "acsac":
            committee = scrape_acsac_committee(year, session=sess, cache_only=cache_only)

        # Try HotCRP (public pages, not blocked by SKIP_USENIX_SCRAPE)
        if not committee and (conf, year) in HOTCRP_URLS:
            hotcrp_sess = sess if sess is not None else _get_session()
            committee = scrape_hotcrp_committee(conf, year, session=hotcrp_sess, cache_only=False)

        # Fallback: local/static data (e.g. from local pipeline run)
        if not committee:
            committee = local.get(conf_year_str)

        if committee and len(committee) > 0:
            results[conf_year_str] = committee

    return results


def get_all_usenix_committees(conf_regex=None):
    """Scrape all available USENIX conference committees.

    Parameters
    ----------
    conf_regex : str, optional
        Regex to filter conference/year strings (e.g. '.20[2][0-5]')

    Returns
    -------
    dict of {conf_year_str: [{name, affiliation}, ...]}
    """
    results = {}
    sess = _get_session()

    for conf, years in USENIX_KNOWN_YEARS.items():
        for year in years:
            conf_year = f"{conf}{year}"
            if conf_regex and not re.search(conf_regex, conf_year):
                continue
            committee = scrape_usenix_committee(conf, year, session=sess)
            if committee:
                results[conf_year] = committee

    return results


# ── CLI for testing ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()

    import argparse

    parser = argparse.ArgumentParser(description="Scrape AE committees from alternative sources")
    parser.add_argument("--conference", type=str, help="Conference name (fast, osdi, usenixsec, woot, ches, pets)")
    parser.add_argument("--year", type=int, help="Year (e.g. 2024)")
    parser.add_argument("--all-usenix", action="store_true", help="Scrape all known USENIX committees")
    parser.add_argument("--all-ches", action="store_true", help="Scrape all CHES years 2021-2025")
    parser.add_argument("--all-pets", action="store_true", help="Scrape all PETS years 2020-2025")
    parser.add_argument("--all-acsac", action="store_true", help="Scrape all ACSAC years 2020-2025")
    args = parser.parse_args()

    if args.all_usenix:
        results = get_all_usenix_committees()
        for cy, members in sorted(results.items()):
            logger.info(f"{cy}: {len(members)} members")
    elif args.all_ches:
        sess = _get_session()
        for y in CHES_KNOWN_YEARS:
            committee = scrape_ches_committee(y, session=sess)
            if committee:
                logger.info(f"ches{y}: {len(committee)} members")
            else:
                logger.info(f"ches{y}: not found")
    elif args.all_pets:
        sess = _get_session()
        for y in PETS_KNOWN_YEARS:
            committee = scrape_pets_committee(y, session=sess)
            if committee:
                logger.info(f"pets{y}: {len(committee)} members")
            else:
                logger.info(f"pets{y}: not found")
    elif args.all_acsac:
        sess = _get_session()
        for y in ACSAC_KNOWN_YEARS:
            committee = scrape_acsac_committee(y, session=sess)
            if committee:
                logger.info(f"acsac{y}: {len(committee)} members")
            else:
                logger.info(f"acsac{y}: not found")
    elif args.conference and args.year:
        conf = args.conference.lower()
        if conf in USENIX_CONF_SLUGS:
            result = scrape_usenix_committee(conf, args.year)
        elif conf == "ches":
            result = scrape_ches_committee(args.year)
        elif conf == "pets":
            result = scrape_pets_committee(args.year)
        elif conf == "acsac":
            result = scrape_acsac_committee(args.year)
        else:
            logger.info(f"Unknown conference: {conf}")
            sys.exit(1)

        if result:
            logger.info(f"Found {len(result)} members:")
            for m in result:
                logger.info(f"  {m['name']} — {m['affiliation']}")
        else:
            logger.info("No committee data found.")
    else:
        parser.print_help()
