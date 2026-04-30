#!/usr/bin/env python3
"""
Scrape ACSAC conference artifact evaluation pages for paper titles,
badges, and artifact URLs.

ACSAC publishes artifact evaluation results at:
  https://www.acsac.org/<YYYY>/program/artifacts/

The page groups papers under badge images (code_available, code_reviewed,
code_reproducible) in bullet lists.  Each bullet contains the paper title
and one or more links to the artifact.

Usage as a library:
  from src.scrapers.acsac_scrape import scrape_acsac_artifacts
  artifacts = scrape_acsac_artifacts(2025)

Usage standalone:
  python -m src.scrapers.acsac_scrape --years 2024,2025
"""

import argparse
import json
import logging
import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from src.utils.apis.http import create_session

logger = logging.getLogger(__name__)

ACSAC_ARTIFACTS_URL = "https://www.acsac.org/{year}/program/artifacts/"

# Map badge image filename fragments to canonical badge names
_BADGE_MAP = {
    "code_available": "available",
    "code_reviewed": "reviewed",
    "code_reproducible": "reproducible",
}

# Query parameters that look like ephemeral auth tokens and should be stripped.
_TOKEN_PARAMS = {"token"}


def _strip_tokens(url):
    """Remove ephemeral auth/access tokens from a URL for archival."""
    parsed = urlparse(url)
    if not parsed.query:
        return url
    params = parse_qs(parsed.query, keep_blank_values=True)
    cleaned = {k: v for k, v in params.items() if k.lower() not in _TOKEN_PARAMS}
    new_query = urlencode(cleaned, doseq=True) if cleaned else ""
    return urlunparse(parsed._replace(query=new_query))


def scrape_acsac_artifacts(year, session=None):
    """
    Scrape the ACSAC artifact evaluation page for a given year.

    Args:
        year: Conference year (int).
        session: Optional requests.Session to reuse.

    Returns:
        List of dicts with keys:
          - title (str): paper title
          - badges (list[str]): e.g. ['available'] or ['available', 'reviewed']
          - artifact_urls (list[str]): artifact link(s)
          - paper_url (str): always empty (ACSAC pages don't link to papers)

    Note: ACSAC awards each paper exactly one badge level (the highest),
    so each artifact appears once under its highest badge section.
    """
    if session is None:
        session = create_session()

    url = ACSAC_ARTIFACTS_URL.format(year=year)
    logger.info("Fetching %s", url)
    resp = session.get(url, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    content = soup.find("main") or soup.find("article") or soup.body
    if content is None:
        logger.warning("Could not find main content element for ACSAC %d", year)
        return []

    # First pass: collect raw entries with their single badge
    raw_entries = []
    current_badge = None

    for element in content.descendants:
        # Detect badge section from badge images.
        # NOTE: some ACSAC pages have incorrect alt text (e.g. "Code Available"
        # for the reviewed badge), so we match on src first, falling back to alt.
        if element.name == "img":
            src = element.get("src", "").lower()
            alt = element.get("alt", "").lower().replace(" ", "_")
            matched = False
            for fragment, badge_name in _BADGE_MAP.items():
                if fragment in src:
                    current_badge = badge_name
                    matched = True
                    break
            if not matched:
                for fragment, badge_name in _BADGE_MAP.items():
                    if fragment in alt:
                        current_badge = badge_name
                        break

        # Parse paper entries from list items.
        # NOTE: ACSAC pages use malformed HTML where <li> elements are nested
        # (no closing </li> tags), so element.get_text() would include all
        # subsequent entries.  We only collect direct children up to the first
        # child <li>.
        if element.name == "li" and current_badge:
            title_parts = []
            artifact_urls = []
            for child in element.children:
                if getattr(child, "name", None) == "li":
                    break
                if getattr(child, "name", None) == "a":
                    href = child.get("href", "").strip()
                    if href and not href.startswith("#"):
                        artifact_urls.append(_strip_tokens(href))
                elif isinstance(child, str):
                    title_parts.append(child.strip())

            title = " ".join(p for p in title_parts if p).strip()
            title = re.sub(r"^Title:\s*", "", title)

            if title:
                raw_entries.append(
                    {
                        "title": title,
                        "badge": current_badge,
                        "artifact_urls": artifact_urls,
                    }
                )

    # Convert to the common artifact format (list of badges per paper)
    # ACSAC gives each paper its highest badge, so badges is a single-element list
    artifacts = []
    for entry in raw_entries:
        artifacts.append(
            {
                "title": entry["title"],
                "badges": [entry["badge"]],
                "artifact_urls": entry["artifact_urls"],
                "paper_url": "",
            }
        )

    return artifacts


def main():
    parser = argparse.ArgumentParser(description="Scrape ACSAC artifact evaluation pages")
    parser.add_argument("--years", "-y", type=str, required=True, help="Comma-separated years")
    parser.add_argument("--format", type=str, default="json", choices=["json", "yaml"], help="Output format")
    args = parser.parse_args()

    years = [int(y.strip()) for y in args.years.split(",")]
    session = create_session()

    for year in years:
        artifacts = scrape_acsac_artifacts(year, session=session)
        logger.info("ACSAC %d: %d artifacts", year, len(artifacts))
        if args.format == "json":
            logger.info("%s", json.dumps(artifacts, indent=2))
        else:
            import yaml

            logger.info("%s", yaml.dump(artifacts, default_flow_style=False))


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()
    main()
