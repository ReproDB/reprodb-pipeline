#!/usr/bin/env python3
"""
Pre-extract structured data from the local DBLP XML dump.

Parses ``dblp.xml.gz`` in a single pass and writes JSON lookup files that
every downstream pipeline step can load instead of hitting the DBLP API.

Outputs (under *output_dir*):
  .cache/dblp_extracted/papers_by_venue.json
      {conf: {year_str: [{title, authors, doi, dblp_key}]}}

  .cache/dblp_extracted/affiliations.json
      {author_name: affiliation}

The extraction is cached: if the DBLP file has not changed (same mtime)
the previous JSON files are reused.

Usage:
  python -m src.utils.dblp_extract --dblp_file data/dblp/dblp.xml.gz

NOTE — DBLP API policy
~~~~~~~~~~~~~~~~~~~~~~
We deliberately avoid the DBLP web API (https://dblp.org/search/…).  The
local XML dump contains the same data and avoids rate-limiting issues that
grow worse as the number of tracked conferences increases.  All new code
should use the extracted JSON files produced by this module.  Do NOT add
new DBLP API calls.
"""

from __future__ import annotations

import argparse
import html.entities
import io
import json
import logging
import os
from collections import defaultdict
from gzip import GzipFile
from pathlib import Path

import lxml.etree as ET

from src.utils.normalization.conference import venue_to_conference

logger = logging.getLogger(__name__)
# Where we write the extracted JSON files
_EXTRACT_DIR_NAME = "dblp_extracted"


_REPO_ROOT = Path(__file__).resolve().parents[3]


def _extract_dir(repo_root=None):
    """Return the directory where extracted JSON files are stored."""
    root = Path(repo_root) if repo_root else _REPO_ROOT
    return str(root / ".cache" / _EXTRACT_DIR_NAME)


def _mtime_file(extract_dir):
    return os.path.join(extract_dir, "_dblp_mtime")


def _is_fresh(dblp_file, extract_dir):
    """Check whether the cached extraction is still valid."""
    mtime_path = _mtime_file(extract_dir)
    if not os.path.exists(mtime_path):
        return False
    try:
        with open(mtime_path) as f:
            cached_mtime = float(f.read().strip())
        return os.path.getmtime(dblp_file) == cached_mtime
    except (ValueError, OSError):
        return False


# ---------------------------------------------------------------------------
# HTML entity definitions for DBLP XML
# ---------------------------------------------------------------------------
# DBLP XML references an external DTD (dblp.dtd) that defines HTML entities
# like &ouml; &eacute; etc.  When the DTD file is absent, lxml with
# recover=True silently drops unresolved entities, corrupting names such as
# "Jörg" → "Jrg".  We inject an internal-DTD subset with all required
# entity definitions so the parser resolves them correctly.
#
# We use Python's standard html.entities.name2codepoint which already maps
# every HTML entity name to its Unicode codepoint — no need to maintain a
# manual map.
_HTML_ENTITY_MAP: dict[str, int] = html.entities.name2codepoint

_INTERNAL_DTD_ENTITIES = "\n".join(f'  <!ENTITY {name} "&#x{cp:04X};">' for name, cp in _HTML_ENTITY_MAP.items())
_INTERNAL_DOCTYPE = b"<!DOCTYPE dblp [\n" + _INTERNAL_DTD_ENTITIES.encode("ascii") + b"\n]>"


class _PatchedDTDStream:
    """Wraps a GzipFile stream to replace the external DTD reference with inline entity definitions.

    DBLP XML starts with ``<!DOCTYPE dblp SYSTEM "dblp.dtd">``.
    This wrapper substitutes it with an internal DTD subset so that
    lxml can resolve HTML entities without an external DTD file.
    """

    def __init__(self, raw_stream: GzipFile):
        self._raw = raw_stream
        # Read enough to capture the DOCTYPE (it's in the first ~100 bytes)
        header = self._raw.read(4096)
        header = header.replace(
            b'<!DOCTYPE dblp SYSTEM "dblp.dtd">',
            _INTERNAL_DOCTYPE,
        )
        self._prefix = io.BytesIO(header)
        self._prefix_done = False

    def read(self, size: int = -1) -> bytes:
        if self._prefix_done:
            return self._raw.read(size)
        chunk = self._prefix.read(size)
        if size < 0:
            # Read everything
            self._prefix_done = True
            return chunk + self._raw.read()
        if len(chunk) < size:
            self._prefix_done = True
            return chunk + self._raw.read(size - len(chunk))
        return chunk


def extract_dblp(dblp_file: str) -> tuple[str, str]:
    """Parse dblp.xml.gz and write JSON lookup files.

    Returns (papers_path, affiliations_path).
    """
    extract_dir = _extract_dir()
    os.makedirs(extract_dir, exist_ok=True)

    papers_path = os.path.join(extract_dir, "papers_by_venue.json")
    affiliations_path = os.path.join(extract_dir, "affiliations.json")

    # Re-use cached files if the DBLP dump hasn't changed
    if _is_fresh(dblp_file, extract_dir):
        logger.warning("DBLP extraction cache is fresh — skipping parse")
        return papers_path, affiliations_path

    logger.info(f"Parsing DBLP XML ({dblp_file}) …")

    # {conf -> {year_str -> [paper_dict]}}
    papers: defaultdict[str, defaultdict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    # {author_name -> affiliation}
    affiliations = {}

    dblp_stream = _PatchedDTDStream(GzipFile(filename=dblp_file))
    iteration = 0

    for _, elem in ET.iterparse(
        dblp_stream,
        events=("end",),
        tag=("inproceedings", "article", "www"),
        load_dtd=True,
        recover=True,
        huge_tree=True,
    ):
        # --- Person records: extract affiliations ---
        if elem.tag == "www":
            authors = [a.text for a in elem.findall("author") if a.text]
            affil = None
            for note in elem.findall("note"):
                if note.get("type") == "affiliation" and note.text:
                    affil = note.text.strip()
                    break  # take the first (most recent) affiliation
            if affil:
                for name in authors:
                    if name not in affiliations:
                        affiliations[name] = affil
            elem.clear()
            continue

        # --- Papers ---
        booktitle = elem.findtext("booktitle") or elem.findtext("journal") or ""
        conf = venue_to_conference(booktitle)
        if conf:
            year_str = elem.findtext("year")
            if year_str:
                title = elem.findtext("title") or ""
                # Strip trailing period (DBLP convention)
                title = title.rstrip(".")

                # Extract DOI from <ee> elements
                doi = ""
                for ee in elem.findall("ee"):
                    if ee.text and "doi.org/" in ee.text:
                        doi = ee.text.split("doi.org/")[-1]
                        break

                authors = [a.text for a in elem.findall("author") if a.text]
                dblp_key = elem.get("key", "")

                papers[conf][year_str].append(
                    {
                        "title": title,
                        "authors": authors,
                        "doi": doi,
                        "dblp_key": dblp_key,
                    }
                )

        iteration += 1
        if iteration % 2_000_000 == 0:
            logger.info(f"  … {iteration // 1_000_000}M elements")
        elem.clear()

    dblp_stream._raw.close()

    total_papers = sum(len(plist) for conf_years in papers.values() for plist in conf_years.values())
    logger.info(
        f"  Done — {iteration} elements, {total_papers} conference papers, {len(affiliations)} author affiliations"
    )

    # Write JSON files (ensure_ascii=False preserves Unicode characters)
    with open(papers_path, "w", encoding="utf-8") as f:
        json.dump(papers, f, separators=(",", ":"), ensure_ascii=False)
    with open(affiliations_path, "w", encoding="utf-8") as f:
        json.dump(affiliations, f, separators=(",", ":"), ensure_ascii=False)

    # Record the DBLP file mtime for freshness checks
    with open(_mtime_file(extract_dir), "w") as f:
        f.write(str(os.path.getmtime(dblp_file)))

    sz_p = os.path.getsize(papers_path) // 1024 // 1024
    sz_a = os.path.getsize(affiliations_path) // 1024 // 1024
    logger.info(f"  → {papers_path} ({sz_p} MB)")
    logger.info(f"  → {affiliations_path} ({sz_a} MB)")

    return papers_path, affiliations_path


# ── Public lookup helpers ────────────────────────────────────────────────────


def load_papers_by_venue(repo_root: str | None = None) -> dict[str, dict[str, list[dict]]]:
    """Load the pre-extracted papers index.

    Returns dict: conf (str) → year_str (str) → list of paper dicts.
    Each paper dict has keys: title, authors, doi, dblp_key.
    """
    path = os.path.join(_extract_dir(repo_root), "papers_by_venue.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        result: dict[str, dict[str, list[dict]]] = json.load(f)
        return result


def load_affiliations(repo_root: str | None = None) -> dict[str, str]:
    """Load the pre-extracted author → affiliation mapping.

    Returns dict: author_name (str) → affiliation (str).
    """
    path = os.path.join(_extract_dir(repo_root), "affiliations.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        result: dict[str, str] = json.load(f)
        return result


# In-memory cache for find_affiliation (avoid re-reading JSON on every call)
_affiliations_cache: dict[str, str] | None = None
_affiliations_lower_cache: dict[str, str] | None = None


def find_affiliation(name, repo_root=None):
    """Look up an author's affiliation from the pre-extracted DBLP data.

    Tries exact match, then case-insensitive.  Returns the affiliation
    string or *None*.
    """
    global _affiliations_cache, _affiliations_lower_cache
    if _affiliations_cache is None:
        _affiliations_cache = load_affiliations(repo_root)
        _affiliations_lower_cache = {k.lower(): v for k, v in _affiliations_cache.items()}
    if not _affiliations_cache:
        return None
    # Exact match
    if name in _affiliations_cache:
        return _affiliations_cache[name]
    # Case-insensitive fallback
    assert _affiliations_lower_cache is not None
    return _affiliations_lower_cache.get(name.lower())


def papers_for_venue_year(conf: str, year: int, repo_root: str | None = None) -> list[dict]:
    """Convenience: return list of paper dicts for a conference/year.

    Falls back to empty list if data is not available.
    """
    data = load_papers_by_venue(repo_root)
    return data.get(conf, {}).get(str(year), [])


def paper_count_by_venue_year(repo_root=None):
    """Return dict: (conf, year_int) → paper_count."""
    data = load_papers_by_venue(repo_root)
    counts = {}
    for conf, years in data.items():
        for year_str, paper_list in years.items():
            counts[(conf, int(year_str))] = len(paper_list)
    return counts


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Pre-extract DBLP XML data to JSON lookup files")
    parser.add_argument(
        "--dblp_file",
        type=str,
        default="data/dblp/dblp.xml.gz",
        help="Path to dblp.xml.gz",
    )
    args = parser.parse_args()

    if not os.path.exists(args.dblp_file):
        logger.error(f"Error: {args.dblp_file} not found")
        logger.info("Run python -m src.utils.download_dblp first.")
        return

    extract_dblp(args.dblp_file)


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()

    main()
