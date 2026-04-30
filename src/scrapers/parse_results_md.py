"""Parse artifact evaluation results from sysartifacts/secartifacts GitHub repos.

Downloads and parses ``results.md`` (or ``result.md``) files that contain
YAML front-matter with artifact metadata (titles, badges, URLs).  Also
handles the HTML-table variant used by some conferences (OSDI, ATC, etc.)
where badges are encoded as ``<span>`` tags inside markdown tables.

Public API:
    get_ae_results(conf_regex, prefix) -> dict[str, list[dict]]
"""

import argparse
import logging
import re

import requests
import yaml
from bs4 import BeautifulSoup

from .repo_utils import download_file, get_conferences_from_prefix, github_urls

logger = logging.getLogger(__name__)
# Alternative results file names used by different conferences
RESULTS_FILENAMES = ["results.md", "result.md"]


def parse_html_results(content):
    """
    Parse HTML-table-based results pages (used by OSDI, ATC, etc.).
    These pages use <span> tags with ids to indicate badges:
      span#aa = Available, span#af = Functional, span#rr = Reproduced
    And markdown tables with rows like:
      | [Title](url) | <span id="aa">AVAILABLE</span>... | [Github](url) |
    """
    artifacts = []

    # Use BeautifulSoup to parse the HTML fragments within the markdown
    soup = BeautifulSoup(content, "html.parser")

    # Find all table rows
    rows = soup.find_all("tr")
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue

        # Extract title from first cell
        title_cell = cells[0]
        title_link = title_cell.find("a")
        title = title_link.get_text(strip=True) if title_link else title_cell.get_text(strip=True)
        if not title or title.lower() in ("paper title", ""):
            continue

        paper_url = ""
        if title_link and title_link.get("href"):
            paper_url = title_link["href"]

        # Extract badges from second cell (span tags)
        badge_cell = cells[1] if len(cells) > 1 else None
        badges = []
        if badge_cell:
            spans = badge_cell.find_all("span")
            for span in spans:
                span_id = span.get("id", "")
                span_text = span.get_text(strip=True).lower()
                if span_id == "aa" or "available" in span_text:
                    badges.append("available")
                elif span_id == "af" or "functional" in span_text:
                    badges.append("functional")
                elif span_id == "rr" or "reproduc" in span_text or "replicated" in span_text:
                    badges.append("reproduced")

        # Extract repository URL from third cell
        repo_url = ""
        artifact_url = ""
        if len(cells) > 2:
            url_cell = cells[2]
            links = url_cell.find_all("a")
            for link in links:
                href = link.get("href", "")
                link_text = link.get_text(strip=True).lower()
                if (
                    "github" in link_text
                    or "github.com" in href
                    or "gitlab" in link_text
                    or "gitlab" in href
                    or "bitbucket" in link_text
                ):
                    repo_url = href
                elif "zenodo" in link_text or "zenodo.org" in href or "figshare" in link_text or "doi.org" in href:
                    artifact_url = href
                elif not repo_url:
                    repo_url = href

        if title and (badges or repo_url or artifact_url):
            artifact = {
                "title": title,
                "badges": ",".join(badges) if badges else "",
            }
            if paper_url:
                artifact["paper_url"] = paper_url
            if repo_url:
                artifact["repository_url"] = repo_url
            if artifact_url:
                artifact["artifact_url"] = artifact_url
            artifacts.append(artifact)

    return artifacts


def parse_markdown_table_results(content):
    """
    Fallback parser for markdown tables that weren't converted to HTML.
    Parses raw markdown rows like:
    | [Title](url) | <span id="aa">AVAILABLE</span>... | [Github](url) |
    """
    artifacts = []

    # Find table rows in markdown
    lines = content.split("\n")
    for line in lines:
        line = line.strip()
        if not line.startswith("|") or ":-" in line:
            continue

        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) < 2:
            continue

        # Extract title
        title_match = re.search(r"\[([^\]]+)\]", cells[0])
        if not title_match:
            continue
        title = title_match.group(1).strip()
        if title.lower() in ("paper title", ""):
            continue

        # Extract badges from spans
        badges = []
        badge_cell = cells[1] if len(cells) > 1 else ""
        if 'id="aa"' in badge_cell or ">AVAILABLE<" in badge_cell:
            badges.append("available")
        if 'id="af"' in badge_cell or ">FUNCTIONAL<" in badge_cell:
            badges.append("functional")
        if 'id="rr"' in badge_cell or ">REPRODUCED<" in badge_cell or ">REPLICATED<" in badge_cell:
            badges.append("reproduced")

        # Extract URLs from third cell
        repo_url = ""
        artifact_url = ""
        if len(cells) > 2:
            url_matches = re.findall(r"\[([^\]]*)\]\(([^)]+)\)", cells[2])
            for link_text, href in url_matches:
                lt = link_text.lower()
                if "github" in lt or "gitlab" in lt or "bitbucket" in lt:
                    repo_url = href
                elif "zenodo" in lt or "figshare" in lt or "doi" in lt:
                    artifact_url = href
                elif not repo_url:
                    repo_url = href
            # Also check for bare URLs
            if not repo_url:
                bare_urls = re.findall(r"(https?://github\.com/[^\s<|]+)", cells[2])
                if bare_urls:
                    repo_url = bare_urls[0]

        if title and (badges or repo_url or artifact_url):
            artifact = {
                "title": title,
                "badges": ",".join(badges) if badges else "",
            }
            if repo_url:
                artifact["repository_url"] = repo_url
            if artifact_url:
                artifact["artifact_url"] = artifact_url
            artifacts.append(artifact)

    return artifacts


def get_ae_results(conference_regex, prefix):
    results = {}
    # get conference name from prefix
    conferences = get_conferences_from_prefix(prefix)
    if conferences is None:
        logger.info(f"Invalid prefix: {prefix}")
        return results
    # get the base url for the conference
    for conf in conferences:
        if re.search(conference_regex, conf["name"]):
            name = conf["name"]

            # Try multiple results filenames (results.md, result.md)
            downloaded = False
            for filename in RESULTS_FILENAMES:
                file_url = github_urls[prefix]["raw_base_url"] + name + "/" + filename
                try:
                    content = download_file(file_url)
                    if content is not None:
                        results[name] = content
                        logger.info(f"got {name}/{filename}")
                        downloaded = True
                        break
                    logger.debug("download_file returned None for %s/%s", name, filename)
                except requests.exceptions.HTTPError:
                    logger.debug("HTTPError downloading %s, trying next source", filename if filename else "file")
                    continue
                except requests.exceptions.ConnectionError as e:
                    logger.error(f"  connection error for {name}/{filename}: {e}")
                    continue

            if not downloaded:
                logger.info(f"couldn't get results for {name} (tried {', '.join(RESULTS_FILENAMES)})")

    parsed_results = {}

    for conf_year, content in results.items():
        # First, try YAML frontmatter parsing (EuroSys, SOSP, SC, secartifacts)
        # Use regex to split only on '---' at the start of a line (YAML document
        # separators), not '---' embedded inside quoted strings/URLs.
        parts = re.split(r"^---\s*$", content, maxsplit=2, flags=re.MULTILINE)
        if len(parts) >= 2:
            yaml_part = parts[1].replace("\t", "  ")  # Some conferences use tabs
            try:
                parsed_content = yaml.safe_load(yaml_part)
                if parsed_content and "artifacts" in parsed_content:
                    parsed_results[conf_year] = parsed_content["artifacts"]
                    logger.info(f"  {conf_year}: parsed {len(parsed_content['artifacts'])} artifacts (YAML)")
                    continue
                if parsed_content and "issues" in parsed_content:
                    # PETS-style: artifacts nested under issues list
                    all_artifacts = []
                    for issue in parsed_content["issues"]:
                        all_artifacts.extend(issue.get("artifacts", []))
                    if all_artifacts:
                        parsed_results[conf_year] = all_artifacts
                        logger.info(f"  {conf_year}: parsed {len(all_artifacts)} artifacts (YAML/issues)")
                        continue
            except yaml.YAMLError as e:
                logger.error(f"  YAML parse error for {conf_year}: {e}")

        # Fallback: try HTML table parsing (OSDI, ATC)
        artifacts = parse_html_results(content)
        if not artifacts:
            # Try raw markdown table parsing
            artifacts = parse_markdown_table_results(content)

        if artifacts:
            parsed_results[conf_year] = artifacts
            logger.info(f"  {conf_year}: parsed {len(artifacts)} artifacts (HTML table)")
        else:
            logger.info(f"  {conf_year}: no artifacts found")

    return parsed_results


def main():

    parser = argparse.ArgumentParser(description="Scraping results of sys/secartifacts.github.io from conferences.")
    parser.add_argument(
        "--conf_regex", type=str, default=".20[1|2][0-9]", help="Regular expression for conference name and or years"
    )
    parser.add_argument(
        "--prefix",
        type=str,
        default="sys",
        help="Prefix of artifacts website like sys for sysartifacts or sec for secartifacts",
    )

    args = parser.parse_args()

    results = get_ae_results(args.conf_regex, args.prefix)
    for year in results:
        logger.info(f"{year}: {len(results[year])}")
        logger.info(results[year])


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()

    main()
