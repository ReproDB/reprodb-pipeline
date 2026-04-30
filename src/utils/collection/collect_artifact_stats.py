"""Thin wrappers around cached repository-stats functions.

Provides a unified API for fetching GitHub/Zenodo/Figshare stats and
attaching them to artifact dictionaries in-place.  All HTTP work is
delegated to the cached helpers in ``scrapers.repo_utils``.
"""

import argparse
import logging

from src.scrapers.parse_results_md import get_ae_results
from src.scrapers.repo_utils import cached_figshare_stats, cached_github_stats, cached_zenodo_stats

from .test_artifact_repositories import check_artifact_exists

logger = logging.getLogger(__name__)


# Keep thin wrappers so existing callers still work
def github_stats(url):
    return cached_github_stats(url)


def zenodo_stats(url):
    return cached_zenodo_stats(url)


def figshare_stats(url):
    return cached_figshare_stats(url)


def get_all_artifact_stats(results, url_keys):
    """Fetch repository stats and attach them to each artifact entry in-place.

    For each artifact with a valid URL (GitHub/Zenodo/Figshare), fetches
    metadata and merges it into ``artifact['stats']``.
    """
    for name, artifacts in results.items():
        for url_key in url_keys:
            logger.info(f"Getting stats for {len(artifacts)}")
            for artifact in artifacts:
                url = artifact.get(url_key, "")
                if not url or not artifact.get(url_key + "_exists"):
                    logger.info(f"{url_key} does not exist for {artifact.get('title', '?')} at {name}")
                    continue

                if "zenodo" in url:
                    stats = zenodo_stats(url)
                elif "figshare" in url:
                    stats = figshare_stats(url)
                elif "github" in url:
                    stats = github_stats(url)
                else:
                    logger.info(f"No stats for {url} at {name} titled {artifact.get('title', '?')}")
                    continue

                if stats:
                    artifact["stats"] = {**stats, **artifact.get("stats", {})}

    return results


def main():

    parser = argparse.ArgumentParser(description="Scraping results of sys/secartifacts.github.io from conferences.")
    parser.add_argument(
        "--conf_regex", type=str, default=".20[1|2][0-9]", help="Regular expression for conference name and or names"
    )
    parser.add_argument(
        "--prefix",
        type=str,
        default="sys",
        help="Prefix of artifacts website like sys for sysartifacts or sec for secartifacts",
    )
    parser.add_argument(
        "--url_keys",
        type=str,
        nargs="+",
        default=["repository_url"],
        help="Keys in the artifact dictionary to check the URLs for",
    )

    args = parser.parse_args()
    results = get_ae_results(args.conf_regex, args.prefix)
    results, _, _ = check_artifact_exists(results, args.url_keys)

    results = get_all_artifact_stats(results, args.url_keys)

    artifact_id = 0
    for name, artifacts in results.items():
        for artifact in artifacts:
            if "stats" not in artifact:
                continue

            for key, value in artifact["stats"].items():
                logger.info(f"{name},{artifact_id},{key},{value}")

            artifact_id += 1


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()

    main()
