#!/usr/bin/env python3
"""
Scrape ACM Digital Library conference proceedings for paper artifact badges.

ACM conferences (e.g., CCS, SOSP) display artifact evaluation badges on
individual paper pages in the ACM DL.  This module:

  1. Uses the DBLP API to discover all papers for a given proceedings volume.
  2. Attempts to scrape badge information directly from ACM DL paper pages.
  3. Gracefully degrades when ACM DL access is blocked (Cloudflare 403),
     returning the DBLP paper list without badge data.

Usage examples:
  # Scrape CCS 2024 (attempts ACM DL, falls back to YAML)
  python acm_scrape.py --conference ccs --years 2024

  # Scrape CCS 2023 and 2024
  python acm_scrape.py --conference ccs --years 2023,2024

  # Output as YAML suitable for the pipeline
  python acm_scrape.py --conference ccs --years 2024 --format yaml
"""

import argparse
import json
import logging
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import yaml
from bs4 import BeautifulSoup

from src.utils.io.cache import _MISSING

from .repo_utils import (
    CACHE_DIR,
    CACHE_TTL,
    _read_cache,
    _session_with_retries,
    _write_cache,
)

# --------------------------------------------------------------------------- #
#  ACM Conference metadata                                                    #
# --------------------------------------------------------------------------- #

# Maps our internal conference key → dict of DBLP venue key(s) and
# proceedings DOI prefixes, by year.
#
# DBLP key:        Used to fetch the BibTeX listing from dblp.org/db/conf/<key>/
# Proceedings DOI:  The DOI of the ACM DL proceedings container (10.1145/…).
#                   Individual paper DOIs all share this prefix.
# Category:         'systems' or 'security' – used for website grouping.

ACM_CONFERENCES = {
    # CCS removed: ACM DL is blocked by Cloudflare and there is no
    # alternative public source for badge data (secartifacts does not
    # track CCS).  Re-add when a scrapable source becomes available.
    #
    # SOSP is already handled by sysartifacts, but we keep metadata here
    # in case the pipeline needs direct ACM DL scraping in the future.
    "sosp": {
        "dblp_key": "sosp",
        "category": "systems",
        "display_name": "SOSP",
        "proceedings_dois": {
            2023: "10.1145/3600006",
            2024: "10.1145/3694715",
        },
    },
}

# Badge name normalisation (ACM uses Artifacts Available / Artifacts Evaluated
# – Functional / Artifacts Evaluated – Reusable / Results Reproduced).
_BADGE_MAP = {
    "artifacts_available": "available",
    "artifacts available": "available",
    "available": "available",
    "artifacts_evaluated_functional": "functional",
    "artifacts evaluated functional": "functional",
    "artifacts_evaluated": "functional",  # generic → functional
    "functional": "functional",
    "artifacts_evaluated_reusable": "reusable",
    "artifacts evaluated reusable": "reusable",
    "reusable": "reusable",
    "results_reproduced": "reproduced",
    "results reproduced": "reproduced",
    "reproduced": "reproduced",
    "results_replicated": "reproduced",
    "results replicated": "reproduced",
    "replicated": "reproduced",
}


def _normalise_badge(text):
    """Normalise an ACM badge string to one of available/functional/reusable/reproduced."""
    key = re.sub(r"[^a-z ]", "", text.lower()).strip()
    key = re.sub(r"\s+", " ", key)
    return _BADGE_MAP.get(key, key)


# --------------------------------------------------------------------------- #
#  DBLP-based paper discovery                                                 #
# --------------------------------------------------------------------------- #


def _dblp_papers(dblp_key, year, session=None):
    """
    Get the list of papers for a proceedings volume from the pre-extracted
    DBLP data (JSON produced by ``src.utils.dblp_extract``).

    Returns a list of dicts with keys: title, doi, authors, dblp_url.
    """
    from src.utils.apis.dblp_extract import papers_for_venue_year

    conf_upper = dblp_key.upper()
    extracted = papers_for_venue_year(conf_upper, year)
    papers = []
    for p in extracted:
        authors_list = p.get("authors", [])
        papers.append(
            {
                "title": p["title"],
                "doi": p.get("doi", ""),
                "authors": ", ".join(authors_list) if isinstance(authors_list, list) else authors_list,
                "dblp_url": "",
            }
        )
    logger.info(f"  Got {len(papers)} papers from DBLP extraction cache")
    return papers


# --------------------------------------------------------------------------- #
#  ACM DL badge scraping                                                      #
# --------------------------------------------------------------------------- #


def _scrape_acm_paper_badges(doi, session=None):
    """
    Attempt to scrape artifact badges from an ACM DL paper page.

    Returns a list of normalised badge strings, or None on failure (403, etc.).
    The caller should check ``None`` to distinguish "no badges" from
    "ACM DL blocked".
    """
    if not doi:
        return None

    cache_key = f"acm_badges:{doi}"
    cached = _read_cache(CACHE_DIR, cache_key, ttl=CACHE_TTL, namespace="acm_badges")
    if cached is not _MISSING:
        return cached  # list of badges (possibly empty) or None

    sess = session or _session_with_retries()
    url = f"https://dl.acm.org/doi/{doi}"

    try:
        resp = sess.get(
            url,
            timeout=20,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
    except requests.RequestException as e:
        logger.warning(f"  Request error for {url}: {e}")
        return None

    if resp.status_code == 403:
        logger.warning(f"  Cloudflare block for {url} — cannot scrape")
        return None
    if resp.status_code != 200:
        logger.warning(f"  HTTP {resp.status_code} for {url}")
        _write_cache(CACHE_DIR, cache_key, None, namespace="acm_badges")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    badges = []

    # ACM DL badge images typically live in a div with class
    # 'badge-*' or as <img> tags whose alt/src contain badge names.
    for img in soup.find_all("img"):
        alt = (img.get("alt", "") or "").lower()
        src = (img.get("src", "") or "").lower()
        combined = alt + " " + src
        if "available" in combined:
            badges.append("available")
        elif "functional" in combined:
            badges.append("functional")
        elif "reusable" in combined:
            badges.append("reusable")
        elif "reproduced" in combined or "replicated" in combined:
            badges.append("reproduced")

    # De-duplicate while preserving order
    seen = set()
    deduped = []
    for b in badges:
        if b not in seen:
            seen.add(b)
            deduped.append(b)

    _write_cache(CACHE_DIR, cache_key, deduped, namespace="acm_badges")
    return deduped


def scrape_acm_proceedings(conference, year, session=None, max_workers=4, delay=0.5):
    """
    Scrape an ACM DL proceedings volume for paper titles and artifact badges.

    1. Gets papers from DBLP (always works).
    2. For each paper, tries to scrape badge info from ACM DL.
    3. If ACM DL is blocked (403), the function stops attempting further
       papers and returns papers with empty badge lists (partial data).

    Returns:
        (papers_list, acm_dl_accessible)  where acm_dl_accessible is a bool
        indicating whether ACM DL scraping succeeded.
    """
    conf_meta = ACM_CONFERENCES.get(conference)
    if not conf_meta:
        logger.info(f"  Unknown ACM conference: {conference}")
        return [], False

    dblp_key = conf_meta["dblp_key"]
    papers = _dblp_papers(dblp_key, year, session)
    if not papers:
        return [], False

    sess = session or _session_with_retries()
    acm_dl_accessible = True  # optimistic
    blocked_count = 0

    def _fetch_badges(paper):
        nonlocal acm_dl_accessible, blocked_count
        if not acm_dl_accessible:
            return paper, []
        badges = _scrape_acm_paper_badges(paper["doi"], sess)
        if badges is None:
            blocked_count += 1
            if blocked_count >= 3:
                acm_dl_accessible = False
                logger.warning(
                    "  ACM DL appears blocked (3 consecutive failures), skipping further scraping", file=sys.stderr
                )
            return paper, []
        time.sleep(delay)
        return paper, badges

    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_badges, p): p for p in papers}
        for i, future in enumerate(as_completed(futures), 1):
            paper, badges = future.result()
            paper_out = {
                "title": paper["title"],
                "doi": paper["doi"],
                "authors": paper["authors"],
                "badges": badges,
            }
            results.append(paper_out)
            if i % 20 == 0 or i == len(papers):
                logger.info(f"  Processed {i}/{len(papers)} papers …")

    with_badges = sum(1 for r in results if r["badges"])
    logger.info(
        f"  {conference.upper()} {year}: {len(results)} papers, "
        f"{with_badges} with badges"
        f" (ACM DL {'accessible' if acm_dl_accessible else 'BLOCKED'})",
        file=sys.stderr,
    )
    return results, acm_dl_accessible


# --------------------------------------------------------------------------- #
#  Public API (used by generate_statistics.py)                                #
# --------------------------------------------------------------------------- #


def scrape_conference_year(conference, year, session=None, max_workers=4, delay=0.5):
    """
    Get artifact data for an ACM conference/year via DBLP + ACM DL scraping.

    Returns a list of dicts ready for ``to_pipeline_format()``.
    """
    scraped, acm_ok = scrape_acm_proceedings(conference, year, session, max_workers, delay)
    return scraped


def to_pipeline_format(artifacts):
    """
    Convert scraped/merged artifacts to the format used by generate_statistics.py.
    Only includes papers that have at least one badge.
    """
    pipeline = []
    for a in artifacts:
        badges = a.get("badges", [])
        if not badges:
            continue
        entry = {
            "title": a.get("title", "Unknown"),
            "badges": ",".join(badges),
        }
        if a.get("doi"):
            entry["doi"] = a["doi"]
        if a.get("repository_url"):
            entry["repository_url"] = a["repository_url"]
        if a.get("artifact_url"):
            entry["artifact_url"] = a["artifact_url"]
        pipeline.append(entry)
    return pipeline


def get_acm_conferences():
    """Return the ACM_CONFERENCES dict for use by the pipeline."""
    return ACM_CONFERENCES


# --------------------------------------------------------------------------- #
#  CLI                                                                        #
# --------------------------------------------------------------------------- #


def main():
    parser = argparse.ArgumentParser(description="Scrape ACM DL proceedings for paper titles and artifact badges.")
    parser.add_argument(
        "--conference", "-c", type=str, required=True, help="Conference key(s), comma-separated (e.g. ccs, sosp)"
    )
    parser.add_argument(
        "--years", "-y", type=str, required=True, help="Year(s) to scrape, comma-separated (e.g. 2023,2024)"
    )
    parser.add_argument("--format", "-f", choices=["json", "yaml", "summary"], default="summary", help="Output format")
    parser.add_argument("--max-workers", type=int, default=4, help="Max parallel requests (default: 4)")
    parser.add_argument("--delay", type=float, default=0.5, help="Delay between requests in seconds (default: 0.5)")
    parser.add_argument("--all-papers", action="store_true", help="Include papers without badges")

    args = parser.parse_args()
    conferences = [c.strip().lower() for c in args.conference.split(",")]
    years = [int(y.strip()) for y in args.years.split(",")]

    all_results = {}
    for conf in conferences:
        for year in years:
            key = f"{conf}{year}"
            logger.info(f"\n{'=' * 60}")
            logger.info(f"Processing {conf.upper()} {year}")
            logger.info(f"{'=' * 60}")

            artifacts = scrape_conference_year(conf, year, max_workers=args.max_workers, delay=args.delay)
            if args.all_papers:
                all_results[key] = artifacts
            else:
                all_results[key] = to_pipeline_format(artifacts)

    if args.format == "json":
        logger.info(json.dumps(all_results, indent=2))
    elif args.format == "yaml":
        logger.info(yaml.dump(all_results, default_flow_style=False))
    else:
        logger.info(f"\n{'=' * 60}")
        logger.info("SUMMARY")
        logger.info(f"{'=' * 60}")
        for key, arts in sorted(all_results.items()):
            match = re.match(r"^([a-z]+)(\d{4})$", key)
            if match:
                cname, yr = match.groups()
            else:
                cname, yr = key, "?"
            total = len(arts)
            if args.all_papers:
                with_badges = sum(1 for a in arts if a.get("badges"))
            else:
                with_badges = total
            avail = sum(1 for a in arts if "available" in str(a.get("badges", "")))
            func = sum(1 for a in arts if "functional" in str(a.get("badges", "")))
            repro = sum(1 for a in arts if "reproduced" in str(a.get("badges", "")))
            logger.info(
                f"  {cname.upper()} {yr}: {total} papers"
                f" | {with_badges} with badges"
                f" (available={avail}, functional={func},"
                f" reproduced={repro})"
            )


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()
    main()

logger = logging.getLogger(__name__)
