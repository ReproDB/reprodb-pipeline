#!/usr/bin/env python3
"""
Generate artifact-evaluation results.md files for sysartifacts and secartifacts sites.

This is the unified entry point for generating results pages for both:
  - sysartifacts.github.io  (USENIX systems conferences: FAST, OSDI, ATC, NSDI)
  - secartifacts.github.io  (security conferences: USENIX Security, ACSAC)

The --target flag selects the output format (badge images, Liquid template).
The --conference flag selects which scraper to use.

Usage:
  # sysartifacts: FAST 2025
  python -m src.scrapers.generate_results --target sysartifacts --conference fast --years 2025

  # sysartifacts: OSDI 2024 with custom dir prefix
  python -m src.scrapers.generate_results --target sysartifacts --conference osdi --years 2024 --dir-prefix osdi

  # secartifacts: ACSAC 2025
  python -m src.scrapers.generate_results --target secartifacts --conference acsac --years 2025

  # secartifacts: USENIX Security 2025
  python -m src.scrapers.generate_results --target secartifacts --conference usenixsec --years 2025

  # Preview without writing
  python -m src.scrapers.generate_results --target secartifacts --conference acsac --years 2025 --dry-run

Requirements:
  pip install requests beautifulsoup4 pyyaml lxml

Output: <output_dir>/<dir_prefix><YYYY>/results.md (and organizers.md when available)
"""

import argparse
import logging
import os

import yaml

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Target site configuration (output format)
# ═══════════════════════════════════════════════════════════════════════════

# Each target+conference pair defines badge images, names, column order,
# YAML front-matter order, and the Liquid template.


def _sysartifacts_usenix_config(conference):
    """Build a sysartifacts config for a USENIX systems conference.

    All sysartifacts USENIX conferences share the same badge images, labels,
    front-matter, and template — only the dir_prefix (== conference name) and
    the USENIX scraper key differ.
    """
    return {
        "dir_prefix": conference,
        "badge_columns": ["available", "functional", "reproduced"],
        "badge_labels": {
            "available": "Artifacts Available",
            "functional": "Artifacts Functional",
            "reproduced": "Results Reproduced",
        },
        "front_matter": {
            "order": 50,
            "available_img": "usenix_available.svg",
            "available_name": "Artifacts Available",
            "functional_img": "usenix_functional.svg",
            "functional_name": "Artifacts Evaluated - Functional",
            "reproduced_img": "usenix_reproduced.svg",
            "reproduced_name": "Results Reproduced",
        },
        "badges_format": "csv_lower",  # "available,functional"
        "template": "sysartifacts_usenix",
        "scraper": conference,  # passed to usenix_scrape → URL suffix
        "scrape_organizers": True,
    }


TARGET_CONFERENCE_CONFIG = {
    # ── sysartifacts (all USENIX systems conferences share the same format) ──
    ("sysartifacts", "fast"): _sysartifacts_usenix_config("fast"),
    ("sysartifacts", "osdi"): _sysartifacts_usenix_config("osdi"),
    ("sysartifacts", "atc"): _sysartifacts_usenix_config("atc"),
    ("sysartifacts", "nsdi"): _sysartifacts_usenix_config("nsdi"),
    # ── secartifacts ──────────────────────────────────────────────────────
    ("secartifacts", "usenixsec"): {
        "dir_prefix": "usenixsec",
        "badge_columns": ["available", "functional", "reproduced"],
        "badge_labels": {
            "available": "Artifacts Available",
            "functional": "Artifacts Evaluated - Functional",
            "reproduced": "Results Reproduced",
        },
        "front_matter": {
            "order": 70,
            "available_img": "usenixbadges-available.svg",
            "available_name": "Artifacts Available (v1.1)",
            "functional_img": "usenixbadges-functional.svg",
            "functional_name": "Artifacts Evaluated - Functional (v1.1)",
            "reproduced_img": "usenixbadges-reproduced.svg",
            "reproduced_name": "Results Reproduced (v1.1)",
        },
        "badges_format": "secartifacts_usenix",  # "Badges: Available, Functional"
        "template": "secartifacts_usenix",
        "scraper": "usenixsecurity",
        "scrape_organizers": False,
        "include_empty_artifact_fields": True,
    },
    ("secartifacts", "acsac"): {
        "dir_prefix": "acsac",
        "badge_columns": ["available", "reviewed", "reproducible"],
        "badge_labels": {
            "available": "Code Available",
            "reviewed": "Code Reviewed",
            "reproducible": "Code Reproducible",
        },
        "front_matter": {
            "order": 20,
            "available_img": "ieeexplore_code_available.png",
            "available_name": "Code Available",
            "reviewed_img": "ieeexplore_code_reviewed.png",
            "reviewed_name": "Code Reviewed",
            "reproducible_img": "ieeexplore_code_reproducible.png",
            "reproducible_name": "Code Reproducible",
        },
        "badges_format": "single_lower",  # "available"
        "template": "secartifacts_acsac",
        "scraper": "acsac",
        "scrape_organizers": False,
    },
}


def _available_conferences(target=None):
    """Return sorted list of conference names, optionally filtered by target."""
    confs = set()
    for t, c in TARGET_CONFERENCE_CONFIG:
        if target is None or t == target:
            confs.add(c)
    return sorted(confs)


# ═══════════════════════════════════════════════════════════════════════════
# Scraper dispatch
# ═══════════════════════════════════════════════════════════════════════════


def scrape_artifacts(config, year, **kwargs):
    """
    Dispatch to the appropriate scraper.

    Returns list of dicts with: title, badges (list[str]), artifact_urls (list[str]), paper_url (str).
    """
    scraper = config["scraper"]

    if scraper == "acsac":
        from .acsac_scrape import scrape_acsac_artifacts

        return scrape_acsac_artifacts(year)

    # All USENIX conferences (fast, osdi, atc, nsdi, usenixsecurity)
    from .usenix_scrape import scrape_conference_year

    max_workers = kwargs.get("max_workers", 4)
    delay = kwargs.get("delay", 0.5)
    all_papers = scrape_conference_year(scraper, year, max_workers=max_workers, delay=delay)

    # Normalize to common format, keep only papers with badges
    artifacts = []
    for p in all_papers:
        if not p.get("badges"):
            continue
        artifacts.append(
            {
                "title": p["title"],
                "badges": p["badges"],
                "artifact_urls": [],
                "paper_url": p.get("paper_url", ""),
            }
        )
    return artifacts


def scrape_organizers_for(config, year):
    """Scrape AE committee organizers if supported for this config."""
    if not config.get("scrape_organizers"):
        return None
    from .usenix_scrape import scrape_organizers

    return scrape_organizers(config["scraper"], year)


# ═══════════════════════════════════════════════════════════════════════════
# Results.md generation
# ═══════════════════════════════════════════════════════════════════════════


def _format_badges(badges, fmt):
    """Format a badge list according to the target convention."""
    if fmt == "csv_lower":
        # sysartifacts: "available,functional,reproduced"
        return ",".join(badges)
    if fmt == "secartifacts_usenix":
        # secartifacts USENIX: "Badges: Available, Functional, Reproduced"
        return "Badges: " + ", ".join(b.capitalize() for b in badges)
    if fmt == "single_lower":
        # ACSAC: just the single badge name
        return badges[0] if badges else ""
    return ",".join(badges)


def generate_results_md(config, year, artifacts):
    """Generate a results.md string from config + scraped artifacts."""
    badge_cols = config["badge_columns"]
    badge_labels = config["badge_labels"]
    badges_fmt = config["badges_format"]
    include_empty = config.get("include_empty_artifact_fields", False)

    # Count badges
    counts = {b: sum(1 for a in artifacts if b in a["badges"]) for b in badge_cols}

    # Build artifact YAML entries
    yaml_artifacts = []
    for a in artifacts:
        entry = {"title": a["title"]}
        entry["badges"] = _format_badges(a["badges"], badges_fmt)

        # Artifact URLs
        if a.get("artifact_urls"):
            if len(a["artifact_urls"]) == 1:
                entry["artifact_url"] = a["artifact_urls"][0]
            else:
                entry["artifact_urls"] = a["artifact_urls"]
        elif include_empty:
            entry["artifact_url"] = ""

        if a.get("paper_url"):
            entry["paper_url"] = a["paper_url"]

        if include_empty:
            entry["appendix_url"] = ""

        yaml_artifacts.append(entry)

    # Front matter
    front = {"title": "Results"}
    front.update(config["front_matter"])
    front["artifacts"] = yaml_artifacts

    front_yaml = yaml.dump(front, default_flow_style=False, allow_unicode=True, sort_keys=False, width=120)

    # Summary
    summary = "\n".join(f"* {counts[b]} {badge_labels[b]}" for b in badge_cols)

    # Template
    template_name = config["template"]
    table = _render_template(template_name, year, badge_cols)

    return f"---\n{front_yaml.rstrip()}\n---\n\n**Evaluation Results**:\n\n{summary}\n\n{table}\n"


def generate_organizers_md(organizers):
    """Generate organizers.md from scraped organizer data, or None."""
    if not organizers or (not organizers.get("chairs") and not organizers.get("members")):
        return None

    lines = ["---", "title: Organizers", "order: 20", "---", "", "## Artifact Evaluation Committee Co-Chairs", ""]

    for chair in organizers.get("chairs", []):
        aff = f", {chair['affiliation']}" if chair["affiliation"] else ""
        lines.append(f"{chair['name']}{aff} <br>")

    lines.extend(["", "## Artifact Evaluation Committee", ""])

    members = organizers.get("members", [])
    for i, member in enumerate(members):
        aff = f", {member['affiliation']}" if member["affiliation"] else ""
        suffix = "<br>" if i < len(members) - 1 else ""
        lines.append(f"{member['name']}{aff}{suffix}")

    return "\n".join(lines) + "\n"


# ═══════════════════════════════════════════════════════════════════════════
# Liquid templates
# ═══════════════════════════════════════════════════════════════════════════


def _render_template(name, year, badge_cols):
    templates = {
        "sysartifacts_usenix": _tmpl_sysartifacts_usenix,
        "secartifacts_usenix": _tmpl_secartifacts_usenix,
        "secartifacts_acsac": _tmpl_secartifacts_acsac,
    }
    fn = templates.get(name)
    if fn is None:
        raise ValueError(f"Unknown template: {name}")
    return fn(year=year, badge_cols=badge_cols)


def _tmpl_sysartifacts_usenix(**_kwargs):
    return """\
<table>
  <thead>
    <tr>
      <th>Paper title</th>
      <th>Avail.</th>
      <th>Funct.</th>
      <th>Repro.</th>
    </tr>
  </thead>
  <tbody>
  {% for artifact in page.artifacts %}
    <tr>
      <td>
        {% if artifact.paper_url %}
          <a href="{{artifact.paper_url}}" target="_blank">{{artifact.title}}</a>
        {% else %}
          {{ artifact.title }}
        {% endif %}
      </td>
      <td>
        {% if artifact.badges contains "available" %}
          <img src="{{ site.baseurl }}/images/{{ page.available_img }}" alt="{{ page.available_name }}" width="50px">
        {% endif %}
      </td>
      <td>
        {% if artifact.badges contains "functional" %}
          <img src="{{ site.baseurl }}/images/{{ page.functional_img }}" alt="{{ page.functional_name }}" width="50px">
        {% endif %}
      </td>
      <td>
        {% if artifact.badges contains "reproduced" %}
          <img src="{{ site.baseurl }}/images/{{ page.reproduced_img }}" alt="{{ page.reproduced_name }}" width="50px">
        {% endif %}
      </td>
    </tr>
  {% endfor %}
  </tbody>
</table>"""


def _tmpl_secartifacts_usenix(**_kwargs):
    return """\
<table>
  <thead>
    <tr>
      <th>Paper</th>
      <th width="75px">Avail.</th>
      <th width="75px">Funct.</th>
      <th width="75px">Repro.</th>
      <th>Available At</th>
    </tr>
  </thead>
  <tbody>
  {% assign sorted_artifacts = page.artifacts | sort: "title" %}
  {% for artifact in sorted_artifacts %}
    <tr>
      <td>
        {% if artifact.paper_url %}
          <a href="{{artifact.paper_url}}" target="_blank">{{artifact.title}}</a>
        {% else %}
          {{ artifact.title }}
        {% endif %}
        {% if artifact.award %}
          <br> <b>🏆 {{ artifact.award }}</b><br>
        {% endif %}
      </td>
      <td width="75px">
        {% if artifact.badges contains "Available" %}
          <img src="{{ site.baseurl }}/images/{{ page.available_img }}" alt="{{ page.available_name }}">
        {% endif %}
      </td>
      <td width="75px">
        {% if artifact.badges contains "Functional" %}
          <img src="{{ site.baseurl }}/images/{{ page.functional_img }}" alt="{{ page.functional_name }}">
        {% endif %}
      </td>
      <td width="75px">
        {% if artifact.badges contains "Reproduced" %}
          <img src="{{ site.baseurl }}/images/{{ page.reproduced_img }}" alt="{{ page.reproduced_name }}">
        {% endif %}
      </td>
      <td width="120px">
        {% if artifact.artifact_url %}
          📦 <a href="{{artifact.artifact_url}}" target="_blank">Artifact</a><br>
        {% endif %} {% if artifact.artifact_urls %}
          📦 Artifacts: <br>&nbsp; &nbsp; &nbsp;
            {% for artifact_url in artifact.artifact_urls %}
              <a href="{{artifact_url}}" target="_blank">{{ forloop.index }}</a>{% unless forloop.last %}, {% endunless %}
            {% endfor %} <br>
        {% endif %} {% if artifact.repository_url %}
          🗂️ <a href="{{artifact.repository_url}}" target="_blank">Repository</a><br>
        {% endif %} {% if artifact.appendix_url %}
          📄 <a href="{{artifact.appendix_url}}" target="_blank">Appendix</a><br>
        {% endif %}
      </td>
    </tr>
  {% endfor %}
  </tbody>
</table>"""


def _tmpl_secartifacts_acsac(year=None, **_kwargs):
    # ACSAC template uses f-string for the year link, and needs double-brace
    # escaping for Liquid/Jinja since Python f-strings consume single braces.
    return f"""\
Results automatically obtained from <a href="https://www.acsac.org/{year}/program/artifacts/">ACSAC website</a>.

<table>
  <thead>
    <tr>
      <th>Title</th>
      <th>Avail.</th>
      <th>Review.</th>
      <th>Repro.</th>
      <th>Available At</th>
    </tr>
  </thead>
  <tbody>
    {{% for artifact in page.artifacts %}}
    <tr>
      <td>
        {{% if artifact.paper_url %}}
        <a href="{{{{artifact.paper_url}}}}" target="_blank">
          {{{{artifact.title}}}}
        </a>
        {{% else %}}
            {{{{ artifact.title }}}}
        {{% endif %}}
      </td>
      <td width="62px">
        {{% if artifact.badges contains "available" %}}
        <img alt="{{{{ page.available_name }}}}" src="{{{{ site.baseurl }}}}/images/{{{{ page.available_img }}}}">
        {{% endif %}}
      </td>
      <td width="62px">
        {{% if artifact.badges contains "reviewed" %}}
        <img alt="{{{{ page.reviewed_name }}}}" src="{{{{ site.baseurl }}}}/images/{{{{ page.reviewed_img }}}}">
        {{% endif %}}
      </td>
      <td width="62px">
        {{% if artifact.badges contains "reproducible" %}}
        <img alt="{{{{ page.reproducible_name }}}}" src="{{{{ site.baseurl }}}}/images/{{{{ page.reproducible_img }}}}">
        {{% endif %}}
      </td>
      <td>
        {{% if artifact.artifact_url %}}
            {{% assign artifacts = artifact.artifact_url | split: " " %}}
            {{% for url in artifacts %}}
        <a href="{{{{url}}}}" target="_blank">
          Artifact
        </a>
        <br>
        {{% endfor %}}
        {{% endif %}}
        {{% if artifact.artifact_urls %}}
            {{% for url in artifact.artifact_urls %}}
        <a href="{{{{url}}}}" target="_blank">
          Artifact {{{{ forloop.index }}}}
        </a>
        <br>
        {{% endfor %}}
        {{% endif %}}
        {{% if artifact.appendix_url %}}
        <a href="{{{{artifact.appendix_url}}}}" target="_blank">
          Appendix
        </a>
        <br>
        {{% endif %}}
      </td>
    </tr>
    {{% endfor %}}
  </tbody>
</table>"""


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════


def main():
    all_targets = sorted({t for t, _ in TARGET_CONFERENCE_CONFIG})
    all_confs = sorted({c for _, c in TARGET_CONFERENCE_CONFIG})

    parser = argparse.ArgumentParser(
        description="Generate artifact-evaluation results.md for sysartifacts / secartifacts sites"
    )
    parser.add_argument(
        "--target",
        "-t",
        type=str,
        required=True,
        choices=all_targets,
        help="Target site: sysartifacts or secartifacts",
    )
    parser.add_argument(
        "--conference",
        "-c",
        type=str,
        required=True,
        help=f"Conference ({', '.join(all_confs)})",
    )
    parser.add_argument(
        "--years",
        "-y",
        type=str,
        required=True,
        help="Comma-separated conference years (e.g. 2024,2025)",
    )
    parser.add_argument(
        "--output_dir",
        "-o",
        type=str,
        default=None,
        help="Output directory (results written to <output_dir>/<prefix><YYYY>/results.md)",
    )
    parser.add_argument(
        "--dir-prefix",
        type=str,
        default=None,
        help="Override directory prefix (default: from config)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print generated results to stdout instead of writing files",
    )
    parser.add_argument("--max-workers", type=int, default=4, help="Max parallel HTTP requests (default: 4)")
    parser.add_argument("--delay", type=float, default=0.5, help="Delay between requests in seconds (default: 0.5)")
    args = parser.parse_args()

    target = args.target.lower()
    conference = args.conference.lower()
    key = (target, conference)

    if key not in TARGET_CONFERENCE_CONFIG:
        valid = _available_conferences(target)
        parser.error(
            f"Conference '{conference}' is not supported for target '{target}'. Valid conferences: {', '.join(valid)}"
        )

    config = TARGET_CONFERENCE_CONFIG[key]
    dir_prefix = (args.dir_prefix or config["dir_prefix"]).lower()
    years = [int(y.strip()) for y in args.years.split(",")]

    for year in years:
        conf_label = f"{conference.upper()} {year}"
        logger.info("=" * 60)
        logger.info("Scraping %s for %s ...", conf_label, target)
        logger.info("=" * 60)

        artifacts = scrape_artifacts(config, year, max_workers=args.max_workers, delay=args.delay)

        if not artifacts:
            logger.warning("No artifacts found for %s", conf_label)
            continue

        # Log counts
        badge_cols = config["badge_columns"]
        badge_labels = config["badge_labels"]
        counts = {b: sum(1 for a in artifacts if b in a["badges"]) for b in badge_cols}
        count_str = ", ".join(f"{badge_labels[b]}={counts[b]}" for b in badge_cols)
        logger.info("%s: %d artifacts (%s)", conf_label, len(artifacts), count_str)

        content = generate_results_md(config, year, artifacts)

        # Organizers (USENIX sysartifacts only)
        organizers = scrape_organizers_for(config, year)
        organizers_content = generate_organizers_md(organizers)

        if args.dry_run:
            logger.info("\n--- %s%s/results.md ---", dir_prefix, year)
            logger.info("%s", content)
            if organizers_content:
                logger.info("\n--- %s%s/organizers.md ---", dir_prefix, year)
                logger.info("%s", organizers_content)
        else:
            out_dir = args.output_dir or "."
            conf_dir = os.path.join(out_dir, f"{dir_prefix}{year}")
            os.makedirs(conf_dir, exist_ok=True)

            out_path = os.path.join(conf_dir, "results.md")
            with open(out_path, "w") as f:
                f.write(content)
            logger.info("Written: %s", out_path)

            if organizers_content:
                org_path = os.path.join(conf_dir, "organizers.md")
                with open(org_path, "w") as f:
                    f.write(organizers_content)
                logger.info("Written: %s", org_path)

    logger.info("Done!")


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()
    main()
