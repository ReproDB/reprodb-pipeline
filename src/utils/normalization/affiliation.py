"""Shared affiliation normalization utilities.

Provides ``normalize_affiliation()`` (and its helpers) which apply
regex rules from ``data/affiliation_rules.yaml``, strip sub-unit details,
and collapse trailing location tokens so that different spellings of the
same institution map to one canonical name.
"""

import re
from pathlib import Path

from src.utils.io.io import load_yaml

# ── Affiliation normalization rules (loaded from YAML) ────────────────────────

_RULES_PATH = Path(__file__).resolve().parents[3] / "data" / "affiliation_rules.yaml"


def _load_affiliation_rules(path: Path = _RULES_PATH) -> list[tuple[re.Pattern, str]]:
    """Load affiliation regex rules from a YAML data file."""
    entries = load_yaml(path)
    rules: list[tuple[re.Pattern, str]] = []
    for entry in entries:
        combined = "|".join(entry["patterns"])
        rules.append((re.compile(combined, re.I), entry["canonical"]))
    return rules


_AFFILIATION_RULES = _load_affiliation_rules()


def _strip_trailing_location(aff: str) -> str:
    """Strip everything after the first comma.

    The explicit YAML rules and the university-regex steps above handle all
    cases where a comma is semantically meaningful (e.g. "University of
    California, Berkeley").  For everything else the first comma-separated
    segment is the core institution name; the rest is location, department,
    or sub-unit detail that should be dropped.
    """
    idx = aff.find(",")
    if idx <= 0:
        return aff
    core = aff[:idx].strip()
    return core if core else aff


def normalize_affiliation(affiliation: str) -> str:
    """Normalize affiliation string to a canonical form.

    Strategy:
    1. Try each regex rule; if one matches, return its canonical name.
    2. Strip sub-units after a university name core.
    3. Strip trailing location tokens (city, state, country).
    """
    if not affiliation:
        return ""
    aff = affiliation.strip()
    if not aff:
        return ""

    # 1. Apply explicit pattern rules
    for pat, canonical in _AFFILIATION_RULES:
        if pat.search(aff):
            return canonical

    # 2. Generic: strip sub-unit details after the university name
    #    Match "<Name> University" or "University of <Name>" then drop the rest.
    m = re.match(
        r"((?:The\s+)?(?:University|Universität|Universidade|Università|Université)"
        r"\s+(?:of\s+)?[\w''\-\–\—.]+(?:\s+[\w''\-\–\—.]+){0,4}?)"
        r"\s*[,(]",
        aff,
        re.IGNORECASE,
    )
    if m:
        core = m.group(1).strip()
        # Keep it only if the core is long enough to be meaningful
        if len(core) > 10:
            return core

    # Same for "<Name> University" pattern (e.g. "Tsinghua University, ...")
    m = re.match(
        r"([\w''\-\–\—.]+(?:\s+[\w''\-\–\—.]+){0,4}?\s+"
        r"(?:University|Institute|Universität|Polytechnic|College))"
        r"\s*[,(]",
        aff,
        re.IGNORECASE,
    )
    if m:
        core = m.group(1).strip()
        if len(core) > 10:
            return core

    # 3. Strip trailing location (city, state, country) from any affiliation
    return _strip_trailing_location(aff)
