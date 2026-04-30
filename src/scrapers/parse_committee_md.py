import argparse
import logging
import re

from .repo_utils import download_file, get_conferences_from_prefix, github_urls

logger = logging.getLogger(__name__)


def _parse_member_line(line):
    """Parse a single line from a committee markdown file into name + affiliation.

    Returns (name, affiliation) or (None, None) if the line should be skipped.
    """
    line = line.strip()
    if not line:
        return None, None
    # Skip markdown headings and separators
    if line.startswith(("#", "---")):
        return None, None
    # Skip contact / email lines that are not actual names
    if "reach the" in line.lower() or "contact" in line.lower() and "@" in line:
        return None, None
    # Skip descriptive/footnote lines that are not member names
    if line.lstrip("*- ").startswith(("¹", "²", "³", "⁴", "⁵")):
        return None, None
    lower = line.lower()
    if "award" in lower or "distinguished" in lower or "participated in" in lower:
        return None, None
    # Strip list markers
    start = 0
    if line.startswith(("-", "*")):
        start = 1
        if len(line) > 1 and line[1] == " ":
            start = 2
    line = line[start:].strip()
    if not line:
        return None, None
    # Strip markdown link syntax: [name](url) -> name
    link_match = re.match(r"\[([^\]]+)\]\([^)]*\)", line)
    if link_match:
        line = link_match.group(1) + line[link_match.end() :]
    # Strip trailing <br> or <br/> tags
    line = re.sub(r"\s*<br\s*/?>$", "", line).strip()
    if not line:
        return None, None
    # Strip bold/italic markers
    line = line.strip("*_").strip()
    # Strip trailing footnote markers (e.g., "Cen Zhang (Georgia Tech)¹")
    line = re.sub(r"[¹²³⁴⁵⁶⁷⁸⁹⁰]+$", "", line).strip()

    # Handle "Name, Details (Affiliation)" pattern first
    # Example: "Salvatore Signorello, INESC-ID/IST (University of Lisbon)"
    if "(" in line and ")" in line:
        # Extract affiliation from parentheses
        affiliation = line[line.find("(") + 1 : line.find(")")].strip()
        # Get name part (everything before parentheses)
        name_part = line[: line.find("(")].strip()
        # If there's a comma in the name part, the real name is before the first comma
        if "," in name_part:
            name = name_part.split(",")[0].strip()
        else:
            name = name_part
    elif "," in line:
        name = line.split(",")[0].strip()
        affiliation = line.split(",", 1)[1].strip()
    else:
        name = line
        affiliation = ""

    # Strip residual markdown bold/italic markers from name and affiliation
    name = name.strip("*_").strip()
    affiliation = affiliation.strip("*_").strip()

    # Skip placeholder entries
    if name.lower() in ("you?", "you", "tba", "tbd", ""):
        return None, None
    if len(name) <= 1:
        return None, None

    return name, affiliation


def get_committee_for_conference(conference: str, prefix: str) -> dict | None:
    base_url = github_urls[prefix]["raw_base_url"] + conference
    # committee files are either named committee.md or organizers.md
    response = download_file(base_url + "/committee.md")
    if response is None:
        response = download_file(base_url + "/organizers.md")
    if response is None:
        logger.info(f"couldn't get committee for {conference}")
        return None

    # --- Parse chairs and members separately ---
    # Look for chair sections before the AEC section
    committee = []
    lines = response.splitlines()

    # Identify section boundaries
    chair_lines = []
    member_lines = []
    current_section = None  # None, 'chair', 'member'

    for line in lines:
        stripped = line.strip().lower()
        # Detect chair heading
        if (
            ("chair" in stripped and "artifact" in stripped)
            or (stripped.startswith("**chair") and stripped.endswith("**:"))
            or re.match(r"^#{1,4}\s*.*chair", stripped)
            or (stripped.startswith("**chair"))
        ):
            current_section = "chair"
            continue
        # Detect member/committee heading
        if (
            re.match(r"^#{1,4}\s*.*artifact\s*evaluation\s*committee", stripped)
            or re.match(r"^#{1,4}\s*.*\bmembers?\b", stripped)
            or (stripped.startswith("**member") and stripped.endswith("**:"))
        ):
            current_section = "member"
            continue
        # Any other heading (e.g. "Best Reproducibility Advancement Award")
        # ends the current section to avoid bleeding unrelated members in.
        if re.match(r"^#{1,4}\s", stripped):
            current_section = None
            continue
        if current_section == "chair":
            chair_lines.append(line)
        elif current_section == "member":
            member_lines.append(line)

    # If no sections detected, fall back to original split approach
    if not chair_lines and not member_lines:
        committees_text = response.split("Artifact Evaluation Committee")
        aec = committees_text[len(committees_text) - 1].strip()
        for line in aec.splitlines():
            name, affiliation = _parse_member_line(line)
            if name:
                committee.append({"name": name, "affiliation": affiliation, "role": "member"})
        return committee

    # Parse chairs
    for line in chair_lines:
        name, affiliation = _parse_member_line(line)
        if name:
            committee.append({"name": name, "affiliation": affiliation, "role": "chair"})

    # Parse members
    for line in member_lines:
        name, affiliation = _parse_member_line(line)
        if name:
            committee.append({"name": name, "affiliation": affiliation, "role": "member"})

    # If only chairs found (no member section), also parse the AEC section
    if not member_lines:
        committees_text = response.split("Artifact Evaluation Committee")
        if len(committees_text) > 1:
            aec = committees_text[-1].strip()
            for line in aec.splitlines():
                name, affiliation = _parse_member_line(line)
                if name and not any(m["name"] == name for m in committee):
                    committee.append({"name": name, "affiliation": affiliation, "role": "member"})

    return committee


def get_committees(conference_regex: str, prefix: str) -> dict[str, dict]:
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
            if name in results:
                continue
            # add year
            committee = get_committee_for_conference(name, prefix)
            if committee:
                results[name] = committee

    return results


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
    parser.add_argument("--print", action="store_true", help="Print committees")

    args = parser.parse_args()

    results = get_committees(args.conf_regex, args.prefix)

    for year in results:
        logger.info(f"{year}: {len(results[year])}")

    if args.print:
        for year in results:
            logger.info(results[year])


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()

    main()
