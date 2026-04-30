"""Auto-generate CLI reference pages from argparse parsers.

This script runs at MkDocs build time via mkdocs-gen-files.
It captures --help output for each module and writes markdown pages.
"""

import subprocess
import sys

import mkdocs_gen_files

# All modules with argparse CLIs, grouped by category.
MODULES = [
    # (module_path, display_name, category, pipeline_step_or_None)
    # Generators
    ("src.generators.output.generate_statistics", "generate_statistics", "Generators", "2"),
    ("src.generators.repository.generate_repo_stats", "generate_repo_stats", "Generators", "3"),
    ("src.generators.repository.generate_artifact_availability", "generate_artifact_availability", "Generators", "3b"),
    ("src.generators.repository.generate_participation_stats", "generate_participation_stats", "Generators", "3c"),
    ("src.generators.output.generate_visualizations", "generate_visualizations", "Generators", "6"),
    ("src.generators.authors.generate_author_stats", "generate_author_stats", "Generators", "7"),
    ("src.generators.authors.generate_area_authors", "generate_area_authors", "Generators", "8"),
    ("src.generators.generate_committee_stats", "generate_committee_stats", "Generators", "9"),
    ("src.generators.rankings.generate_combined_rankings", "generate_combined_rankings", "Generators", "10"),
    ("src.generators.rankings.generate_institution_rankings", "generate_institution_rankings", "Generators", "11"),
    ("src.generators.authors.generate_author_profiles", "generate_author_profiles", "Generators", "12"),
    ("src.generators.output.generate_search_data", "generate_search_data", "Generators", "13"),
    ("src.generators.rankings.generate_ranking_history", "generate_ranking_history", "Generators", "14"),
    ("src.generators.authors.generate_author_index", "generate_author_index", "Generators", None),
    ("src.generators.citations.generate_artifact_citations", "generate_artifact_citations", "Generators", None),
    ("src.generators.citations.generate_cited_artifacts_list", "generate_cited_artifacts_list", "Generators", None),
    ("src.generators.citations.generate_paper_citations", "generate_paper_citations", "Generators", None),
    ("src.generators.citations.export_artifact_citations", "export_artifact_citations", "Generators", None),
    ("src.generators.citations.verify_artifact_citations", "verify_artifact_citations", "Generators", None),
    ("src.generators.analyze_retention", "analyze_retention", "Generators", None),
    ("src.generators.collect_repo_detail", "collect_repo_detail", "Generators", None),
    ("src.generators.output.generate_paper_index", "generate_paper_index", "Generators", None),
    ("src.generators.generate_artifact_sources_table", "generate_artifact_sources_table", "Generators", None),
    ("src.generators.generate_artifact_sources_timeline", "generate_artifact_sources_timeline", "Generators", None),
    # Enrichers
    ("src.enrichers.enrich_affiliations_csrankings", "enrich_affiliations_csrankings", "Enrichers", None),
    ("src.enrichers.enrich_affiliations_dblp", "enrich_affiliations_dblp", "Enrichers", None),
    ("src.enrichers.enrich_affiliations_dblp_incremental", "enrich_affiliations_dblp_incremental", "Enrichers", None),
    ("src.enrichers.enrich_affiliations_openalex", "enrich_affiliations_openalex", "Enrichers", None),
    # Scrapers
    ("src.scrapers.usenix_scrape", "usenix_scrape", "Scrapers", None),
    ("src.scrapers.acm_scrape", "acm_scrape", "Scrapers", None),
    ("src.scrapers.parse_committee_md", "parse_committee_md", "Scrapers", None),
    ("src.scrapers.parse_results_md", "parse_results_md", "Scrapers", None),
    ("src.scrapers.generate_results", "generate_results", "Scrapers", None),
    ("src.scrapers.acsac_scrape", "acsac_scrape", "Scrapers", None),
    ("src.scrapers.scrape_committee_web", "scrape_committee_web", "Scrapers", None),
    # Utils
    ("src.utils.apis.dblp_extract", "dblp_extract", "Utilities", "1b"),
    ("src.utils.committee_statistics", "committee_statistics", "Utilities", None),
    ("src.utils.collection.test_artifact_repositories", "test_artifact_repositories", "Utilities", None),
    ("src.utils.collection.collect_artifact_stats", "collect_artifact_stats", "Utilities", None),
]


def _get_help(module_path: str) -> str:
    """Capture --help output from a module."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", module_path, "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout.strip() or result.stderr.strip()
    except Exception as e:
        return f"(could not capture help: {e})"


# Build index page
index_lines = ["# CLI Reference\n\n"]
index_lines.append("Command-line interface reference for all pipeline scripts.\n\n")

# Pipeline order table
pipeline_modules = [(m, n, c, s) for m, n, c, s in MODULES if s]
pipeline_modules.sort(key=lambda x: x[3].replace("b", ".1").replace("c", ".2"))
index_lines.append("## Pipeline Order\n\n")
index_lines.append("| Step | Script | Category |\n|------|--------|----------|\n")
for _mod, name, cat, step in pipeline_modules:
    index_lines.append(f"| {step} | [{name}]({name}.md) | {cat} |\n")
index_lines.append("\n")

# Group by category
current_cat = None
for _mod, name, cat, step in MODULES:
    if cat != current_cat:
        current_cat = cat
        index_lines.append(f"## {cat}\n\n")
        index_lines.append("| Script | Pipeline Step |\n|--------|---------------|\n")
    step_str = step or "—"
    index_lines.append(f"| [{name}]({name}.md) | {step_str} |\n")

with mkdocs_gen_files.open("cli/index.md", "w") as f:
    f.writelines(index_lines)

# Individual pages
for mod, name, cat, step in MODULES:
    help_text = _get_help(mod)
    lines = [f"# {name}\n\n"]
    if step:
        lines.append(f'!!! info "Pipeline step {step}"\n\n')
    lines.append(f"**Module:** `{mod}`  \n")
    lines.append(f"**Category:** {cat}\n\n")
    lines.append("## Usage\n\n```bash\n")
    lines.append(f"python -m {mod} [options]\n")
    lines.append("```\n\n")
    lines.append("## Options\n\n```\n")
    lines.append(help_text)
    lines.append("\n```\n")

    with mkdocs_gen_files.open(f"cli/{name}.md", "w") as f:
        f.writelines(lines)
