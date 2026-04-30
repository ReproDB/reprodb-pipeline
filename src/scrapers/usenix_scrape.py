#!/usr/bin/env python3
"""
Scrape USENIX conference technical-sessions pages (e.g. FAST, OSDI, ATC)
for paper titles and artifact evaluation badges.

Each USENIX conference publishes a program at:
  https://www.usenix.org/conference/<conf><yy>/technical-sessions

Individual presentation pages may include artifact badge images in a
``field-artifact-evaluated`` div. The badge type is inferred from the
image filename (available, functional, reproduced/reproduced).

Usage examples:
  # Scrape FAST 2025
  python usenix_scrape.py --conference fast --years 2025

  # Scrape FAST 2024 and 2025
  python usenix_scrape.py --conference fast --years 2024,2025

  # Scrape multiple conferences
  python usenix_scrape.py --conference fast,osdi --years 2024,2025

  # Output as YAML suitable for the pipeline
  python usenix_scrape.py --conference fast --years 2025 --format yaml
"""

import argparse
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import yaml
from bs4 import BeautifulSoup

from src.utils.io.cache import _MISSING

from .repo_utils import CACHE_DIR, CACHE_TTL, _read_cache, _write_cache

logger = logging.getLogger(__name__)
BASE_URL = "https://www.usenix.org"


# Map full year → short suffix used in USENIX URLs (e.g. 2025 → "25")
def _year_suffix(year):
    return str(year)[2:]


def get_session(session=None):
    """Return a requests.Session, optionally reusing an existing one."""
    if session is not None:
        return session
    from src.utils.apis.http import create_session

    return create_session()


def scrape_presentation_links(conference: str, year: int, session: requests.Session | None = None) -> list[str]:
    """
    Scrape the technical-sessions page for a USENIX conference and return
    a list of unique presentation paths (e.g. /conference/fast25/presentation/satija).
    """
    sess = get_session(session)
    suffix = _year_suffix(year)
    url = f"{BASE_URL}/conference/{conference}{suffix}/technical-sessions"

    logger.info(f"  Fetching program: {url}")
    cached = _read_cache(CACHE_DIR, url, ttl=CACHE_TTL, namespace="usenix")
    if cached is not _MISSING:
        links = cached
        logger.info(f"  Found {len(links)} unique presentation pages (cached)")
        return links

    resp = sess.get(url, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    # Find all links to presentation pages
    prefix = f"/conference/{conference}{suffix}/presentation/"
    links = set()
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if href.startswith(prefix):
            links.add(href)

    result = sorted(links)
    _write_cache(CACHE_DIR, url, result, namespace="usenix")
    logger.info(f"  Found {len(result)} unique presentation pages")
    return result


def scrape_paper_page(path: str, session: requests.Session | None = None) -> dict | None:
    """
    Scrape a single USENIX presentation page and extract:
      - title
      - authors (text)
      - artifact evaluation badges (from badge images)
      - paper PDF URL

    Returns a dict or None if the page is not a research paper.
    """
    sess = get_session(session)
    url = f"{BASE_URL}{path}"

    # Check cache first
    cached = _read_cache(CACHE_DIR, url, ttl=CACHE_TTL, namespace="usenix_paper")
    if cached is not _MISSING:
        return cached  # dict or None

    resp = sess.get(url, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    # Extract title from <h1> with id "page-title" or class "page__title"
    title_el = soup.find("h1", id="page-title") or soup.find("h1", class_="page__title")
    if not title_el:
        logger.warning(f"Could not find title element in {url} — page structure may have changed")
        _write_cache(CACHE_DIR, url, None, namespace="usenix_paper")
        return None
    title = title_el.get_text(strip=True)

    # Skip non-paper entries (keynotes, panels, etc.)
    skip_prefixes = (
        "keynote",
        "panel",
        "workshop",
        "tutorial",
        "honoring",
        "break",
        "lunch",
        "closing",
        "opening",
        "reception",
        "poster session",
        "work-in-progress",
    )
    if any(title.lower().startswith(p) for p in skip_prefixes):
        _write_cache(CACHE_DIR, url, None, namespace="usenix_paper")
        return None

    # Extract authors
    authors_div = soup.find("div", class_=re.compile(r"field-name-field-paper-people-text"))
    authors = ""
    if authors_div:
        authors = authors_div.get_text(strip=True)

    # Extract artifact badges
    badges = []
    artifact_div = soup.find("div", class_=re.compile(r"field-name-field-artifact-evaluated"))
    if artifact_div:
        for img in artifact_div.find_all("img"):
            src = img.get("src", "").lower()
            if "available" in src:
                badges.append("available")
            elif "functional" in src:
                badges.append("functional")
            elif "reproduced" in src or "replicated" in src:
                badges.append("reproduced")

    # Extract paper PDF URL
    paper_url = ""
    pdf_div = soup.find("div", class_=re.compile(r"field-name-field-final-paper-pdf"))
    if pdf_div:
        pdf_link = pdf_div.find("a", href=True)
        if pdf_link:
            paper_url = pdf_link["href"]
            if paper_url.startswith("/"):
                paper_url = BASE_URL + paper_url

    result = {
        "title": title,
        "authors": authors,
        "badges": badges,
        "paper_url": paper_url,
        "presentation_url": url,
    }
    _write_cache(CACHE_DIR, url, result, namespace="usenix_paper")
    return result


def scrape_conference_year(
    conference: str, year: int, session: requests.Session | None = None, max_workers: int = 4, delay: float = 0.5
) -> list[dict]:
    """
    Scrape all papers and badges for a conference/year combination.

    Args:
        conference: Conference short name (e.g. 'fast')
        year: Full year (e.g. 2025)
        session: Optional requests.Session
        max_workers: Number of parallel requests for paper pages
        delay: Delay between batches of requests (be polite)

    Returns:
        List of artifact dicts with badges
    """
    sess = get_session(session)
    paths = scrape_presentation_links(conference, year, sess)

    if not paths:
        logger.info(f"  No presentation pages found for {conference.upper()} {year}")
        return []

    artifacts = []
    papers_with_badges = 0

    # Scrape paper pages with controlled parallelism
    def _fetch(path):
        time.sleep(delay)  # be polite
        return scrape_paper_page(path, sess)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch, p): p for p in paths}
        for i, future in enumerate(as_completed(futures), 1):
            path = futures[future]
            try:
                result = future.result()
                if result is not None:
                    artifacts.append(result)
                    if result["badges"]:
                        papers_with_badges += 1
                    if i % 10 == 0 or i == len(paths):
                        logger.info(f"  Scraped {i}/{len(paths)} pages...")
            except Exception as e:
                logger.warning(f"  Error scraping {path}: {e}")

    logger.info(
        f"  {conference.upper()} {year}: {len(artifacts)} papers, {papers_with_badges} with artifact badges",
    )
    return artifacts


def scrape_organizers(conference: str, year: int, session: requests.Session | None = None) -> dict | None:
    """
    Scrape the AE committee chairs and members from the USENIX call-for-artifacts page.

    USENIX pages use two HTML structures for the AEC list:
      1. Structured <h3 class="grouping-field-heading"> sections (e.g. FAST 25, OSDI 25)
      2. Inline <h2>/<p> with <br>-separated entries (e.g. FAST 24)

    Args:
        conference: Conference short name (e.g. 'fast', 'osdi')
        year: Conference year (int)
        session: Optional requests.Session

    Returns:
        dict with 'chairs' (list of {'name': ..., 'affiliation': ...})
        and 'members' (list of {'name': ..., 'affiliation': ...}),
        or None if the page cannot be fetched or parsed.
    """
    sess = get_session(session)
    suffix = _year_suffix(year)
    url = f"{BASE_URL}/conference/{conference}{suffix}/call-for-artifacts"

    cache_key = f"{url}#organizers"
    cached = _read_cache(CACHE_DIR, cache_key, ttl=CACHE_TTL, namespace="usenix_organizers")
    if cached is not _MISSING:
        return cached

    logger.info(f"  Fetching organizers: {url}")
    try:
        resp = sess.get(url, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"  Warning: Could not fetch organizers page: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    def _clean_text(html_str):
        """Remove HTML tags and clean up whitespace/entities."""
        text = re.sub(r"&nbsp;", " ", html_str)
        text = re.sub(r"&quot;", '"', text)
        text = re.sub(r"<em>", "", text)
        text = re.sub(r"</em>", "", text)
        text = re.sub(r"<[^>]+>", "", text)
        return text.strip()

    def _parse_name_affiliation(text):
        """Parse 'Name, Affiliation' into a dict."""
        text = text.strip()
        if not text:
            return None
        # Split on first comma
        if ", " in text:
            name, affiliation = text.split(", ", 1)
        else:
            name, affiliation = text, ""
        return {"name": name.strip(), "affiliation": affiliation.strip()}

    def _extract_from_structured(heading_text, exact=False):
        """Extract entries from structured <h3 class='grouping-field-heading'> sections."""
        entries = []
        heading = None

        # Search via <span> child for reliable matching
        for h3 in soup.find_all("h3", class_="grouping-field-heading"):
            span = h3.find("span")
            if span:
                span_text = span.get_text().strip()
                if exact and span_text == heading_text or not exact and heading_text in span_text:
                    heading = h3
                    break

        if heading:
            # Collect all sibling divs until next h3
            for sibling in heading.find_next_siblings():
                if sibling.name == "h3":
                    break
                for div in sibling.find_all("div", class_=re.compile(r"field-content")):
                    text = _clean_text(str(div))
                    entry = _parse_name_affiliation(text)
                    if entry:
                        entries.append(entry)
        return entries

    def _extract_from_inline(section_html):
        """Extract entries from inline <p> with <br>-separated text."""
        entries = []
        # Split on <br> tags
        lines = re.split(r"<br\s*/?>", section_html)
        for line in lines:
            text = _clean_text(line)
            entry = _parse_name_affiliation(text)
            if entry:
                entries.append(entry)
        return entries

    chairs = []
    members = []

    # Try structured format first (FAST 25, OSDI 25 style)
    chairs = _extract_from_structured("Artifact Evaluation Committee Co-Chairs")
    members = _extract_from_structured("Artifact Evaluation Committee", exact=True)

    # Fall back to inline format (FAST 24 style: <h2> then <p> with <br>)
    if not chairs:
        h2_chairs = soup.find("h2", string=re.compile(r"Artifact Evaluation Committee Co-Chairs"))
        if h2_chairs:
            p_tag = h2_chairs.find_next("p")
            if p_tag:
                chairs = _extract_from_inline(str(p_tag))

    if not members:
        # Find the <h2>Artifact Evaluation Committee</h2> (not Co-Chairs)
        for h2 in soup.find_all("h2"):
            h2_text = h2.get_text(strip=True)
            if h2_text == "Artifact Evaluation Committee":
                # Skip comment blocks, find the actual <p> with members
                for sibling in h2.find_next_siblings():
                    if sibling.name in ("h2", "h3"):
                        break
                    if sibling.name == "p" and "<br" in str(sibling):
                        members = _extract_from_inline(str(sibling))
                        if members:
                            break
                break

    if not chairs and not members:
        logger.warning(f"  Warning: No organizer data found for {conference.upper()} {year}")
        _write_cache(CACHE_DIR, cache_key, None, namespace="usenix_organizers")
        return None

    result = {"chairs": chairs, "members": members}
    logger.info(f"  Found {len(chairs)} chairs and {len(members)} committee members")
    _write_cache(CACHE_DIR, cache_key, result, namespace="usenix_organizers")
    return result


def to_pipeline_format(artifacts):
    """
    Convert scraped artifacts to the format used by the existing pipeline
    (matching parse_results_md.py output format).
    """
    pipeline_artifacts = []
    for a in artifacts:
        if not a["badges"]:
            continue  # Only include papers that went through AE
        entry = {
            "title": a["title"],
            "badges": ",".join(a["badges"]),
        }
        if a.get("paper_url"):
            entry["paper_url"] = a["paper_url"]
        pipeline_artifacts.append(entry)
    return pipeline_artifacts


def main():
    parser = argparse.ArgumentParser(description="Scrape USENIX conference pages for paper titles and artifact badges.")
    parser.add_argument(
        "--conference",
        "-c",
        type=str,
        required=True,
        help="Conference short name(s), comma-separated (e.g. fast, osdi, atc)",
    )
    parser.add_argument(
        "--years", "-y", type=str, required=True, help="Year(s) to scrape, comma-separated (e.g. 2024,2025)"
    )
    parser.add_argument(
        "--format",
        "-f",
        choices=["json", "yaml", "summary"],
        default="summary",
        help="Output format (default: summary)",
    )
    parser.add_argument(
        "--max-workers", type=int, default=4, help="Max parallel requests per conference/year (default: 4)"
    )
    parser.add_argument("--delay", type=float, default=0.3, help="Delay in seconds between requests (default: 0.3)")
    parser.add_argument(
        "--all-papers",
        action="store_true",
        help="Include papers without badges in output (default: only badged papers)",
    )

    args = parser.parse_args()

    conferences = [c.strip().lower() for c in args.conference.split(",")]
    years = [int(y.strip()) for y in args.years.split(",")]

    session = get_session()
    all_results = {}

    for conf in conferences:
        for year in years:
            key = f"{conf}{year}"
            logger.info(f"\n{'=' * 60}")
            logger.info(f"Scraping {conf.upper()} {year}")
            logger.info(f"{'=' * 60}")

            artifacts = scrape_conference_year(conf, year, session, max_workers=args.max_workers, delay=args.delay)

            if args.all_papers:
                all_results[key] = artifacts
            else:
                all_results[key] = to_pipeline_format(artifacts)

    # Output
    if args.format == "json":
        logger.info(json.dumps(all_results, indent=2))
    elif args.format == "yaml":
        logger.info(yaml.dump(all_results, default_flow_style=False))
    else:
        # Summary
        logger.info(f"\n{'=' * 60}")
        logger.info("SUMMARY")
        logger.info(f"{'=' * 60}")
        for key, artifacts in sorted(all_results.items()):
            conf_name, year = re.match(r"^([a-z]+)(\d{4})$", key).groups()
            total = len(artifacts)
            if args.all_papers:
                with_badges = sum(1 for a in artifacts if a.get("badges"))
                avail = sum(1 for a in artifacts if "available" in (a.get("badges") or []))
                func = sum(1 for a in artifacts if "functional" in (a.get("badges") or []))
                repro = sum(1 for a in artifacts if "reproduced" in (a.get("badges") or []))
            else:
                with_badges = total
                avail = sum(1 for a in artifacts if "available" in a.get("badges", ""))
                func = sum(1 for a in artifacts if "functional" in a.get("badges", ""))
                repro = sum(1 for a in artifacts if "reproduced" in a.get("badges", ""))
            logger.info(
                f"  {conf_name.upper()} {year}: {total} papers"
                f" | {with_badges} with badges"
                f" (available={avail}, functional={func}, reproduced={repro})"
            )


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()

    main()
