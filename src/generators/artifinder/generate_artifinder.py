#!/usr/bin/env python3
"""Generate ArtiFinder integration outputs.

Downloads the ArtiFinder-Data set, matches each discovered artifact link to an
existing artifact-evaluation (AE) paper by **title + author list**, and:

  - **back-patches** ``assets/data/artifacts.json`` to add an ``artifinder_urls``
    list to every AE artifact that ArtiFinder found a link for.  These links
    carry no badges and never affect any score; the only place they may be
    reused is repository statistics (GitHub stars/forks), per project policy.
  - writes small Jekyll aggregates for the ArtiFinder discovery page:
    ``_data/artifinder_summary.yml``, ``_data/artifinder_by_year.yml``,
    ``_data/artifinder_by_conference.yml``.

The raw discovered links live in the upstream ArtiFinder-Data repository; we do
not republish them here.

Usage::

    python -m src.generators.artifinder.generate_artifinder --data_dir ../reprodb.github.io/src
"""

from __future__ import annotations

import argparse
import logging
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from src.models.artifacts.artifacts import Artifact
from src.scrapers.artifinder import DEFAULT_MIN_YEAR, ArtiFinderNoResultsError, load_artifinder
from src.scrapers.repo_utils import _normalise_github_repo_url
from src.utils.io.io import load_json, resolve_data_path, save_json, save_validated_json, save_yaml
from src.utils.normalization.conference import normalize_name, normalize_title

logger = logging.getLogger(__name__)

#: Minimum SequenceMatcher ratio for the fuzzy title fallback. Deliberately high
#: (exact matching handles the bulk); this only recovers same-paper titles that
#: differ by Unicode/LaTeX scraping artifacts or minor wording, and is further
#: guarded by author overlap when both author lists are known.
FUZZY_MIN_RATIO = 0.94


def _author_key_set(authors: list[str]) -> set[str]:
    """Return the set of aggressively-normalised author names."""
    return {normalize_name(a) for a in authors if a and normalize_name(a)}


def _build_paper_authors_index(paper_authors: list[dict]) -> dict[str, set[str]]:
    """Map normalised paper title → set of normalised author names."""
    index: dict[str, set[str]] = {}
    for pa in paper_authors:
        title = pa.get("title", "")
        if not title:
            continue
        index[normalize_title(title)] = _author_key_set(pa.get("authors", []))
    return index


def match_entries(
    entries: list[dict],
    artifacts: list[dict],
    authors_by_title: dict[str, set[str]],
) -> list[dict]:
    """Match ArtiFinder entries to AE artifacts by title + author overlap.

    Mutates matched ``artifacts`` in place to append ``artifinder_urls`` and
    returns the per-entry match records (used only to build the aggregates).

    First pass: same conference + year + identical normalised title. Second
    pass (fuzzy): for still-unmatched entries, the closest AE title in the same
    conference+year with a :data:`FUZZY_MIN_RATIO` similarity, to recover
    same-paper titles that differ by Unicode/LaTeX artifacts or minor wording.
    Both passes reject a match when author lists are known on both sides but
    share no author (guards against title collisions / different papers).
    """
    # Index artifacts by (conference, year, normalized_title) and by (conf, year).
    artifact_index: dict[tuple[str, int, str], dict] = {}
    by_conf_year: dict[tuple[str, int], list[tuple[str, dict]]] = defaultdict(list)
    for art_row in artifacts:
        conf = str(art_row.get("conference", "")).upper()
        year = int(art_row.get("year", 0))
        ant = normalize_title(art_row.get("title", ""))
        artifact_index.setdefault((conf, year, ant), art_row)
        by_conf_year[(conf, year)].append((ant, art_row))

    def _authors_overlap_ok(entry_authors: set[str], ae_norm_title: str) -> bool:
        """False only when both sides list authors but none overlap."""
        art_authors = authors_by_title.get(ae_norm_title, set())
        if entry_authors and art_authors:
            return bool(entry_authors & art_authors)
        return True

    def _fuzzy_match(nt: str, conf: str, year: int, entry_authors: set[str]) -> dict | None:
        """Return the best fuzzy-matching AE artifact in the same conf/year, or None."""
        best_ratio, best_art, best_nt = 0.0, None, ""
        for ant, art_row in by_conf_year.get((conf, year), ()):
            if not ant:
                continue
            ratio = SequenceMatcher(None, nt, ant).ratio()
            if ratio > best_ratio:
                best_ratio, best_art, best_nt = ratio, art_row, ant
        if best_art is not None and best_ratio >= FUZZY_MIN_RATIO and _authors_overlap_ok(entry_authors, best_nt):
            return best_art
        return None

    records: list[dict] = []
    fuzzy_matched = 0

    for entry in entries:
        conf = str(entry["conference"]).upper()
        year = int(entry["year"])
        nt = normalize_title(entry["title"])
        urls = entry["artifact_urls"]
        entry_authors = _author_key_set(entry.get("authors", []))

        # First pass: exact normalised title.
        art = artifact_index.get((conf, year, nt))
        matched = False
        via_fuzzy = False
        if art is not None and _authors_overlap_ok(entry_authors, nt):
            matched = True
        else:
            art = None

        # Second pass: fuzzy title within the same conference + year.
        if art is None:
            art = _fuzzy_match(nt, conf, year, entry_authors)
            if art is not None:
                matched = True
                via_fuzzy = True

        records.append(
            {
                "conference": entry["conference"],
                "year": year,
                "artifact_urls": urls,
                "matched_ae": matched,
            }
        )

        if matched and art is not None:
            if via_fuzzy:
                fuzzy_matched += 1
            existing = set(art.get("artifact_urls", [])) | set(art.get("artifinder_urls", []))
            # Compare on a scheme-insensitive / trailing-slash-insensitive basis.
            norm_existing = {u.rstrip("/") for u in existing}
            for url in urls:  # keeps ArtiFinder's order (best score first)
                if url.rstrip("/") not in norm_existing:
                    art.setdefault("artifinder_urls", []).append(url)
                    norm_existing.add(url.rstrip("/"))

    if fuzzy_matched:
        logger.info("  ArtiFinder: %d links matched to AE papers via fuzzy title fallback", fuzzy_matched)

    return records


def _build_stats(
    counts: list[dict], records: list[dict], source_updated: str | None = None
) -> tuple[dict, list[dict], list[dict]]:
    """Build summary, per-year, and per-conference aggregates for the website."""
    matched_by_cy: dict[tuple[str, int], int] = defaultdict(int)
    github_by_cy: dict[tuple[str, int], int] = defaultdict(int)
    for r in records:
        cy = (r["conference"], r["year"])
        if r["matched_ae"]:
            matched_by_cy[cy] += 1
        if any(_normalise_github_repo_url(u) for u in r["artifact_urls"]):
            github_by_cy[cy] += 1

    by_year_acc: dict[int, dict] = defaultdict(
        lambda: {"total_papers": 0, "discovered": 0, "matched_ae": 0, "github": 0}
    )
    by_conf_acc: dict[str, dict] = {}
    for c in counts:
        conf, year = c["conference"], c["year"]
        cy = (conf, year)
        y = by_year_acc[year]
        y["total_papers"] += c["total_papers"]
        y["discovered"] += c["discovered"]
        y["matched_ae"] += matched_by_cy.get(cy, 0)
        y["github"] += github_by_cy.get(cy, 0)

        cf = by_conf_acc.setdefault(
            conf,
            {"name": conf, "category": c["category"], "total_papers": 0, "discovered": 0, "matched_ae": 0, "years": []},
        )
        cf["total_papers"] += c["total_papers"]
        cf["discovered"] += c["discovered"]
        cf["matched_ae"] += matched_by_cy.get(cy, 0)
        cf["years"].append(
            {
                "year": year,
                "total_papers": c["total_papers"],
                "discovered": c["discovered"],
                "matched_ae": matched_by_cy.get(cy, 0),
            }
        )

    by_year = [
        {"year": yr, **{k: v[k] for k in ("total_papers", "discovered", "matched_ae", "github")}}
        for yr, v in sorted(by_year_acc.items())
    ]
    by_conf = sorted(by_conf_acc.values(), key=lambda x: x["name"])
    for cf in by_conf:
        cf["years"].sort(key=lambda x: x["year"])

    total_papers = sum(c["total_papers"] for c in counts)
    total_discovered = len(records)
    total_matched = sum(1 for r in records if r["matched_ae"])
    total_github = sum(1 for r in records if any(_normalise_github_repo_url(u) for u in r["artifact_urls"]))
    years = [c["year"] for c in counts]
    summary = {
        "total_papers": total_papers,
        "total_discovered": total_discovered,
        "total_matched_ae": total_matched,
        "total_unmatched": total_discovered - total_matched,
        "github_count": total_github,
        "discovery_pct": round(100 * total_discovered / total_papers, 1) if total_papers else 0.0,
        "conferences": sorted(by_conf_acc.keys()),
        "year_range": f"{min(years)}\u2013{max(years)}" if years else "",
        # Date the ArtiFinder-Data source was last updated (its latest commit),
        # falling back to today when that can't be determined.
        "data_updated": source_updated or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    return summary, by_year, by_conf


def _build_search_entries(entries: list[dict], records: list[dict]) -> list[dict]:
    """Build ``SearchEntry``-shaped rows for discovered papers that did NOT go
    through artifact evaluation (so they are absent from artifacts.json).

    These make the discovered artifacts appear in the site search, marked with
    the ArtiFinder sign. They carry no badges and no AE ``artifact_urls``; the
    link lives in ``artifinder_urls``. Records are aligned 1:1 with *entries*.
    """
    out: list[dict] = []
    for entry, rec in zip(entries, records, strict=True):
        if rec["matched_ae"]:
            continue  # already represented by its AE artifact in search
        page = entry.get("page_link") or ""
        is_doi = "doi.org" in page
        se: dict = {
            "title": entry["title"].strip(),
            "conference": entry["conference"],
            "category": entry["category"],
            "year": int(entry["year"]),
            "badges": [],
            "artifact_urls": [],
            "artifinder_urls": list(entry["artifact_urls"]),
            "doi_url": page if is_doi else "",
            "authors": entry.get("authors", []),
            "affiliations": [],
            "source": "artifinder",
        }
        if page and not is_doi:
            se["paper_url"] = page
        out.append(se)
    return out


def _af_author_key(name: str) -> str:
    """Normalised author key used to index discoveries per author.

    MUST produce byte-identical output to ``afAuthorKey()`` in the website's
    ``assets/js/reprodb-profile-page.js`` (the profile page looks papers up by
    this key). Lower-cases, strips accents (NFKD + drop combining marks), and
    reduces to single-spaced alphanumerics. Kept intentionally simple so the
    browser can reproduce it exactly.

    Shared test vector (keep in sync with the JS comment + tests):
        "Manuel Vögele"              -> "manuel vogele"
        "Anjo Vahldiek-Oberwagner"   -> "anjo vahldiek oberwagner"
        "Jane  Q.  Doe"              -> "jane q doe"
    """
    n = unicodedata.normalize("NFKD", name or "")
    n = "".join(c for c in n if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", n.lower()).strip()


def _build_author_index(entries: list[dict], records: list[dict]) -> dict[str, list[dict]]:
    """Map normalised author key -> list of non-AE discovered papers.

    Only unmatched (non-AE) discoveries are included; matched papers already
    appear on profiles via their AE artifact (with the ArtiFinder marker).
    Records are aligned 1:1 with *entries*.
    """
    index: dict[str, list[dict]] = defaultdict(list)
    for entry, rec in zip(entries, records, strict=True):
        if rec["matched_ae"]:
            continue
        paper = {
            "title": entry["title"].strip(),
            "conference": entry["conference"],
            "year": int(entry["year"]),
            "urls": list(entry["artifact_urls"]),
            # Kept for the currently deployed profile page, which reads a single
            # ``url``; equals the first (best-scored) link.
            "url": entry["artifact_urls"][0],
            "authors": entry.get("authors", []),
        }
        for author in entry.get("authors", []):
            key = _af_author_key(author)
            if key:
                index[key].append(paper)
    return dict(index)


def generate_artifinder(
    data_dir: str,
    min_year: int | None = DEFAULT_MIN_YEAR,
    conf_regex: str | None = None,
    local_dir: str | None = None,
) -> dict:
    """Run the ArtiFinder integration and write all output files."""
    root = Path(data_dir)
    assets_data = root / "assets" / "data"
    jekyll_data = root / "_data"
    build_dir = root / "_build"

    data = load_artifinder(conf_regex=conf_regex, min_year=min_year, local_dir=local_dir)
    if not data.entries:
        raise ArtiFinderNoResultsError(
            "ArtiFinder returned no results "
            f"(conf_regex={conf_regex!r}, min_year={min_year}, local_dir={local_dir!r}); "
            "check network access to GitHub or the filters. Existing outputs were left untouched."
        )

    artifacts = load_json(assets_data / "artifacts.json", default=[]) or []

    pa_path = resolve_data_path(root, "paper_authors_map.json")
    paper_authors = load_json(pa_path, default=[]) or [] if pa_path.exists() else []
    authors_by_title = _build_paper_authors_index(paper_authors)

    records = match_entries(data.entries, artifacts, authors_by_title)

    # Back-patch artifacts.json (only artifinder_urls were possibly added). This
    # is the single place ArtiFinder links are persisted for AE papers; the raw
    # links stay in the upstream ArtiFinder-Data repo. The repo_stats stage
    # reads the GitHub links directly from here.
    save_validated_json(assets_data / "artifacts.json", artifacts, Artifact)

    # Discovered papers that never went through AE become extra search rows so
    # they are findable in the site search (marked, no badges, no scores).
    search_entries = _build_search_entries(data.entries, records)
    save_json(build_dir / "artifinder_search_entries.json", search_entries)

    # Author-indexed non-AE discoveries so they can be listed (marked) on
    # author and institution profile pages without affecting any score.
    author_index = _build_author_index(data.entries, records)
    save_json(assets_data / "artifinder_authors.json", author_index)

    # Website statistics (Jekyll _data) for the ArtiFinder discovery page.
    summary, by_year, by_conf = _build_stats(data.counts, records, data.source_updated)
    # Distinct authors across all discovered-artifact entries (analogous to the
    # AE "total authors" figure shown on the methodology page).
    summary["author_count"] = len(
        {_af_author_key(a) for e in data.entries for a in e.get("authors", []) if _af_author_key(a)}
    )
    save_yaml(jekyll_data / "artifinder_summary.yml", summary)
    save_yaml(jekyll_data / "artifinder_by_year.yml", by_year)
    save_yaml(jekyll_data / "artifinder_by_conference.yml", by_conf)

    logger.info(
        "ArtiFinder: %d discovered links (%d matched to AE, %d non-AE search rows, %d GitHub), min_year=%s",
        summary["total_discovered"],
        summary["total_matched_ae"],
        len(search_entries),
        summary["github_count"],
        min_year,
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate ArtiFinder integration outputs")
    parser.add_argument("--data_dir", type=str, required=True, help="Website output directory")
    parser.add_argument(
        "--min_year",
        type=int,
        default=DEFAULT_MIN_YEAR,
        help=f"Earliest conference edition year to include (default {DEFAULT_MIN_YEAR}).",
    )
    parser.add_argument("--conf_regex", type=str, default=None, help="Optional conference-year filter regex")
    parser.add_argument(
        "--local_dir",
        type=str,
        default=None,
        help="Path to a local ArtiFinder-Data checkout (skips network access). "
        "Defaults to the REPRODB_ARTIFINDER_DIR environment variable.",
    )
    args = parser.parse_args()
    generate_artifinder(args.data_dir, min_year=args.min_year, conf_regex=args.conf_regex, local_dir=args.local_dir)


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()
    main()
