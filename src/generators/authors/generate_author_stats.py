#!/usr/bin/env python3
"""
Generate prolific artifact author statistics by matching artifact papers with DBLP.
This script requires downloading the DBLP XML file first (~3GB compressed).
Download from: https://dblp.org/xml/dblp.xml.gz
"""

import argparse
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from src.models.authors.author_stats import AuthorStats
from src.utils.io.io import load_json, load_yaml, save_json, save_validated_json, save_yaml
from src.utils.normalization.affiliation import normalize_affiliation as _normalize_affiliation
from src.utils.normalization.conference import clean_name as clean_display_name
from src.utils.normalization.conference import normalize_title

# Conference categorization is derived from the source (sys vs sec artifacts)
# and stored in the 'category' field of each artifact by generate_statistics.py


logger = logging.getLogger(__name__)


def load_artifacts(data_dir: str) -> list[dict] | None:
    """Load artifacts from generated data file"""
    artifacts_path = Path(data_dir) / "assets/data/artifacts.json"
    if not artifacts_path.exists():
        logger.error(f"Error: {artifacts_path} not found")
        logger.info("Please run generate_statistics.py first")
        return None

    artifacts = load_json(artifacts_path)

    return artifacts


def load_conference_active_years(data_dir):
    """Load artifacts_by_conference data and extract active years per conference.

    Returns dict: conference_name -> set of years when that conference had artifact evaluation.
    """
    conf_path = Path(data_dir) / "_data/artifacts_by_conference.yml"
    if not conf_path.exists():
        logger.warning(f"Warning: {conf_path} not found, will count all years")
        return {}

    conf_data = load_yaml(conf_path)

    active_years = {}
    for conf in conf_data:
        conf_name = conf.get("name", "").upper()
        if not conf_name:
            continue

        years = conf.get("years", [])
        # Include any year that had at least one artifact
        active_years[conf_name] = set(year_entry["year"] for year_entry in years if year_entry.get("total", 0) > 0)

    logger.info(f"Loaded active years for {len(active_years)} conferences")
    for conf, years in sorted(active_years.items()):
        if years:
            year_list = sorted(years)
            logger.info(f"  {conf}: {min(year_list)}-{max(year_list)} ({len(year_list)} years)")

    return active_years


def load_artifact_citations(data_dir: str) -> dict[str, int]:
    """Load artifact citation data if available.

    Returns dict: normalized_title -> cited_by_count (max across duplicates).
    """
    citations_path = Path(data_dir) / "assets" / "data" / "artifact_citations.json"
    if not citations_path.exists():
        logger.info("Artifact citation data not available (collection disabled — see generate_artifact_citations.py)")
        return {}

    entries = load_json(citations_path)

    by_title = {}
    for entry in entries:
        title = normalize_title(entry.get("title", ""))
        if not title:
            continue
        cited = entry.get("cited_by_count")
        if isinstance(cited, int):
            by_title[title] = max(by_title.get(title, 0), cited)

    return by_title


def extract_paper_titles(artifacts: list[dict]) -> tuple[set[str], dict[str, dict]]:
    """Extract unique paper titles from artifacts"""
    titles = set()
    title_to_artifact = {}

    for artifact in artifacts:
        title = artifact.get("title", "")
        if title and title != "Unknown":
            normalized = normalize_title(title)
            titles.add(normalized)
            # Keep mapping for metadata
            if normalized not in title_to_artifact:
                title_to_artifact[normalized] = artifact

    # Found {len(titles)} unique paper titles
    return titles, title_to_artifact


def parse_dblp_for_authors(
    dblp_file: str, paper_titles: set[str], title_to_artifact: dict[str, dict]
) -> tuple[list[dict], dict, dict]:
    """
    Find authors for artifact papers using pre-extracted DBLP cache.

    Calls ``extract_dblp()`` to ensure the cache exists, then reads from
    the JSON lookup files rather than re-parsing the 3 GB XML.

    Args:
        dblp_file: Path to dblp.xml.gz file (passed to extract_dblp)
        paper_titles: Set of normalized paper titles to find
        title_to_artifact: Mapping from title to artifact metadata

    Returns:
        Tuple of:
          - List of papers with author information (artifact papers)
          - Dict mapping (author, conference) -> {year: set of normalized titles}
            for ALL papers at tracked venues (venue_papers)
          - Dict mapping author_name -> affiliation string (from DBLP <www> entries)
    """
    if not Path(dblp_file).exists():
        logger.error(f"Error: DBLP file not found: {dblp_file}")
        logger.info("Please download from: https://dblp.org/xml/dblp.xml.gz")
        return [], {}, {}

    from src.utils.apis.dblp_extract import extract_dblp, load_affiliations, load_papers_by_venue

    logger.info("Extracting DBLP data (cached if unchanged)...")
    extract_dblp(dblp_file)

    affiliations = load_affiliations()
    papers_by_venue = load_papers_by_venue()

    logger.info(f"Loaded {len(affiliations)} affiliations, {len(papers_by_venue)} venue groups from cache")

    papers_found = []
    titles_to_find = paper_titles.copy()
    # (author_name, conference) -> {year: set of normalized_title}
    venue_papers = defaultdict(lambda: defaultdict(set))

    for conf, years in papers_by_venue.items():
        for year_str, paper_list in years.items():
            year = int(year_str) if year_str else 0
            for paper in paper_list:
                title = paper.get("title", "")
                normalized = normalize_title(title.rstrip("."))
                authors = paper.get("authors", [])

                # Track all venue papers (for total-papers denominator)
                for author in authors:
                    if author:
                        venue_papers[(author, conf)][year].add(normalized)

                # Match artifact titles
                if normalized in titles_to_find:
                    artifact_meta = title_to_artifact.get(normalized, {})

                    # Build DOI URL from extracted DOI
                    doi = paper.get("doi", "")
                    doi_url = f"https://doi.org/{doi}" if doi else ""

                    paper_info = {
                        "title": title,
                        "normalized_title": normalized,
                        "authors": authors,
                        "year": year if year else artifact_meta.get("year"),
                        "artifact_year": artifact_meta.get("year"),
                        "venue": conf,
                        "conference": artifact_meta.get("conference", ""),
                        "category": artifact_meta.get("category", "unknown"),
                        "badges": artifact_meta.get("badges", []),
                        "doi_url": doi_url,
                    }

                    papers_found.append(paper_info)
                    titles_to_find.remove(normalized)

    if titles_to_find:
        logger.warning(f"Warning: {len(titles_to_find)} papers not found in DBLP")

    total_venue = sum(len(t) for ydict in venue_papers.values() for t in ydict.values())
    logger.info(f"Total artifact papers matched: {len(papers_found)}")
    logger.info(f"Total papers tracked at conference venues: {total_venue} (author-paper pairs)")
    logger.info(f"Total DBLP affiliations extracted: {len(affiliations)}")

    return papers_found, venue_papers, affiliations


def aggregate_author_statistics(
    papers, venue_papers=None, affiliations=None, conference_active_years=None, citations_by_title=None
):
    """Calculate statistics per author.

    Args:
        papers: list of artifact papers with author info
        venue_papers: optional dict (author, conference)->year_dict->set(titles)
                      of ALL papers at tracked conferences
        affiliations: optional dict author_name -> affiliation string
        conference_active_years: optional dict conference_name -> set of active years
                      Only papers from these years will be counted in total_papers
    """
    if venue_papers is None:
        venue_papers = {}
    if affiliations is None:
        affiliations = {}
    if conference_active_years is None:
        conference_active_years = {}
    if citations_by_title is None:
        citations_by_title = {}

    author_stats = defaultdict(
        lambda: {
            "name": "",
            "artifact_count": 0,
            "papers": [],
            "papers_without_artifacts": [],
            "artifact_titles": set(),  # Track which titles have artifacts
            "conferences": set(),
            "years": set(),
            "artifact_citations": 0,
            "badges": {"available": 0, "functional": 0, "reproducible": 0},
        }
    )

    # Pre-populate venue_papers with ALL artifact papers.
    # This guarantees artifacts <= total_papers by construction:
    # every artifact paper is counted in the denominator even when the
    # DBLP venue mapping misses a journal/booktitle alias.
    for paper in papers:
        conf = paper.get("conference", "")
        if not conf:
            continue
        # Prefer the artifact's declared year over the DBLP year.
        # DBLP may have matched a preprint version with a different year.
        yr = paper.get("artifact_year") or paper.get("year")
        if yr is None:
            yr = 0
        active_years = conference_active_years.get(conf, set())
        if active_years and yr not in active_years:
            continue
        title_norm = paper.get("normalized_title", "")
        if not title_norm:
            continue
        for author in paper.get("authors", []):
            if not author:
                continue
            if (author, conf) not in venue_papers:
                venue_papers[(author, conf)] = defaultdict(set)
            venue_papers[(author, conf)][yr].add(title_norm)

    for paper in papers:
        for author in paper["authors"]:
            stats = author_stats[author]
            stats["name"] = author

            title_key = paper.get("normalized_title")
            if title_key in stats["artifact_titles"]:
                continue

            stats["artifact_count"] += 1
            stats["papers"].append(
                {
                    "title": paper["title"],
                    "conference": paper["conference"],
                    "year": paper["year"],
                    "badges": paper["badges"],
                    "category": paper.get("category", "unknown"),
                    "artifact_citations": citations_by_title.get(title_key, 0),
                }
            )
            stats["artifact_citations"] += citations_by_title.get(title_key, 0)
            # Track normalized title to identify papers WITHOUT artifacts later
            stats["artifact_titles"].add(title_key)
            stats["conferences"].add(paper["conference"])
            stats["years"].add(paper["year"])

            badge_list = paper["badges"]
            if isinstance(badge_list, str):
                badge_list = [b.strip() for b in badge_list.split(",")]

            # If artifact was evaluated but has no formal badges recorded, treat as "available"
            has_available = False
            has_functional = False
            has_repro = False
            if not badge_list or len(badge_list) == 0:
                has_available = True
            else:
                for badge in badge_list:
                    badge_lower = badge.lower()
                    if "reproduc" in badge_lower or "reusable" in badge_lower:
                        has_repro = True
                    elif "functional" in badge_lower:
                        has_functional = True
                    elif "available" in badge_lower:
                        has_available = True

            if has_available:
                stats["badges"]["available"] += 1
            if has_functional:
                stats["badges"]["functional"] += 1
            if has_repro:
                stats["badges"]["reproducible"] += 1

    # Convert to list and add computed fields
    authors_list = []
    current_year = datetime.now().year

    # Track category-specific authors
    systems_authors = set()
    security_authors = set()
    cross_domain_authors = set()

    for author, stats in author_stats.items():
        years_sorted = sorted(stats["years"])
        recent_count = sum(1 for y in stats["years"] if y >= current_year - 3)

        # Determine author category based on paper categories
        list(stats["conferences"])
        paper_categories = set(p.get("category", "unknown") for p in stats["papers"])
        has_systems = "systems" in paper_categories
        has_security = "security" in paper_categories

        if has_systems and has_security:
            category = "both"
            cross_domain_authors.add(author)
            systems_authors.add(author)
            security_authors.add(author)
        elif has_systems:
            category = "systems"
            systems_authors.add(author)
        elif has_security:
            category = "security"
            security_authors.add(author)
        else:
            category = "unknown"

        # --- Compute total papers at tracked conferences (per-conf per-year) ---
        # Only count papers from years when the conference was actively doing AE
        total_papers_set = set()
        conf_title_sets = {}
        total_papers_by_conf = {}
        total_papers_by_conf_year = {}
        for conf in stats["conferences"]:
            year_dict = venue_papers.get((author, conf), {})
            conf_titles = set()
            conf_year_counts = {}
            active_years = conference_active_years.get(conf, set())

            for yr, titles in year_dict.items():
                # Only count papers from years when this conference had AE
                # If no active_years data available, count all years (backward compat)
                if not active_years or yr in active_years:
                    conf_titles |= titles
                    conf_year_counts[yr] = len(titles)

            total_papers_set |= conf_titles
            conf_title_sets[conf] = conf_titles
            total_papers_by_conf[conf] = len(conf_titles)
            total_papers_by_conf_year[conf] = conf_year_counts

        # Also check conferences the author didn't have artifacts at but
        # did publish at (from DBLP venue scan)
        for (a, c), year_dict in venue_papers.items():
            if a == author and c not in total_papers_by_conf:
                conf_titles = set()
                conf_year_counts = {}
                active_years = conference_active_years.get(c, set())

                for yr, titles in year_dict.items():
                    # Only count papers from years when this conference had AE
                    if not active_years or yr in active_years:
                        conf_titles |= titles
                        conf_year_counts[yr] = len(titles)

                total_papers_set |= conf_titles
                conf_title_sets[c] = conf_titles
                total_papers_by_conf[c] = len(conf_titles)
                total_papers_by_conf_year[c] = conf_year_counts

        # Recompute totals (artifact papers are already in venue_papers
        # thanks to the pre-population step above)
        total_papers_set = set()
        total_papers_by_conf = {}
        for conf, conf_titles in conf_title_sets.items():
            total_papers_set |= conf_titles
            total_papers_by_conf[conf] = len(conf_titles)
        total_papers = len(total_papers_set) if total_papers_set else 0

        # --- Compute papers WITHOUT artifacts ---
        # Collect all papers from venue_papers, then subtract artifact papers
        all_venue_papers = []  # List of (conf, year, title) for papers at venues
        for (a, c), year_dict in venue_papers.items():
            if a == author:
                active_years = conference_active_years.get(c, set())
                for yr, titles in year_dict.items():
                    if not active_years or yr in active_years:
                        for title in titles:
                            all_venue_papers.append((c, yr, title))

        # Find papers without artifacts
        papers_without = []
        for conf, yr, title in all_venue_papers:
            if title not in stats["artifact_titles"]:  # Not an artifact paper
                papers_without.append({"title": title, "conference": conf, "year": yr})

        # Remove duplicates and sort by year desc, then conference
        papers_without_dedup = {}
        for p in papers_without:
            # Use title+year+conf as key to deduplicate
            key = (p["title"], p["year"], p["conference"])
            if key not in papers_without_dedup:
                papers_without_dedup[key] = p

        papers_without_list = list(papers_without_dedup.values())
        papers_without_list.sort(key=lambda x: (-x["year"], x["conference"]))

        art_count = stats["artifact_count"]
        avail = stats["badges"]["available"]
        func = stats["badges"]["functional"]
        repro = stats["badges"]["reproducible"]

        if art_count > total_papers:
            raise ValueError(
                f"Invariant violation for '{stats['name']}': artifacts ({art_count}) > total_papers ({total_papers})"
            )
        if repro > art_count:
            raise ValueError(
                f"Invariant violation for '{stats['name']}': reproduced_badges ({repro}) > artifacts ({art_count})"
            )
        if func > art_count:
            raise ValueError(
                f"Invariant violation for '{stats['name']}': functional_badges ({func}) > artifacts ({art_count})"
            )

        # Artifact rate: % of tracked-conference papers that have an artifact.
        artifact_pct = round(art_count / total_papers * 100, 1) if total_papers > 0 else 0.0
        # Reproducibility rate: % of artifact papers with a "reproduced" badge
        repro_pct = round(repro / art_count * 100, 1) if art_count > 0 else 0.0
        # Functional rate: % of artifact papers with a "functional" badge
        functional_pct = round(func / art_count * 100, 1) if art_count > 0 else 0.0

        # Look up affiliation from DBLP
        affiliation_raw = affiliations.get(stats["name"], "")
        affiliation = _normalize_affiliation(affiliation_raw)

        author_entry = {
            "name": stats["name"],
            "display_name": clean_display_name(stats["name"]),
            "affiliation": affiliation,
            "artifact_count": art_count,
            "total_papers": total_papers,
            "total_papers_by_conf": total_papers_by_conf,
            "total_papers_by_conf_year": total_papers_by_conf_year,
            "artifact_pct": artifact_pct,
            "repro_pct": repro_pct,
            "functional_pct": functional_pct,
            "category": category,
            "conferences": sorted(list(stats["conferences"])),
            "years": years_sorted,
            "year_range": f"{min(years_sorted)}-{max(years_sorted)}" if years_sorted else "",
            "recent_count": recent_count,
            "artifact_citations": stats["artifact_citations"],
            "badges_available": avail,
            "badges_functional": func,
            "badges_reproducible": repro,
            "papers": stats["papers"],
            "papers_without_artifacts": papers_without_list,
        }
        authors_list.append(author_entry)

    # Sort by artifact count
    authors_list.sort(key=lambda x: x["artifact_count"], reverse=True)

    # Add category breakdown to return
    category_breakdown = {
        "systems_count": len(systems_authors),
        "security_count": len(security_authors),
        "cross_domain_count": len(cross_domain_authors),
    }

    return authors_list, category_breakdown


def generate_author_stats(dblp_file: str, data_dir: str, output_dir: str) -> None:
    """Main function to generate author statistics"""
    logger.info("Generating author statistics...")

    # Load artifacts
    artifacts = load_artifacts(data_dir)
    if not artifacts:
        return None

    # Load conference active years (years when each conference had AE)
    conference_active_years = load_conference_active_years(data_dir)

    # Total artifacts: {len(artifacts)}

    # Extract titles
    paper_titles, title_to_artifact = extract_paper_titles(artifacts)

    # Parse DBLP
    papers_with_authors, venue_papers, affiliations = parse_dblp_for_authors(dblp_file, paper_titles, title_to_artifact)

    if not papers_with_authors:
        logger.info("No papers matched in DBLP")
        return None

    # Load artifact citations (optional)
    citations_by_title = load_artifact_citations(data_dir)

    # Aggregate statistics (pass venue_papers and conference_active_years for total-paper counts)
    authors_list, category_breakdown = aggregate_author_statistics(
        papers_with_authors, venue_papers, affiliations, conference_active_years, citations_by_title
    )

    # Load author index for IDs and canonical affiliations
    try:
        from src.utils.normalization.author_index import load_author_index

        index_entries, index_by_name = load_author_index(data_dir)
        if index_by_name:
            patched_aff = 0
            for author in authors_list:
                idx_entry = index_by_name.get(author["name"])
                if idx_entry is None:
                    continue
                if idx_entry.get("id") is not None:
                    author["author_id"] = idx_entry["id"]
                # Override affiliation with canonical index value (enricher-sourced)
                idx_aff = idx_entry.get("affiliation", "")
                if idx_aff and idx_aff != author.get("affiliation", ""):
                    author["affiliation"] = idx_aff
                    patched_aff += 1
            assigned = sum(1 for a in authors_list if "author_id" in a)
            logger.info(f"Author IDs assigned: {assigned}/{len(authors_list)}")
            logger.info(f"Affiliations overridden from author index: {patched_aff}")
    except ImportError:
        logger.debug("Optional module not available, skipping enrichment")

    # Count affiliation coverage
    with_affil = sum(1 for a in authors_list if a.get("affiliation"))
    logger.info(
        f"Authors with DBLP affiliation: {with_affil}/{len(authors_list)} ({round(with_affil / len(authors_list) * 100, 1) if authors_list else 0}%)"
    )

    # Generate summary
    author_summary = {
        "total_authors": len(authors_list),
        "total_papers_matched": len(papers_with_authors),
        "active_last_year": sum(1 for a in authors_list if a["recent_count"] > 0),
        "multi_conference": sum(1 for a in authors_list if len(a["conferences"]) > 1),
        "systems_authors": category_breakdown["systems_count"],
        "security_authors": category_breakdown["security_count"],
        "cross_domain_authors": category_breakdown["cross_domain_count"],
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC"),
    }

    # Write output files
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "_data").mkdir(parents=True, exist_ok=True)
    (output_dir / "assets/data").mkdir(parents=True, exist_ok=True)

    # --- Build paper index and replace embedded papers with IDs ---
    from .generate_paper_index import build_paper_index, load_existing_index, normalize_title

    # Load existing index from the canonical location (assets/data)
    assets_papers = output_dir / "assets/data/papers.json"
    existing_papers, existing_by_title = load_existing_index(assets_papers)
    max_paper_id = max((e["id"] for e in existing_papers), default=0)
    papers_list, norm_to_id = build_paper_index(authors_list, existing_by_title, max_paper_id)

    # Write paper index (single copy in assets/data)
    save_json(assets_papers, papers_list, indent=None)
    artifact_count = sum(1 for p in papers_list if p.get("has_artifact", True))
    logger.info(f"Paper index: {len(papers_list)} papers ({artifact_count} with artifacts)")

    # Replace embedded papers with paper_ids in authors_list
    for author in authors_list:
        paper_ids = []
        for p in author.get("papers", []):
            norm = normalize_title(p.get("title", ""))
            pid = norm_to_id.get(norm)
            if pid is not None:
                paper_ids.append(pid)
        author["paper_ids"] = paper_ids

        without_ids = []
        for p in author.get("papers_without_artifacts", []):
            norm = normalize_title(p.get("title", ""))
            pid = norm_to_id.get(norm)
            if pid is not None:
                without_ids.append(pid)
        author["papers_without_artifact_ids"] = without_ids

        # Keep 'papers' in the full JSON for backward compatibility,
        # but remove from YAML to cut file size

    # YAML for Jekyll — without embedded papers (use paper_ids instead)
    authors_for_yaml = []
    for author in authors_list:
        entry = {
            k: v
            for k, v in author.items()
            if k not in ("papers", "papers_without_artifacts", "total_papers_by_conf", "total_papers_by_conf_year")
        }
        authors_for_yaml.append(entry)
    save_yaml(output_dir / "_data/authors.yml", authors_for_yaml)

    save_yaml(output_dir / "_data/author_summary.yml", author_summary)

    # JSON for download (full data including embedded papers for backward compat)
    save_validated_json(output_dir / "assets/data/authors.json", authors_list, AuthorStats)

    # Paper -> authors mapping for citation attribution (intermediate, not deployed)
    build_dir = output_dir / "_build"
    build_dir.mkdir(parents=True, exist_ok=True)
    save_json(
        build_dir / "paper_authors_map.json",
        [{k: v for k, v in p.items() if k != "normalized_title"} for p in papers_with_authors],
    )

    logger.info(f"Author data written to {output_dir} ({len(authors_list)} authors, {len(papers_with_authors)} papers)")

    return {"authors": authors_list, "summary": author_summary}


def main():
    parser = argparse.ArgumentParser(description="Generate author statistics from DBLP")
    repo_root = Path(__file__).resolve().parent.parent.parent
    default_dblp = str(repo_root / "data" / "dblp" / "dblp.xml.gz")
    parser.add_argument(
        "--dblp_file",
        type=str,
        default=default_dblp,
        help="Path to DBLP XML file (download from https://dblp.org/xml/dblp.xml.gz)",
    )
    parser.add_argument("--data_dir", type=str, required=True, help="Directory containing generated artifacts data")
    parser.add_argument("--output_dir", type=str, required=True, help="Output directory for author statistics")

    args = parser.parse_args()

    result = generate_author_stats(args.dblp_file, args.data_dir, args.output_dir)

    if result:
        s = result["summary"]
        logger.info(
            f"Authors: {s['total_authors']} ({s['total_papers_matched']} papers, {s['multi_conference']} multi-conf)"
        )


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()

    main()
