#!/usr/bin/env python3
"""
Verify that citing papers actually cite the artifact (Zenodo/Figshare DOI),
not the published paper that happens to share the same title.

Strategy (Crossref-based):
For each citing DOI, fetch its reference list from Crossref and check if
any reference contains the artifact DOI (e.g., 10.5281/zenodo.* or
10.6084/m9.figshare.*). If yes → GENUINE. If no → FALSE_POSITIVE.

This is more reliable than OpenAlex title matching because:
- Crossref has the publisher-submitted bibliography with actual DOIs
- OpenAlex sometimes conflates paper and artifact records with same titles

Usage:
    HTTP_PROXY=http://proxy-dmz.intel.com:912 HTTPS_PROXY=http://proxy-dmz.intel.com:912 \
    python3 verify_artifact_citations.py --data_dir ../reprodb.github.io/src
"""

import argparse
import json
import logging
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from src.utils.apis.citation_apis import ARTIFACT_DOI_PREFIXES, fetch_json_urllib, is_artifact_doi
from src.utils.io.io import load_json, save_json

logger = logging.getLogger(__name__)


def fetch_crossref_references(doi: str) -> list[dict] | None:
    """Fetch the reference list for a DOI from Crossref."""
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
    try:
        data = fetch_json_urllib(url, timeout=30)
        return data.get("message", {}).get("reference", [])
    except Exception as e:
        logger.info(f"      [WARN] Crossref lookup failed for {doi}: {e}")
        return None


def references_contain_artifact_doi(refs: list[dict], artifact_doi: str) -> bool:
    """Check if any Crossref reference entry contains the artifact DOI."""
    artifact_doi_lower = artifact_doi.lower()
    for ref in refs:
        ref_str = json.dumps(ref).lower()
        if artifact_doi_lower in ref_str:
            return True
    return False


def references_contain_any_artifact_doi(refs: list[dict]) -> list[str]:
    """Return any artifact-repository DOIs found in the reference list."""
    found = []
    for ref in refs:
        # Check the DOI field
        ref_doi = ref.get("DOI", "") or ""
        if is_artifact_doi(ref_doi):
            found.append(ref_doi)
        # Also check unstructured text for Zenodo/Figshare URLs
        unstructured = ref.get("unstructured", "") or ""
        for prefix in ARTIFACT_DOI_PREFIXES:
            if prefix in unstructured.lower():
                found.append(f"[in unstructured: {prefix}...]")
                break
    return found


def normalize_author(name: str) -> str:
    """Normalize an author name for comparison (lowercase, stripped)."""
    return re.sub(r"[^a-z]+", " ", name.lower()).strip()


def get_author_surnames(name: str) -> set[str]:
    """Extract likely surnames from an author name string."""
    parts = normalize_author(name).split()
    if not parts:
        return set()
    # Return last part as surname; also return all parts for short names
    return {p for p in parts if len(p) > 1}


def fetch_zenodo_authors(record_id: str) -> list[str]:
    """Fetch author names from Zenodo API."""
    url = f"https://zenodo.org/api/records/{record_id}"
    try:
        data = fetch_json_urllib(url, timeout=20)
        creators = data.get("metadata", {}).get("creators", [])
        return [c.get("name", "") for c in creators if c.get("name")]
    except Exception as e:
        logger.info(f"      [WARN] Zenodo author lookup failed: {e}")
        return []


def fetch_figshare_authors(doi: str) -> list[str]:
    """Fetch author names from Figshare via Crossref (Figshare API is complex)."""
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
    try:
        data = fetch_json_urllib(url, timeout=20)
        authors = data.get("message", {}).get("author", [])
        return [f"{a.get('family', '')} {a.get('given', '')}".strip() for a in authors]
    except Exception:
        return []


def fetch_crossref_authors(doi: str) -> list[str]:
    """Fetch author names from Crossref for a citing paper."""
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
    try:
        data = fetch_json_urllib(url, timeout=20)
        authors = data.get("message", {}).get("author", [])
        return [f"{a.get('family', '')} {a.get('given', '')}".strip() for a in authors]
    except Exception:
        return []


def authors_overlap(artifact_authors: list[str], citing_authors: list[str]) -> tuple[bool, list[str]]:
    """Check if any authors overlap between artifact and citing paper.
    Returns (has_overlap, list_of_matching_surnames)."""
    art_surnames = set()
    for name in artifact_authors:
        art_surnames.update(get_author_surnames(name))

    cite_surnames = set()
    for name in citing_authors:
        cite_surnames.update(get_author_surnames(name))

    # Find overlapping surnames (at least 3 chars to avoid initials)
    common = {s for s in art_surnames & cite_surnames if len(s) >= 3}
    return bool(common), sorted(common)


def verify_citations(data_dir: str, output_file: str = None) -> None:
    """Verify each citing DOI actually references the artifact, not the paper."""

    citations_path = Path(data_dir) / "assets" / "data" / "artifact_citations.json"
    if not citations_path.exists():
        logger.info(f"Error: {citations_path} not found.")
        sys.exit(1)

    artifacts = load_json(citations_path)

    # Collect artifacts that have citing DOIs
    cited_artifacts = []
    for art in artifacts:
        doi = art.get("doi")
        if not doi:
            continue
        citing_oa = art.get("citing_dois_openalex", [])
        citing_s2 = art.get("citing_dois_semantic_scholar", [])
        all_citing = list(set(citing_oa + citing_s2))
        if all_citing:
            cited_artifacts.append(
                {
                    "title": art.get("title", ""),
                    "doi": doi,
                    "cited_by_count": art.get("cited_by_count") or 0,
                    "citing_dois": all_citing,
                }
            )

    if not cited_artifacts:
        logger.info("No artifacts with citing DOIs found.")
        return

    total_citing = sum(len(a["citing_dois"]) for a in cited_artifacts)
    logger.info(f"Found {len(cited_artifacts)} artifacts with {total_citing} citing DOIs to verify")
    logger.info("Strategy: check Crossref reference lists for artifact DOIs")
    logger.info("=" * 70)

    # Test Crossref connectivity
    logger.info("Testing Crossref connectivity...")
    try:
        test_url = "https://api.crossref.org/works?rows=0"
        fetch_json_urllib(test_url, timeout=10)
        logger.info("  Crossref API reachable ✓")
    except Exception as e:
        logger.info(f"  [ERROR] Crossref API unreachable: {e}")
        logger.info("  Make sure HTTP_PROXY/HTTPS_PROXY are set if behind a proxy.")
        sys.exit(1)

    results = []
    total_genuine = 0
    total_false_positive = 0
    total_self_citation = 0
    total_unknown = 0

    for art in cited_artifacts:
        artifact_doi = art["doi"]
        artifact_title = art["title"]
        citing_dois = art["citing_dois"]

        logger.info(f"\n{'─' * 70}")
        logger.info(f"Artifact: {artifact_title[:70]}")
        logger.info(f"  DOI: {artifact_doi}")
        logger.info(f"  Citing DOIs to check: {len(citing_dois)}")

        # Pre-fetch artifact authors (Zenodo or Figshare)
        artifact_authors = []
        if artifact_doi.lower().startswith("10.5281/zenodo."):
            record_id = artifact_doi.split("zenodo.")[1]
            artifact_authors = fetch_zenodo_authors(record_id)
            time.sleep(0.3)
        elif artifact_doi.lower().startswith("10.6084/m9.figshare."):
            artifact_authors = fetch_figshare_authors(artifact_doi)
            time.sleep(0.3)
        if artifact_authors:
            logger.info(
                f"  Artifact authors: {', '.join(a[:30] for a in artifact_authors[:5])}{'...' if len(artifact_authors) > 5 else ''}"
            )
        else:
            logger.info("  Artifact authors: (could not fetch)")

        for cdoi in citing_dois:
            logger.info(f"\n  Checking: {cdoi}")

            # Special case: citing DOI is the artifact DOI itself
            if cdoi.lower() == artifact_doi.lower():
                logger.info("    → SELF_CITATION: citing DOI is the artifact itself")
                results.append(
                    {
                        "artifact_doi": artifact_doi,
                        "artifact_title": artifact_title,
                        "citing_doi": cdoi,
                        "verdict": "SELF_CITATION",
                        "reason": "citing DOI matches artifact DOI",
                    }
                )
                total_self_citation += 1
                continue

            # Fetch Crossref reference list and metadata
            refs = fetch_crossref_references(cdoi)
            time.sleep(0.5)  # Rate limit courtesy

            if refs is None:
                logger.info("    → UNKNOWN: Crossref lookup failed")
                results.append(
                    {
                        "artifact_doi": artifact_doi,
                        "artifact_title": artifact_title,
                        "citing_doi": cdoi,
                        "verdict": "UNKNOWN",
                        "reason": "Crossref lookup failed",
                    }
                )
                total_unknown += 1
                continue

            logger.info(f"    Crossref refs: {len(refs)} total")

            # Check if the exact artifact DOI appears in references
            has_exact = references_contain_artifact_doi(refs, artifact_doi)

            # Also check if ANY artifact-repo DOI appears (zenodo/figshare)
            any_artifact_dois = references_contain_any_artifact_doi(refs)

            if has_exact:
                # Artifact DOI is in the reference list — now check for self-citation
                # by comparing authors of artifact and citing paper
                citing_authors = fetch_crossref_authors(cdoi)
                time.sleep(0.3)
                has_overlap, common = (
                    authors_overlap(artifact_authors, citing_authors) if artifact_authors else (False, [])
                )

                if has_overlap:
                    logger.info(f"    → SELF_CITATION: refs contain artifact DOI, but authors overlap: {common}")
                    verdict = "SELF_CITATION"
                    reason = f"paper cites its own artifact (shared authors: {', '.join(common)})"
                    total_self_citation += 1
                else:
                    logger.info(f"    → GENUINE: reference list contains artifact DOI {artifact_doi}")
                    verdict = "GENUINE"
                    reason = f"Crossref references contain artifact DOI {artifact_doi}"
                    total_genuine += 1
            elif any_artifact_dois:
                logger.info(f"    → GENUINE_SIMILAR: has artifact DOIs {any_artifact_dois} but not exact match")
                verdict = "GENUINE_SIMILAR"
                reason = f"references contain other artifact DOIs: {any_artifact_dois}"
                total_genuine += 1
            else:
                logger.info(f"    → FALSE_POSITIVE: no artifact DOIs found in {len(refs)} references")
                verdict = "FALSE_POSITIVE"
                reason = f"no artifact-repository DOIs in {len(refs)} Crossref references"
                total_false_positive += 1

            results.append(
                {
                    "artifact_doi": artifact_doi,
                    "artifact_title": artifact_title,
                    "citing_doi": cdoi,
                    "crossref_ref_count": len(refs),
                    "has_exact_artifact_doi": has_exact,
                    "artifact_dois_found": any_artifact_dois,
                    "verdict": verdict,
                    "reason": reason,
                }
            )

    # Summary
    logger.info("\n" + "=" * 70)
    logger.info("VERIFICATION SUMMARY")
    logger.info("=" * 70)
    logger.info(f"Total citing DOIs verified: {len(results)}")
    logger.info(f"  GENUINE (artifact DOI in refs):  {total_genuine}")
    logger.info(f"  FALSE_POSITIVE (no artifact DOI):{total_false_positive}")
    logger.info(f"  SELF_CITATION:                   {total_self_citation}")
    logger.info(f"  UNKNOWN (lookup failed):         {total_unknown}")

    # Group by verdict
    logger.info("\n--- GENUINE artifact citations ---")
    for r in results:
        if r["verdict"] in ("GENUINE", "GENUINE_SIMILAR"):
            logger.info(f"  {r['artifact_doi']} ← {r['citing_doi']}")

    logger.info("\n--- FALSE POSITIVES (citing paper, not artifact) ---")
    for r in results:
        if r["verdict"] == "FALSE_POSITIVE":
            logger.info(f"  {r['artifact_doi']} ← {r['citing_doi']}")
            logger.info(f"    ({r['reason']})")

    logger.info("\n--- SELF CITATIONS ---")
    for r in results:
        if r["verdict"] == "SELF_CITATION":
            logger.info(f"  {r['artifact_doi']} ← {r['citing_doi']}")

    logger.info("\n--- UNKNOWN ---")
    for r in results:
        if r["verdict"] == "UNKNOWN":
            logger.info(f"  {r['artifact_doi']} ← {r['citing_doi']}")
            logger.info(f"    ({r['reason']})")

    # Write results to file
    if output_file:
        save_json(output_file, results)
        logger.info(f"\nDetailed results written to: {output_file}")

    # Write verified citing dois file (genuine only)
    verified_output = output_file.replace(".json", "_genuine.txt") if output_file else None
    if verified_output:
        from collections import defaultdict

        genuine_map = defaultdict(list)
        for r in results:
            if r["verdict"] in ("GENUINE", "GENUINE_SIMILAR"):
                genuine_map[r["artifact_doi"]].append(r["citing_doi"])
        with open(verified_output, "w") as f:
            for adoi, cdois in sorted(genuine_map.items()):
                f.write(f"{adoi}: {json.dumps(sorted(cdois))}\n")
        logger.info(f"Verified genuine citations written to: {verified_output}")

    # Write false positives list
    fp_output = output_file.replace(".json", "_false_positives.txt") if output_file else None
    if fp_output:
        from collections import defaultdict

        fp_map = defaultdict(list)
        for r in results:
            if r["verdict"] == "FALSE_POSITIVE":
                fp_map[r["artifact_doi"]].append(r["citing_doi"])
        if fp_map:
            with open(fp_output, "w") as f:
                for adoi, cdois in sorted(fp_map.items()):
                    f.write(f"{adoi}: {json.dumps(sorted(cdois))}\n")
            logger.info(f"False positives written to: {fp_output}")


def main():
    parser = argparse.ArgumentParser(description="Verify artifact citations")
    parser.add_argument("--data_dir", required=True, help="Website data directory")
    parser.add_argument("--output", "-o", default="citation_verification.json", help="Output JSON file")
    args = parser.parse_args()
    verify_citations(args.data_dir, args.output)


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()

    main()
