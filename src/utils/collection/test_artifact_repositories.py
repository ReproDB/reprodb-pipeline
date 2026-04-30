import argparse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.scrapers.parse_results_md import get_ae_results
from src.scrapers.repo_utils import check_url_cached

logger = logging.getLogger(__name__)
MAX_URL_WORKERS = 16  # parallel URL checks


def check_url_existence(url):
    """Check URL existence using the shared cached checker."""
    return check_url_cached(url)


def _normalise_url(val):
    """Normalise a raw URL value from artifact data."""
    if isinstance(val, list):
        val = val[0] if val else ""
    if not isinstance(val, str) or not val:
        return None
    if val.startswith("10."):
        val = "https://doi.org/" + val
    # Convert git@github.com:user/repo.git to HTTPS
    if val.startswith("git@github.com:"):
        path = val[len("git@github.com:") :]
        if path.endswith(".git"):
            path = path[:-4]
        val = "https://github.com/" + path
    return val


def check_artifact_exists(results, url_keys):

    counts: dict[str, dict[str, dict[str, int]]] = {}
    failed = []

    for url_key in url_keys:
        counts[url_key] = {}
        # Build a flat list of (name, artifact_index, url) to check in parallel
        jobs = []
        for name, artifacts in results.items():
            counts[url_key][name] = {"exists": 0, "total": 0}
            for idx, artifact in enumerate(artifacts):
                if url_key in artifact:
                    val = _normalise_url(artifact[url_key])
                    if val is None:
                        continue
                    artifact[url_key] = val
                    jobs.append((name, idx, val))
                    counts[url_key][name]["total"] += 1
                else:
                    counts[url_key][name]["total"] += 1

        logger.info(
            f"testing {len(jobs)} artifact urls for {url_key} ({len(results)} conferences, {MAX_URL_WORKERS} workers)"
        )

        # Check all URLs in parallel
        url_results = {}  # url -> bool
        with ThreadPoolExecutor(max_workers=MAX_URL_WORKERS) as pool:
            future_map = {pool.submit(check_url_existence, url): url for _, _, url in jobs}
            for future in as_completed(future_map):
                url = future_map[future]
                try:
                    url_results[url] = future.result()
                except Exception:
                    url_results[url] = False

        # Apply results back to artifacts
        for name, idx, url in jobs:
            exists = url_results.get(url, False)
            results[name][idx][url_key + "_exists"] = exists
            if exists:
                counts[url_key][name]["exists"] += 1
            else:
                failed.append(url)

    return results, counts, failed


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
    parser.add_argument("--print_failed", action="store_true", help="Print failed website checks")
    parser.add_argument(
        "--url_keys",
        type=str,
        nargs="+",
        default=["repository_url"],
        help="Keys in the artifact dictionary to check the URLs for",
    )

    args = parser.parse_args()
    results = get_ae_results(args.conf_regex, args.prefix)

    _, counts, failed = check_artifact_exists(results, args.url_keys)

    logger.info("url_key, name, total, exists, failed, percentage")
    for url_key, key_counts in counts.items():
        for name, count in key_counts.items():
            percentage = (count["exists"] / count["total"]) * 100 if count["total"] > 0 else 0
            logger.info(
                f"{url_key}, {name}, {count['total']}, {count['exists']}, {count['total'] - count['exists']}, {percentage:.2f}%"
            )

    if args.print_failed:
        logger.info("Failed:")
        for f in failed:
            logger.info(f)


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()

    main()
