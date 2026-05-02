"""Post-pipeline invariant assertions.

These are "data contract" checks that run after the pipeline finishes.
They verify global properties of the output that should never be violated
regardless of what the scrapers find.  Violations signal a bug in the
pipeline, not a change in the underlying data.

Usage::

    # Run all invariants against pipeline output
    python -m src.invariants --output_dir output/staging

    # Also importable for use in tests or the orchestrator
    from src.invariants import check_all
    violations = check_all(Path("output/staging"))
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


class Violation:
    """A single invariant violation."""

    def __init__(self, file: str, check: str, message: str, *, severity: str = "error"):
        self.file = file
        self.check = check
        self.message = message
        self.severity = severity  # "error" or "warning"

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.file}: {self.check} — {self.message}"


def _load_json(path: Path) -> list | dict | None:
    if not path.is_file():
        return None
    result: list | dict = json.loads(path.read_text())
    return result


def _load_yaml(path: Path) -> dict | list | None:
    if not path.is_file():
        return None
    result: dict | list = yaml.safe_load(path.read_text())
    return result


# ── Individual invariant checks ──────────────────────────────────────────────


def check_combined_rankings(output_dir: Path) -> list[Violation]:
    """Validate combined_rankings.json invariants."""
    vs: list[Violation] = []
    path = output_dir / "assets" / "data" / "combined_rankings.json"
    data = _load_json(path)
    if data is None:
        vs.append(Violation(str(path), "exists", "File not found"))
        return vs
    if not isinstance(data, list):
        vs.append(Violation(str(path), "type", "Expected a list"))
        return vs

    fname = "combined_rankings.json"
    if len(data) == 0:
        vs.append(Violation(fname, "non_empty", "Zero records"))
        return vs

    names_seen: set[str] = set()
    for i, r in enumerate(data):
        name = r.get("name", "")

        # No empty names
        if not name or not name.strip():
            vs.append(Violation(fname, "name_nonempty", f"Record {i} has empty name"))

        # No duplicate names
        if name in names_seen:
            vs.append(Violation(fname, "name_unique", f"Duplicate name: {name!r}"))
        names_seen.add(name)

        # Scores non-negative
        for field in ("combined_score", "artifact_score", "ae_score", "citation_score"):
            val = r.get(field)
            if val is not None and val < 0:
                vs.append(Violation(fname, "score_nonneg", f"{name}: {field}={val} < 0"))

        # combined_score = artifact_score + ae_score (+ citation_score if present)
        cs = r.get("combined_score", 0)
        ars = r.get("artifact_score", 0)
        aes = r.get("ae_score", 0)
        cis = r.get("citation_score", 0)
        expected = ars + aes + cis
        if abs(cs - expected) > 0.01:
            vs.append(
                Violation(
                    fname,
                    "score_sum",
                    f"{name}: combined_score={cs} != artifact_score({ars}) + ae_score({aes}) + citation_score({cis}) = {expected}",
                )
            )

        # Badge counts ≤ artifact_count
        artifacts = r.get("artifact_count", 0)
        for badge in ("badges_available", "badges_functional", "badges_reproducible"):
            bv = r.get(badge, 0)
            if bv > artifacts:
                vs.append(Violation(fname, "badge_le_artifacts", f"{name}: {badge}={bv} > artifact_count={artifacts}"))

        # ae_memberships non-negative
        ae = r.get("ae_memberships", 0)
        if ae < 0:
            vs.append(Violation(fname, "ae_nonneg", f"{name}: ae_memberships={ae} < 0"))

        # artifact_pct in [0, 100]
        rate = r.get("artifact_pct")
        if rate is not None and (rate < 0 or rate > 100):
            vs.append(Violation(fname, "rate_range", f"{name}: artifact_pct={rate} outside [0,100]"))

    return vs


def check_institution_rankings(output_dir: Path) -> list[Violation]:
    """Validate institution_rankings.json invariants."""
    vs: list[Violation] = []
    path = output_dir / "assets" / "data" / "institution_rankings.json"
    data = _load_json(path)
    if data is None:
        vs.append(Violation(str(path), "exists", "File not found"))
        return vs
    if not isinstance(data, list):
        vs.append(Violation(str(path), "type", "Expected a list"))
        return vs

    fname = "institution_rankings.json"
    if len(data) == 0:
        vs.append(Violation(fname, "non_empty", "Zero records"))
        return vs

    names_seen: set[str] = set()
    for _i, r in enumerate(data):
        name = r.get("affiliation") or r.get("institution") or r.get("name", "")
        if not name:
            # Empty affiliation is common (unknown institution) — warn, don't error
            continue

        if name in names_seen:
            vs.append(Violation(fname, "name_unique", f"Duplicate institution: {name!r}"))
        names_seen.add(name)

        for field in ("total_score", "total_artifacts", "total_ae_memberships"):
            val = r.get(field)
            if val is not None and val < 0:
                vs.append(Violation(fname, "score_nonneg", f"{name}: {field}={val} < 0"))

    return vs


def check_search_data(output_dir: Path) -> list[Violation]:
    """Validate search_data.json invariants."""
    vs: list[Violation] = []
    path = output_dir / "assets" / "data" / "search_data.json"
    data = _load_json(path)
    if data is None:
        vs.append(Violation(str(path), "exists", "File not found"))
        return vs

    fname = "search_data.json"
    if not isinstance(data, list) or len(data) == 0:
        vs.append(Violation(fname, "non_empty", "Expected non-empty list"))
        return vs

    for i, r in enumerate(data):
        if not r.get("title"):
            vs.append(Violation(fname, "title_nonempty", f"Record {i} has empty title"))
        if not r.get("conference"):
            vs.append(Violation(fname, "conference_nonempty", f"Record {i} has empty conference"))
        year = r.get("year")
        if year is not None and (year < 2000 or year > 2030):
            vs.append(Violation(fname, "year_range", f"Record {i}: year={year} outside [2000, 2030]"))

    return vs


def check_summary(output_dir: Path) -> list[Violation]:
    """Validate _data/summary.yml invariants."""
    from src.models import SCHEMA_VERSION

    vs: list[Violation] = []
    path = output_dir / "_data" / "summary.yml"
    data = _load_yaml(path)
    if data is None:
        vs.append(Violation(str(path), "exists", "File not found"))
        return vs

    fname = "summary.yml"
    if not isinstance(data, dict):
        vs.append(Violation(fname, "type", "Expected a dict"))
        return vs

    # Must have at least some known keys
    for key in ("total_artifacts", "total_conferences"):
        if key not in data:
            vs.append(Violation(fname, "required_key", f"Missing key: {key}"))

    ta = data.get("total_artifacts", 0)
    tc = data.get("total_conferences", 0)
    if ta < 0:
        vs.append(Violation(fname, "nonneg", f"total_artifacts={ta} < 0"))
    if tc < 0:
        vs.append(Violation(fname, "nonneg", f"total_conferences={tc} < 0"))

    # Schema version must match pipeline constant
    sv = data.get("schema_version")
    if sv is None:
        vs.append(Violation(fname, "schema_version_present", "Missing schema_version field"))
    elif str(sv) != SCHEMA_VERSION:
        vs.append(
            Violation(
                fname,
                "schema_version_match",
                f"schema_version={sv!r} != SCHEMA_VERSION={SCHEMA_VERSION!r} (pipeline constant)",
            )
        )

    return vs


def check_cross_file_consistency(output_dir: Path) -> list[Violation]:
    """Cross-file consistency: search_data count ≈ artifacts, authors in rankings exist, etc."""
    vs: list[Violation] = []

    # search_data records should equal the artifact count in summary
    summary = _load_yaml(output_dir / "_data" / "summary.yml")
    search = _load_json(output_dir / "assets" / "data" / "search_data.json")
    if summary and search and isinstance(search, list) and isinstance(summary, dict):
        expected = summary.get("total_artifacts", 0)
        actual = len(search)
        if expected > 0 and abs(actual - expected) > expected * 0.1:
            vs.append(
                Violation(
                    "cross-file",
                    "search_data_count",
                    f"search_data has {actual} records but summary.total_artifacts={expected} (>10% drift)",
                )
            )

    # combined_rankings authors should all have non-empty name
    # (already checked per-file; this is for cross-file: every author in
    # combined_rankings should appear in author_profiles if profiles exist)
    rankings = _load_json(output_dir / "assets" / "data" / "combined_rankings.json")
    profiles = _load_json(output_dir / "assets" / "data" / "author_profiles.json")
    if rankings and profiles and isinstance(rankings, list) and isinstance(profiles, list):
        profile_names = {p.get("name", "").strip().lower() for p in profiles}
        missing = 0
        for r in rankings:
            name = r.get("name", "").strip().lower()
            if name and name not in profile_names:
                missing += 1
        if missing > 0:
            vs.append(
                Violation(
                    "cross-file",
                    "rankings_in_profiles",
                    f"{missing} ranked authors not found in author_profiles.json",
                    severity="warning",
                )
            )

    return vs


# ── Aggregate runner ─────────────────────────────────────────────────────────


ALL_CHECKS = [
    check_combined_rankings,
    check_institution_rankings,
    check_search_data,
    check_summary,
    check_cross_file_consistency,
]


def check_all(output_dir: Path) -> list[Violation]:
    """Run all invariant checks and return collected violations."""
    violations: list[Violation] = []
    for check_fn in ALL_CHECKS:
        try:
            violations.extend(check_fn(output_dir))
        except Exception as exc:
            violations.append(
                Violation(
                    "runner",
                    check_fn.__name__,
                    f"Check raised an exception: {exc}",
                )
            )
    return violations


# ── CLI ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Run post-pipeline invariant checks")
    parser.add_argument("--output_dir", required=True, help="Pipeline output directory")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    violations = check_all(Path(args.output_dir))

    errors = [v for v in violations if v.severity == "error"]
    warnings = [v for v in violations if v.severity == "warning"]

    for v in warnings:
        logger.warning("⚠ %s", v)
    for v in errors:
        logger.error("✗ %s", v)

    if errors:
        logger.error("\n%d error(s), %d warning(s)", len(errors), len(warnings))
        sys.exit(1)

    logger.info("✓ All invariants passed (%d warnings)", len(warnings))
    sys.exit(0)


if __name__ == "__main__":
    main()
