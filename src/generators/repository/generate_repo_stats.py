"""
Generate repository statistics (stars, forks, etc.) for the website.

Collects stats from GitHub/Zenodo/Figshare for all scraped artifacts and writes:
  - _data/repo_stats.yml              — per-conference/year aggregates (for website)
  - assets/data/repo_stats_detail.json — per-repo detail (for analysis/figures)

Usage:
  python generate_repo_stats.py --conf_regex '.*20[12][0-9]' --output_dir ../reprodb.github.io/src
"""

import argparse
import logging
import re
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import yaml

from src.models.aggregates.repo_stats import RepoStatsEntry
from src.scrapers.parse_results_md import get_ae_results
from src.scrapers.repo_utils import _normalise_github_repo_url
from src.utils.collection.collect_artifact_stats import figshare_stats, github_stats, zenodo_stats
from src.utils.collection.test_artifact_repositories import check_artifact_exists
from src.utils.io.io import load_json, load_validated_json, load_yaml, resolve_data_path, save_json, save_yaml
from src.utils.normalization.conference import conf_area as _conf_area
from src.utils.normalization.conference import parse_conf_year as extract_conference_name

logger = logging.getLogger(__name__)

# ── Excluded repos ──────────────────────────────────────────────────────────
_EXCLUDED_REPOS_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "excluded_repos.yaml"
_excluded_repos: set[str] | None = None


def _load_excluded_repos() -> set[str]:
    """Return the set of owner/repo strings that should be excluded (lowercased)."""
    global _excluded_repos  # noqa: PLW0603
    if _excluded_repos is None:
        if _EXCLUDED_REPOS_PATH.exists():
            with _EXCLUDED_REPOS_PATH.open() as fh:
                repos = yaml.safe_load(fh) or []
            _excluded_repos = {r.lower() for r in repos}
        else:
            _excluded_repos = set()
    return _excluded_repos


def _is_excluded_repo(url: str) -> bool:
    """Return True if the repo should be excluded from stats."""
    normalized = _normalise_github_repo_url(url)
    if not normalized:
        return False
    # Extract owner/repo from https://github.com/owner/repo
    owner_repo = normalized.split("github.com/", 1)[-1].lower()
    return owner_repo in _load_excluded_repos()


def collect_stats_for_results(results, url_keys=None):
    """Collect repository stats for all artifacts.

    Expands multi-valued URL fields, deduplicates URLs, then fetches
    GitHub/Zenodo/Figshare stats in parallel.  Returns a list of
    per-URL stat dicts.
    """
    if url_keys is None:
        url_keys = ["repository_url", "artifact_url", "github_url", "second_repository_url", "bitbucket_url"]

    # First pass: extract ALL URLs from list-valued fields and create expanded artifact entries
    # This ensures we collect stats for every artifact location, not just the first
    expanded_artifacts = {}
    for conf_year, artifacts in results.items():
        expanded_artifacts[conf_year] = []
        for artifact in artifacts:
            # Collect all URLs from this artifact (including multi-valued fields)
            all_urls_by_key = {}

            # Add single-valued URL fields
            for url_key in url_keys:
                if url_key in artifact and artifact[url_key]:
                    all_urls_by_key[url_key] = [artifact[url_key]]

            # Add URLs from list-valued fields (artifact_urls, additional_urls, etc.)
            for list_key in ["artifact_urls", "additional_urls"]:
                if list_key in artifact and isinstance(artifact[list_key], list):
                    for url in artifact[list_key]:
                        if isinstance(url, str) and url:
                            # Map back to single key: artifact_urls -> artifact_url
                            flat_key = list_key.rstrip("s")
                            if flat_key not in all_urls_by_key:
                                all_urls_by_key[flat_key] = []
                            if url not in all_urls_by_key[flat_key]:
                                all_urls_by_key[flat_key].append(url)

            # Create separate artifact entry for each URL to process
            if all_urls_by_key:
                for url_key, urls in all_urls_by_key.items():
                    for url in urls:
                        artifact_copy = {
                            k: v for k, v in artifact.items() if k not in ["artifact_urls", "additional_urls"]
                        }
                        artifact_copy[url_key] = url
                        expanded_artifacts[conf_year].append(artifact_copy)
            else:
                # No URLs found, keep original artifact
                expanded_artifacts[conf_year].append(artifact)

    results = expanded_artifacts

    # Filter url_keys to only those that actually appear in the data
    present_keys = set()
    for artifacts in results.values():
        for artifact in artifacts:
            for key in url_keys:
                if key in artifact and artifact[key]:
                    present_keys.add(key)
    url_keys = [k for k in url_keys if k in present_keys]
    if not url_keys:
        logger.warning("  Warning: No URL keys found in artifact data. No repository stats to collect.")
        return []
    logger.info(f"  Scanning URL fields: {', '.join(url_keys)}")

    # Check which URLs exist
    results, _, _ = check_artifact_exists(results, url_keys)

    # Build deduplicated list of (url, conf_name, year, title) tuples to fetch.
    # For GitHub URLs, deduplicate at the owner/repo level so different
    # branches/tags/paths within the same repo don't create separate stats
    # entries. Non-GitHub URLs are deduplicated by their full URL.
    fetch_tasks = []
    seen_urls: set[str] = set()  # normalized keys used for dedup
    # Keep track of all (url, conf, year, title) per normalized GitHub repo
    # so we can record each paper that uses a given repo.
    github_repo_papers: dict[str, list[tuple[str, str, int, str]]] = defaultdict(list)
    for conf_year, artifacts in results.items():
        conf_name, year = extract_conference_name(conf_year)
        if year is None:
            continue
        for artifact in artifacts:
            for url_key in url_keys:
                url = artifact.get(url_key, "")
                exists_key = f"{url_key}_exists"
                if not artifact.get(exists_key, False) or not url:
                    continue

                # Normalize for deduplication
                if "github.com/" in url:
                    # Exclude repos in the excluded list
                    if _is_excluded_repo(url):
                        logger.debug(f"  Excluded repo: {url}")
                        continue
                    norm = _normalise_github_repo_url(url) or url.rstrip("/")
                else:
                    norm = url.rstrip("/")

                title = artifact.get("title", "Unknown")
                if norm in seen_urls:
                    # Still track the paper for this repo
                    if "github.com/" in url:
                        github_repo_papers[norm].append((url, conf_name, year, title))
                    continue
                seen_urls.add(norm)
                fetch_tasks.append((url, conf_name, year, title))
                if "github.com/" in url:
                    github_repo_papers[norm].append((url, conf_name, year, title))

    max_workers = 8
    logger.info(f"  Collecting stats for {len(fetch_tasks)} unique URLs ({max_workers} workers)")

    def _fetch_stats(url):
        """Fetch stats for a single URL (thread-safe via disk cache)."""
        try:
            if "github.com/" in url:
                return github_stats(url), "github"
            if "zenodo" in url:
                return zenodo_stats(url), "zenodo"
            if "figshare" in url:
                return figshare_stats(url), "figshare"
        except Exception as e:
            logger.error(f"  Error collecting stats for {url}: {e}")
        return None, "unknown"

    all_stats = []
    stats_collected = 0
    # Track discovered GitHub URLs from Zenodo/Figshare linked_github_urls
    discovered_github: list[tuple[str, str, int, str]] = []  # (url, conf, year, title)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        pending = {pool.submit(_fetch_stats, url): (url, conf, yr, title) for url, conf, yr, title in fetch_tasks}
        for i, future in enumerate(as_completed(pending), 1):
            url, conf_name, year, title = pending[future]
            stats, source = future.result()
            if stats:
                stats_collected += 1
                entry = {
                    "conference": conf_name,
                    "year": year,
                    "title": title,
                    "url": url,
                    "source": source,
                }
                entry.update(stats)
                all_stats.append(entry)

                # For GitHub repos used by multiple papers, emit additional
                # entries so each paper is represented in per-conference stats.
                if source == "github":
                    norm = _normalise_github_repo_url(url)
                    if norm and norm in github_repo_papers:
                        for extra_url, extra_conf, extra_yr, extra_title in github_repo_papers[norm]:
                            if extra_title == title and extra_conf == conf_name and extra_yr == year:
                                continue  # skip the primary entry already added
                            extra_entry = dict(entry)
                            extra_entry["conference"] = extra_conf
                            extra_entry["year"] = extra_yr
                            extra_entry["title"] = extra_title
                            extra_entry["url"] = extra_url
                            all_stats.append(extra_entry)

                # Collect any linked GitHub URLs discovered from Zenodo/Figshare
                for gh_url in stats.get("linked_github_urls", []):
                    gh_norm = _normalise_github_repo_url(gh_url) or gh_url
                    if gh_norm not in seen_urls and not _is_excluded_repo(gh_url):
                        seen_urls.add(gh_norm)
                        discovered_github.append((gh_url, conf_name, year, title))
            if i % 100 == 0 or i == len(fetch_tasks):
                logger.info(f"  Progress: {i}/{len(fetch_tasks)} URLs fetched, {stats_collected} stats collected")

    # Second pass: fetch GitHub stats for repos discovered via Zenodo/Figshare links
    if discovered_github:
        logger.info(
            f"  Discovered {len(discovered_github)} additional GitHub repos from Zenodo/Figshare linked_github_urls"
        )
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            pending2 = {
                pool.submit(_fetch_stats, url): (url, conf, yr, title) for url, conf, yr, title in discovered_github
            }
            for future in as_completed(pending2):
                url, conf_name, year, title = pending2[future]
                stats, source = future.result()
                if stats:
                    stats_collected += 1
                    entry = {
                        "conference": conf_name,
                        "year": year,
                        "title": title,
                        "url": url,
                        "source": source,
                    }
                    entry.update(stats)
                    all_stats.append(entry)

    return all_stats


def aggregate_stats(all_stats):
    """Aggregate per-conference and per-year statistics.

    When the same GitHub repository (by ``name``, i.e. ``owner/repo``)
    appears for multiple papers, each paper gets its own entry in the
    detail list but the repo's stars/forks are counted only **once** in
    aggregate totals (per-conference, per-year, and overall).
    """
    # Per-conference aggregates
    by_conf = defaultdict(
        lambda: {
            "github_repos": 0,
            "total_stars": 0,
            "total_forks": 0,
            "max_stars": 0,
            "max_forks": 0,
            "zenodo_repos": 0,
            "total_views": 0,
            "total_downloads": 0,
            "years": defaultdict(
                lambda: {"github_repos": 0, "stars": 0, "forks": 0, "_star_values": [], "_fork_values": []}
            ),
            "all_github_entries": [],
            "_seen_repos": set(),  # track repos already counted for this conf
        }
    )

    by_year = defaultdict(
        lambda: {
            "github_repos": 0,
            "total_stars": 0,
            "total_forks": 0,
            "max_stars": 0,
            "max_forks": 0,
            "zenodo_repos": 0,
            "total_views": 0,
            "total_downloads": 0,
            "_seen_repos": set(),
            "_star_values": [],
            "_fork_values": [],
        }
    )

    overall = {
        "github_repos": 0,
        "total_stars": 0,
        "total_forks": 0,
        "max_stars": 0,
        "max_forks": 0,
        "zenodo_repos": 0,
        "total_views": 0,
        "total_downloads": 0,
        "avg_stars": 0,
        "avg_forks": 0,
        "median_stars": 0,
        "median_forks": 0,
    }
    overall_seen_repos: set[str] = set()
    overall_star_values: list[int] = []
    overall_fork_values: list[int] = []

    for s in all_stats:
        conf = s["conference"]
        year = s["year"]

        if s["source"] == "github":
            stars = s.get("github_stars", 0) or 0
            forks = s.get("github_forks", 0) or 0
            repo_name = s.get("name", "") or ""

            # Determine the normalized URL for display — prefer original (has tag info)
            url = s.get("url", "")

            # Always add to the detail list (one entry per paper×repo)
            by_conf[conf]["all_github_entries"].append(
                {
                    "title": s.get("title", "Unknown"),
                    "url": url,
                    "conference": conf,
                    "year": year,
                    "area": _conf_area(conf),
                    "stars": stars,
                    "forks": forks,
                    "description": (s.get("description", "") or "")[:120],
                    "language": s.get("language", "") or "",
                    "name": repo_name,
                    "pushed_at": s.get("pushed_at", ""),
                }
            )

            # Only count stars/forks once per unique repo in aggregates
            repo_key = repo_name.lower() if repo_name else url.rstrip("/")

            if repo_key not in by_conf[conf]["_seen_repos"]:
                by_conf[conf]["_seen_repos"].add(repo_key)
                by_conf[conf]["github_repos"] += 1
                by_conf[conf]["total_stars"] += stars
                by_conf[conf]["total_forks"] += forks
                by_conf[conf]["max_stars"] = max(by_conf[conf]["max_stars"], stars)
                by_conf[conf]["max_forks"] = max(by_conf[conf]["max_forks"], forks)

            year_key = f"{repo_key}@{year}"
            if year_key not in by_conf[conf].get("_seen_year_repos", set()):
                by_conf[conf].setdefault("_seen_year_repos", set()).add(year_key)
                by_conf[conf]["years"][year]["github_repos"] += 1
                by_conf[conf]["years"][year]["stars"] += stars
                by_conf[conf]["years"][year]["forks"] += forks
                by_conf[conf]["years"][year]["_star_values"].append(stars)
                by_conf[conf]["years"][year]["_fork_values"].append(forks)

            if repo_key not in by_year[year]["_seen_repos"]:
                by_year[year]["_seen_repos"].add(repo_key)
                by_year[year]["github_repos"] += 1
                by_year[year]["total_stars"] += stars
                by_year[year]["total_forks"] += forks
                by_year[year]["max_stars"] = max(by_year[year]["max_stars"], stars)
                by_year[year]["max_forks"] = max(by_year[year]["max_forks"], forks)
                by_year[year]["_star_values"].append(stars)
                by_year[year]["_fork_values"].append(forks)

            if repo_key not in overall_seen_repos:
                overall_seen_repos.add(repo_key)
                overall["github_repos"] += 1
                overall["total_stars"] += stars
                overall["total_forks"] += forks
                overall["max_stars"] = max(overall["max_stars"], stars)
                overall["max_forks"] = max(overall["max_forks"], forks)
                overall_star_values.append(stars)
                overall_fork_values.append(forks)

        elif s["source"] == "zenodo":
            views = s.get("zenodo_views", 0) or 0
            downloads = s.get("zenodo_downloads", 0) or 0

            by_conf[conf]["zenodo_repos"] += 1
            by_conf[conf]["total_views"] += views
            by_conf[conf]["total_downloads"] += downloads

            by_year[year]["zenodo_repos"] += 1
            by_year[year]["total_views"] += views
            by_year[year]["total_downloads"] += downloads

            overall["zenodo_repos"] += 1
            overall["total_views"] += views
            overall["total_downloads"] += downloads

    if overall["github_repos"] > 0:
        overall["avg_stars"] = round(overall["total_stars"] / overall["github_repos"], 1)
        overall["avg_forks"] = round(overall["total_forks"] / overall["github_repos"], 1)
        overall["median_stars"] = round(statistics.median(overall_star_values), 1)
        overall["median_forks"] = round(statistics.median(overall_fork_values), 1)
        if len(overall_star_values) >= 2:
            q_stars = statistics.quantiles(overall_star_values, n=4)
            q_forks = statistics.quantiles(overall_fork_values, n=4)
            overall["p25_stars"] = round(q_stars[0], 1)
            overall["p75_stars"] = round(q_stars[2], 1)
            overall["p25_forks"] = round(q_forks[0], 1)
            overall["p75_forks"] = round(q_forks[2], 1)
        else:
            overall["p25_stars"] = overall["median_stars"]
            overall["p75_stars"] = overall["median_stars"]
            overall["p25_forks"] = overall["median_forks"]
            overall["p75_forks"] = overall["median_forks"]

    # Convert to serializable format
    conf_stats = []
    for conf_name in sorted(by_conf.keys()):
        d = by_conf[conf_name]
        avg_stars = round(d["total_stars"] / d["github_repos"], 1) if d["github_repos"] > 0 else 0
        avg_forks = round(d["total_forks"] / d["github_repos"], 1) if d["github_repos"] > 0 else 0
        # Compute conference-level medians from all_github_entries (deduplicated by _seen_repos)
        conf_star_vals = sorted(e["stars"] for e in d["all_github_entries"])
        conf_fork_vals = sorted(e["forks"] for e in d["all_github_entries"])
        median_stars = round(statistics.median(conf_star_vals), 1) if conf_star_vals else 0
        median_forks = round(statistics.median(conf_fork_vals), 1) if conf_fork_vals else 0
        if len(conf_star_vals) >= 2:
            q_stars = statistics.quantiles(conf_star_vals, n=4)
            q_forks = statistics.quantiles(conf_fork_vals, n=4)
            p25_stars = round(q_stars[0], 1)
            p75_stars = round(q_stars[2], 1)
            p25_forks = round(q_forks[0], 1)
            p75_forks = round(q_forks[2], 1)
        else:
            p25_stars = median_stars
            p75_stars = median_stars
            p25_forks = median_forks
            p75_forks = median_forks
        year_list = []
        for yr in sorted(d["years"].keys()):
            yd = d["years"][yr]
            yr_median_stars = round(statistics.median(yd["_star_values"]), 1) if yd["_star_values"] else 0
            yr_median_forks = round(statistics.median(yd["_fork_values"]), 1) if yd["_fork_values"] else 0
            if len(yd["_star_values"]) >= 2:
                yr_q_stars = statistics.quantiles(yd["_star_values"], n=4)
                yr_q_forks = statistics.quantiles(yd["_fork_values"], n=4)
                yr_p25_stars = round(yr_q_stars[0], 1)
                yr_p75_stars = round(yr_q_stars[2], 1)
                yr_p25_forks = round(yr_q_forks[0], 1)
                yr_p75_forks = round(yr_q_forks[2], 1)
            else:
                yr_p25_stars = yr_median_stars
                yr_p75_stars = yr_median_stars
                yr_p25_forks = yr_median_forks
                yr_p75_forks = yr_median_forks
            year_list.append(
                {
                    "year": yr,
                    "github_repos": yd["github_repos"],
                    "total_stars": yd["stars"],
                    "total_forks": yd["forks"],
                    "avg_stars": round(yd["stars"] / yd["github_repos"], 1) if yd["github_repos"] > 0 else 0,
                    "avg_forks": round(yd["forks"] / yd["github_repos"], 1) if yd["github_repos"] > 0 else 0,
                    "median_stars": yr_median_stars,
                    "median_forks": yr_median_forks,
                    "p25_stars": yr_p25_stars,
                    "p75_stars": yr_p75_stars,
                    "p25_forks": yr_p25_forks,
                    "p75_forks": yr_p75_forks,
                }
            )
        # Top 5 repos by stars
        top_repos = sorted(d["all_github_entries"], key=lambda x: x["stars"], reverse=True)[:5]
        conf_stats.append(
            {
                "name": conf_name,
                "github_repos": d["github_repos"],
                "total_stars": d["total_stars"],
                "total_forks": d["total_forks"],
                "avg_stars": avg_stars,
                "avg_forks": avg_forks,
                "median_stars": median_stars,
                "median_forks": median_forks,
                "p25_stars": p25_stars,
                "p75_stars": p75_stars,
                "p25_forks": p25_forks,
                "p75_forks": p75_forks,
                "max_stars": d["max_stars"],
                "max_forks": d["max_forks"],
                "years": year_list,
                "top_repos": top_repos,
            }
        )

    year_stats = []
    for yr in sorted(by_year.keys()):
        d = by_year[yr]
        avg_stars = round(d["total_stars"] / d["github_repos"], 1) if d["github_repos"] > 0 else 0
        avg_forks = round(d["total_forks"] / d["github_repos"], 1) if d["github_repos"] > 0 else 0
        median_stars = round(statistics.median(d["_star_values"]), 1) if d["_star_values"] else 0
        median_forks = round(statistics.median(d["_fork_values"]), 1) if d["_fork_values"] else 0
        if len(d["_star_values"]) >= 2:
            q_stars = statistics.quantiles(d["_star_values"], n=4)
            q_forks = statistics.quantiles(d["_fork_values"], n=4)
            yr_p25_stars = round(q_stars[0], 1)
            yr_p75_stars = round(q_stars[2], 1)
            yr_p25_forks = round(q_forks[0], 1)
            yr_p75_forks = round(q_forks[2], 1)
        else:
            yr_p25_stars = median_stars
            yr_p75_stars = median_stars
            yr_p25_forks = median_forks
            yr_p75_forks = median_forks
        year_stats.append(
            {
                "year": yr,
                "github_repos": d["github_repos"],
                "total_stars": d["total_stars"],
                "total_forks": d["total_forks"],
                "avg_stars": avg_stars,
                "avg_forks": avg_forks,
                "median_stars": median_stars,
                "median_forks": median_forks,
                "p25_stars": yr_p25_stars,
                "p75_stars": yr_p75_stars,
                "p25_forks": yr_p25_forks,
                "p75_forks": yr_p75_forks,
                "max_stars": d["max_stars"],
                "max_forks": d["max_forks"],
            }
        )

    overall["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Per-repo detail: all GitHub entries with individual star/fork counts
    all_github_detail = []
    for conf_name in sorted(by_conf.keys()):
        all_github_detail.extend(by_conf[conf_name]["all_github_entries"])

    return {
        "overall": overall,
        "by_conference": conf_stats,
        "by_year": year_stats,
        "all_github_repos": all_github_detail,
    }


def _normalize_title(title: str) -> str:
    """Lowercase, strip non-alphanumeric for fuzzy title matching."""
    return re.sub(r"[^a-z0-9]", "", title.lower())


def _enrich_top_repos(aggregated: dict, all_results: dict, output_dir: Path | None) -> None:
    """Add authors and badges to each top_repos entry by matching on title.

    * badges  — from the scraped artifact results (all_results)
    * authors — from paper_authors_map.json (generated by an earlier pipeline step)
    """
    # Build title → badges lookup from scraped results
    badge_by_title: dict[str, list[str]] = {}
    for artifacts in all_results.values():
        for art in artifacts:
            title = art.get("title", "")
            badges_raw = art.get("badges", "")
            if title and badges_raw:
                badges = [b.strip() for b in badges_raw.split(",")] if isinstance(badges_raw, str) else list(badges_raw)
                norm = _normalize_title(title)
                if norm and badges:
                    badge_by_title[norm] = badges

    # Build title → authors lookup from paper_authors_map.json
    author_by_title: dict[str, list[str]] = {}
    if output_dir:
        pam_path = resolve_data_path(output_dir, "paper_authors_map.json")
        if pam_path.exists():
            try:
                pam = load_json(pam_path)
                for entry in pam:
                    norm = _normalize_title(entry.get("normalized_title", "") or entry.get("title", ""))
                    authors = entry.get("authors", [])
                    if norm and authors:
                        author_by_title[norm] = authors
            except Exception as exc:
                logger.warning(f"Could not load paper_authors_map.json for enrichment: {exc}")

    enriched = 0
    for conf in aggregated.get("by_conference", []):
        for repo in conf.get("top_repos", []):
            norm = _normalize_title(repo.get("title", ""))
            if norm in badge_by_title:
                repo["badges"] = badge_by_title[norm]
                enriched += 1
            if norm in author_by_title:
                repo["authors"] = author_by_title[norm]

    logger.info(f"Enriched {enriched} top-repo entries with badges/authors")


def main():
    parser = argparse.ArgumentParser(description="Generate repository statistics.")
    parser.add_argument("--conf_regex", type=str, default=".*20[12][0-9]", help="Regex for conference names/years")
    parser.add_argument("--output_dir", type=str, default=None, help="Website repo root directory")
    parser.add_argument("--refresh", action="store_true", help="Re-fetch all stats instead of only new artifacts")
    args = parser.parse_args()

    # Try to load the cached results written by generate_statistics.py to
    # avoid re-scraping every results.md file.
    cache_path = None
    if args.output_dir:
        cache_path = Path(args.output_dir) / "_data" / "all_results_cache.yml"
    if not cache_path or not cache_path.exists():
        # Fallback: repo root .cache directory
        repo_root = Path(__file__).resolve().parent.parent.parent
        cache_path = repo_root / ".cache" / "all_results_cache.yml"

    if cache_path.exists():
        logger.info(f"Loading cached results from {cache_path}...")
        all_results = load_yaml(cache_path) or {}
        # Filter by conf_regex (cache may contain more conferences)
        all_results = {k: v for k, v in all_results.items() if re.search(args.conf_regex, k)}
        logger.info(
            f"Loaded {sum(len(v) for v in all_results.values())} artifacts across {len(all_results)} conference-years (from cache)"
        )
    else:
        logger.info("Collecting artifact results (no cache found, scraping)...")
        sys_results = get_ae_results(args.conf_regex, "sys")
        sec_results = get_ae_results(args.conf_regex, "sec")
        all_results = {**sys_results, **sec_results}
        logger.info(
            f"Found {sum(len(v) for v in all_results.values())} artifacts across {len(all_results)} conference-years"
        )

    # Load existing repo stats from the website (historical data).
    # Only fetch stats for NEW artifacts not already in the historical data,
    # unless --refresh is used which forces a full re-fetch.
    existing_stats = []
    existing_urls = set()
    if not args.refresh and args.output_dir:
        detail_path = resolve_data_path(Path(args.output_dir), "repo_stats_detail.json")
        if detail_path.exists():
            validated = load_validated_json(detail_path, RepoStatsEntry, default=[])
            # Convert back to dicts for downstream merging with new stats
            existing_stats = [e.model_dump() if hasattr(e, "model_dump") else e for e in validated]
            # Filter out excluded repos from historical data
            pre_filter = len(existing_stats)
            existing_stats = [s for s in existing_stats if not _is_excluded_repo(s.get("url", ""))]
            if len(existing_stats) < pre_filter:
                logger.info(f"Filtered {pre_filter - len(existing_stats)} excluded repos from existing stats")
            existing_urls = {s.get("url", "").rstrip("/") for s in existing_stats}
            logger.info(f"Loaded {len(existing_stats)} existing repo stats ({len(existing_urls)} unique URLs)")

    if args.refresh:
        logger.info("--refresh: fetching stats for ALL artifacts")

    # Determine which artifacts are new (not in existing stats)
    new_results = {}
    total_artifacts = 0
    for conf_year, artifacts in all_results.items():
        new_arts = []
        for art in artifacts:
            total_artifacts += 1
            if not args.refresh:
                # Check all URL fields for this artifact
                has_existing = False
                for url_key in [
                    "repository_url",
                    "artifact_url",
                    "github_url",
                    "second_repository_url",
                    "bitbucket_url",
                ]:
                    url = art.get(url_key, "")
                    # Handle list-valued URL fields (e.g. artifact_url can be a list)
                    urls = url if isinstance(url, list) else [url] if url else []
                    for u in urls:
                        if isinstance(u, str) and u.rstrip("/") in existing_urls:
                            has_existing = True
                            break
                    if has_existing:
                        break
                if has_existing:
                    continue
            new_arts.append(art)
        if new_arts:
            new_results[conf_year] = new_arts

    new_count = sum(len(v) for v in new_results.values())
    logger.info(
        f"Total artifacts: {total_artifacts}, already have stats: {total_artifacts - new_count}, new to fetch: {new_count}"
    )

    if new_count > 0:
        logger.info(f"Collecting repository statistics for {new_count} artifacts...")
        new_stats = collect_stats_for_results(new_results)
        logger.info(f"Collected stats for {len(new_stats)} repositories")
        all_stats = existing_stats + new_stats
    else:
        logger.info("No new artifacts — reusing existing stats")
        all_stats = existing_stats

    logger.info("Aggregating statistics...")
    aggregated = aggregate_stats(all_stats)

    # Enrich top_repos with badges (from scraped results) and authors (from paper_authors_map)
    _enrich_top_repos(aggregated, all_results, Path(args.output_dir) if args.output_dir else None)

    logger.info(
        f"Overall: {aggregated['overall']['github_repos']} GitHub repos, "
        f"{aggregated['overall']['total_stars']} total stars, "
        f"{aggregated['overall']['total_forks']} total forks"
    )

    if args.output_dir:
        data_dir = Path(args.output_dir) / "_data"
        assets_dir = Path(args.output_dir) / "assets" / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        assets_dir.mkdir(parents=True, exist_ok=True)
        out_path = data_dir / "repo_stats.yml"
        yaml_data = {k: v for k, v in aggregated.items() if k != "all_github_repos"}
        # by_area will be injected below after area medians are computed

        # Write per-repo detail JSON for CDF generation
        # Sort by URL for stable ordering across runs (ThreadPoolExecutor
        # returns results in non-deterministic completion order).
        detail_repos = sorted(
            aggregated.get("all_github_repos", []),
            key=lambda e: (e.get("conference", ""), e.get("year", 0), e.get("url", "")),
        )
        build_dir = data_dir.parent / "_build"
        build_dir.mkdir(parents=True, exist_ok=True)
        detail_path = build_dir / "repo_stats_detail.json"
        save_json(detail_path, detail_repos)
        logger.info(f"Written per-repo detail ({len(aggregated.get('all_github_repos', []))} repos) to {detail_path}")

        # Write top_repos.json files — global top repos sorted by stars, split by area.
        # The per-conference top_repos (enriched with badges/authors) are collected,
        # then all repos from all_github_repos are ranked globally.
        all_repos = aggregated.get("all_github_repos", [])

        def _build_top_repos(repos, limit=50):
            """Build a sorted top-repos list from all_github_repos entries."""
            top = sorted(repos, key=lambda x: x.get("stars", 0), reverse=True)[:limit]
            result = []
            for r in top:
                url = r.get("url", "")
                # Extract github_org from URL
                org = ""
                if "github.com/" in url:
                    parts = url.split("github.com/", 1)[-1].split("/")
                    if parts:
                        org = parts[0]
                # Derive last_active from pushed_at
                pushed = r.get("pushed_at", "") or ""
                last_active = pushed[:7] if pushed else ""  # YYYY-MM
                # Get badges/authors — may have been set by _enrich_top_repos on
                # the by_conference top_repos, but all_github_repos entries won't
                # have them.  Fall back to empty.
                badges_val = r.get("badges", "")
                if isinstance(badges_val, list):
                    badges_val = ",".join(badges_val)
                authors_val = r.get("authors", "")
                if isinstance(authors_val, list):
                    authors_val = ", ".join(authors_val[:5])
                    if len(r.get("authors", [])) > 5:
                        authors_val += " et al."
                result.append(
                    {
                        "title": r.get("title", "Unknown"),
                        "url": url,
                        "year": r.get("year", 0),
                        "stars": r.get("stars", 0),
                        "forks": r.get("forks", 0),
                        "authors": authors_val,
                        "github_org": org,
                        "badges": badges_val,
                        "last_active": last_active,
                        "description": r.get("description", ""),
                        "language": r.get("language", ""),
                        "conference": r.get("conference", ""),
                        "area": r.get("area", ""),
                    }
                )
            return result

        # Enrich all_github_repos with badges/authors from the by_conference enrichment
        badge_author_lookup: dict[str, dict] = {}
        for conf in aggregated.get("by_conference", []):
            for repo in conf.get("top_repos", []):
                key = _normalize_title(repo.get("title", ""))
                if key:
                    badge_author_lookup[key] = repo
        for r in all_repos:
            key = _normalize_title(r.get("title", ""))
            if key and key in badge_author_lookup:
                enriched_repo = badge_author_lookup[key]
                if "badges" in enriched_repo and "badges" not in r:
                    r["badges"] = enriched_repo["badges"]
                if "authors" in enriched_repo and "authors" not in r:
                    r["authors"] = enriched_repo["authors"]

        all_top = _build_top_repos(all_repos)
        sys_top = _build_top_repos([r for r in all_repos if r.get("area") == "systems"])
        sec_top = _build_top_repos([r for r in all_repos if r.get("area") == "security"])

        save_json(assets_dir / "top_repos.json", all_top)
        save_json(assets_dir / "systems_top_repos.json", sys_top)
        save_json(assets_dir / "security_top_repos.json", sec_top)
        logger.info(f"Written top repos: all={len(all_top)}, systems={len(sys_top)}, security={len(sec_top)}")

        # Compute by_area stats (median, total, max) from all_github_repos
        by_area = []
        for area_name in ("systems", "security"):
            area_repos = [r for r in all_repos if r.get("area") == area_name]
            if area_repos:
                area_stars = [r.get("stars", 0) for r in area_repos]
                area_forks = [r.get("forks", 0) for r in area_repos]
                if len(area_stars) >= 2:
                    q_stars = statistics.quantiles(area_stars, n=4)
                    q_forks = statistics.quantiles(area_forks, n=4)
                    area_p25_stars = round(q_stars[0], 1)
                    area_p75_stars = round(q_stars[2], 1)
                    area_p25_forks = round(q_forks[0], 1)
                    area_p75_forks = round(q_forks[2], 1)
                else:
                    area_p25_stars = round(statistics.median(area_stars), 1)
                    area_p75_stars = area_p25_stars
                    area_p25_forks = round(statistics.median(area_forks), 1)
                    area_p75_forks = area_p25_forks
                by_area.append(
                    {
                        "name": area_name,
                        "github_repos": len(area_repos),
                        "total_stars": sum(area_stars),
                        "total_forks": sum(area_forks),
                        "median_stars": round(statistics.median(area_stars), 1),
                        "median_forks": round(statistics.median(area_forks), 1),
                        "p25_stars": area_p25_stars,
                        "p75_stars": area_p75_stars,
                        "p25_forks": area_p25_forks,
                        "p75_forks": area_p75_forks,
                        "max_stars": max(area_stars),
                    }
                )
        yaml_data["by_area"] = by_area
        save_yaml(out_path, yaml_data)
        logger.info(f"Written to {out_path}")

        # Write repo_stats_yearly.json — per-year stats split by area (all/systems/security)
        # Used by website repo_stats pages as a downloadable data file
        yearly_path = assets_dir / "repo_stats_yearly.json"
        conf_stats = aggregated.get("by_conference", [])
        # Build area lookup from artifacts_by_conference if available
        abc_path = data_dir / "artifacts_by_conference.yml"
        area_lookup = {}
        if abc_path.exists():
            abc = load_yaml(abc_path) or []
            for c in abc:
                area_lookup[c.get("name", "")] = c.get("category", "")
        yearly_by_year = defaultdict(
            lambda: {"all": defaultdict(list), "systems": defaultdict(list), "security": defaultdict(list)}
        )
        for cs in conf_stats:
            area = area_lookup.get(cs["name"], _conf_area(cs["name"]))
            for yr_data in cs.get("years", []):
                yr = yr_data["year"]
                repos = yr_data.get("github_repos", 0)
                avg_s = yr_data.get("avg_stars", 0)
                avg_f = yr_data.get("avg_forks", 0)
                for bucket in ["all", area] if area in ("systems", "security") else ["all"]:
                    yearly_by_year[yr][bucket]["repos_list"].append(repos)
                    yearly_by_year[yr][bucket]["stars_list"].append(avg_s)
                    yearly_by_year[yr][bucket]["forks_list"].append(avg_f)
        yearly_json = []
        for yr in sorted(yearly_by_year.keys()):
            entry = {"year": yr}
            for bucket in ("all", "systems", "security"):
                rl = yearly_by_year[yr][bucket]["repos_list"]
                sl = yearly_by_year[yr][bucket]["stars_list"]
                fl = yearly_by_year[yr][bucket]["forks_list"]
                if rl:
                    total_repos = sum(rl)
                    total_stars = sum(r * s for r, s in zip(rl, sl))
                    total_forks = sum(r * f for r, f in zip(rl, fl))
                    entry[bucket] = {
                        "github_repos": total_repos,
                        "avg_stars": round(total_stars / total_repos, 1) if total_repos else 0,
                        "avg_forks": round(total_forks / total_repos, 1) if total_repos else 0,
                        "min_stars": round(min(sl), 1),
                        "max_stars": round(max(sl), 1),
                        "min_forks": round(min(fl), 1),
                        "max_forks": round(max(fl), 1),
                    }
            yearly_json.append(entry)
        save_json(yearly_path, yearly_json)
        logger.info(f"Written yearly stats ({len(yearly_json)} years) to {yearly_path}")

        # ---- Historical time-series tracking ----
        # Append a dated snapshot for each fetched artifact so we can track
        # stars/forks/views/downloads over time across monthly runs.
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        history_load_path = resolve_data_path(Path(args.output_dir), "repo_stats_history.json")
        # Always write to _build/
        history_write_path = build_dir / "repo_stats_history.json"

        # Load existing history
        history = {}
        if history_load_path.exists():
            history = load_json(history_load_path)
            logger.info(f"Loaded history for {len(history)} URLs")

        # Build snapshots from the raw all_stats (which have full metric detail)
        updated = 0
        for s in all_stats:
            url = s.get("url", "").rstrip("/")
            if not url:
                continue

            source = s.get("source", "")
            if not source:
                url_lower = url.lower()
                if "github" in url_lower:
                    source = "github"
                elif "zenodo" in url_lower:
                    source = "zenodo"
                elif "figshare" in url_lower:
                    source = "figshare"
                else:
                    source = "unknown"

            # Build the snapshot — only time-varying metrics
            snapshot = {"date": today}
            if source == "github":
                snapshot["stars"] = s.get("github_stars", s.get("stars", 0)) or 0
                snapshot["forks"] = s.get("github_forks", s.get("forks", 0)) or 0
            elif source in ("zenodo", "figshare"):
                snapshot["views"] = s.get("zenodo_views", s.get("views", 0)) or 0
                snapshot["downloads"] = s.get("zenodo_downloads", s.get("downloads", 0)) or 0

            if url not in history:
                history[url] = {
                    "meta": {
                        "conference": s.get("conference", ""),
                        "year": s.get("year", 0),
                        "area": _conf_area(s.get("conference", "")),
                        "title": s.get("title", ""),
                        "source": source,
                    },
                    "snapshots": [],
                }

            snapshots = history[url]["snapshots"]
            # Replace if we already have a snapshot for today, otherwise append
            if snapshots and snapshots[-1].get("date") == today:
                snapshots[-1] = snapshot
            else:
                snapshots.append(snapshot)
            updated += 1

        save_json(history_write_path, history)
        logger.info(f"Written history ({len(history)} URLs, {updated} snapshots updated) to {history_write_path}")

    return aggregated


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()

    main()
