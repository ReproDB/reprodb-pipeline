"""
Shared conference metadata, area classification, and name-normalization helpers.

Every generator / enricher should import from here rather than redefining
its own ``SYSTEMS_CONFS``, ``SECURITY_CONFS``, ``_conf_area()``,
``_extract_conf_year()``, or name-cleaning functions.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# ── Conference → area classification ────────────────────────────────────────
# Conferences are discovered from the website directory structure:
#   {website_root}/content/systems/*.md  → systems conferences
#   {website_root}/content/security/*.md → security conferences
# Pages like index.md, ae_members.md, etc. are excluded.

# Non-conference pages present in each area directory.
_AREA_NON_CONF_PAGES = frozenset({"ae_members", "authors", "combined_rankings", "committee", "index", "repo_stats"})


def _scan_area_confs(website_root: str, area: str) -> frozenset[str]:
    """Scan ``{website_root}/content/{area}/`` for conference ``.md`` files."""
    area_dir = os.path.join(website_root, "content", area)
    if not os.path.isdir(area_dir):
        return frozenset()
    confs: set[str] = set()
    for fname in os.listdir(area_dir):
        if not fname.endswith(".md"):
            continue
        stem = fname[:-3]
        if stem in _AREA_NON_CONF_PAGES:
            continue
        confs.add(stem.upper())
    return frozenset(confs)


def _find_website_root() -> str | None:
    """Try to locate the website repo relative to the pipeline."""
    # Common locations when running from reprodb-pipeline/
    for candidate in ("../reprodb.github.io", "reprodb.github.io"):
        if os.path.isdir(os.path.join(candidate, "content", "systems")):
            return candidate
    return None


def discover_conferences(website_root: str | None = None) -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(systems, security)`` conference sets from the website.

    Falls back to auto-detection of the website root, then to built-in
    defaults if the directory is not found (e.g. in tests).
    Discovered conferences are merged with the built-in fallbacks so that
    known conferences are always classified even before the pipeline
    auto-generates their pages.
    """
    root = website_root or _find_website_root()
    if root and os.path.isdir(root):
        sys_confs = _scan_area_confs(root, "systems") | _FALLBACK_SYSTEMS
        sec_confs = _scan_area_confs(root, "security") | _FALLBACK_SECURITY
        if sys_confs or sec_confs:
            logger.debug(
                "Discovered conferences from %s: systems=%s, security=%s",
                root,
                sorted(sys_confs),
                sorted(sec_confs),
            )
            return sys_confs, sec_confs
    # Fallback for tests / CI where the website repo isn't available
    logger.debug("Website directory not found; using built-in conference defaults")
    return _FALLBACK_SYSTEMS, _FALLBACK_SECURITY


# Built-in fallbacks (kept in sync by CI; only used when website is absent).
_FALLBACK_SYSTEMS = frozenset({"ATC", "CAIS", "EUROSYS", "FAST", "OSDI", "SC", "SOSP"})
_FALLBACK_SECURITY = frozenset({"ACSAC", "CHES", "NDSS", "PETS", "SYSTEX", "USENIXSEC", "VEHICLESEC", "WOOT"})

# Module-level sets, populated on first import.
SYSTEMS_CONFS, SECURITY_CONFS = discover_conferences()
ALL_CONFS = SYSTEMS_CONFS | SECURITY_CONFS


def refresh_conference_sets(website_root: str | None = None) -> None:
    """Re-scan the website directory and update the module-level conference sets.

    Call this after :func:`ensure_conference_pages` has created new files.
    """
    global SYSTEMS_CONFS, SECURITY_CONFS, ALL_CONFS  # noqa: PLW0603
    SYSTEMS_CONFS, SECURITY_CONFS = discover_conferences(website_root)
    ALL_CONFS = SYSTEMS_CONFS | SECURITY_CONFS


# ── Auto-create missing conference pages ────────────────────────────────────

_CONF_PAGE_TEMPLATE = """\
---
title: "{display_name}"
permalink: /{area}/{slug}.html
conf_name: "{conf_upper}"
conf_display_name: "{display_name}"
conf_category: "{area}"
---

{{%- include conference_page.html -%}}
"""


def ensure_conference_pages(
    sys_dirs: set[str] | None = None,
    sec_dirs: set[str] | None = None,
    website_root: str | None = None,
) -> list[str]:
    """Create ``<conf>.md`` pages for conferences discovered in artifact sites.

    For each conference-year directory (e.g. ``vehiclesec2026``) found in the
    sysartifacts / secartifacts repos, extract the conference name prefix and
    ensure a corresponding ``{website_root}/{area}/{conf}.md`` page exists.
    Missing pages are created from a standard template.

    Parameters
    ----------
    sys_dirs, sec_dirs:
        Sets of directory names (e.g. ``{"osdi2024", "sosp2023"}``).
        If *None*, they are fetched from the GitHub API via
        :func:`~src.scrapers.repo_utils.get_conferences_from_prefix`.
    website_root:
        Path to the ``reprodb.github.io`` checkout.  Auto-detected if *None*.

    Returns
    -------
    list[str]
        Paths of newly created ``.md`` files.
    """
    root = website_root or _find_website_root()
    if not root or not os.path.isdir(root):
        logger.debug("Website root not found; skipping conference page creation")
        return []

    # Lazy-import to avoid pulling network dependencies at module load time.
    if sys_dirs is None or sec_dirs is None:
        from src.scrapers.repo_utils import get_conferences_from_prefix

        if sys_dirs is None:
            sys_dirs = {item["name"] for item in get_conferences_from_prefix("sys")}
        if sec_dirs is None:
            sec_dirs = {item["name"] for item in get_conferences_from_prefix("sec")}

    created: list[str] = []
    for area, dirs in [("systems", sys_dirs), ("security", sec_dirs)]:
        area_dir = os.path.join(root, "content", area)
        if not os.path.isdir(area_dir):
            continue
        existing = {fname[:-3].upper() for fname in os.listdir(area_dir) if fname.endswith(".md")}
        seen_prefixes: set[str] = set()
        for dir_name in sorted(dirs):
            conf_upper, year = parse_conf_year(dir_name)
            if year is None or conf_upper in seen_prefixes:
                continue
            seen_prefixes.add(conf_upper)
            if conf_upper in existing:
                continue
            slug = conf_upper.lower()
            display_name = CONF_DISPLAY_NAMES.get(conf_upper, conf_upper)
            page_path = os.path.join(area_dir, f"{slug}.md")
            content = _CONF_PAGE_TEMPLATE.format(
                display_name=display_name,
                area=area,
                slug=slug,
                conf_upper=conf_upper,
            )
            with open(page_path, "w") as fh:
                fh.write(content)
            logger.info("Created conference page: %s", page_path)
            created.append(page_path)

    if created:
        refresh_conference_sets(root)
    return created


# ── Conference display-name mapping ─────────────────────────────────────────
# Used by auto-generated conference pages.  Conferences not listed here
# default to the uppercase abbreviation.
CONF_DISPLAY_NAMES: dict[str, str] = {
    "ATC": "USENIX ATC",
    "CAIS": "ACM CAIS",
    "EUROSYS": "EuroSys",
    "FAST": "USENIX FAST",
    "NDSS": "NDSS",
    "SYSTEX": "SysTEX",
    "USENIXSEC": "USENIX Security",
    "VEHICLESEC": "USENIX VehicleSec",
}


# ── Name alias canonicalisation ─────────────────────────────────────────────
# Rules live in data/name_aliases.yaml — regex patterns → canonical name.

_NAME_ALIASES_PATH = Path(__file__).resolve().parents[3] / "data" / "name_aliases.yaml"


def _load_name_aliases(path: Path = _NAME_ALIASES_PATH) -> list[tuple[re.Pattern, str]]:
    """Load name alias rules from a YAML data file."""
    if not path.exists():
        return []
    with open(path) as fh:
        entries = yaml.safe_load(fh) or []
    rules: list[tuple[re.Pattern, str]] = []
    for entry in entries:
        combined = "|".join(entry["patterns"])
        rules.append((re.compile(combined, re.I), entry["canonical"]))
    return rules


_NAME_ALIASES = _load_name_aliases()


def canonicalize_name(name: str) -> str:
    """Map known name aliases to their canonical form."""
    for pat, canonical in _NAME_ALIASES:
        if pat.search(name):
            return canonical
    return name


def conf_area(conf_name: str) -> str:
    """Return ``'systems'``, ``'security'``, or ``'unknown'``.

    Accepts a bare conference name (``'OSDI'``) **or** a conf-year string
    (``'osdi2024'``).  Any casing is accepted.
    """
    upper = re.sub(r"\d+$", "", conf_name).strip().upper()
    if upper in SYSTEMS_CONFS:
        return "systems"
    if upper in SECURITY_CONFS:
        return "security"
    return "unknown"


# ── Conference-year string parsing ──────────────────────────────────────────

_CONF_YEAR_RE = re.compile(r"^([a-zA-Z]+)(\d{4})$")


def parse_conf_year(conf_year_str: str) -> tuple[str, int | None]:
    """Parse ``'osdi2024'`` → ``('OSDI', 2024)``.

    Returns ``(name_upper, year_int)`` on success, or
    ``(conf_year_str.upper(), None)`` on failure.
    """
    m = _CONF_YEAR_RE.match(conf_year_str)
    if m:
        return m.group(1).upper(), int(m.group(2))
    return conf_year_str.upper(), None


# ── Author / person name normalisation ──────────────────────────────────────


def clean_name(name: str) -> str:
    """Remove DBLP disambiguation suffixes and collapse whitespace.

    ``'Jane Doe 0001'`` → ``'Jane Doe'``
    """
    if not name:
        return ""
    name = re.sub(r"[\t\n\r]+", " ", name)
    name = re.sub(r"\s+\d{4}$", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def normalize_name(name: str, *, strip_initials: bool = False) -> str:
    """Aggressive normalisation for cross-source matching.

    Lower-cases, strips accents, removes dots, collapses whitespace.
    Applies name alias canonicalisation first so that known aliases
    (e.g. ``'Bogdan "Bo" Stoica'`` → ``'Bogdan Alexandru Stoica'``)
    collapse to the same normalised key.
    Optionally strips single-letter initials (e.g. "J. Doe" → "Doe")
    and leading underscores for ranking deduplication.
    """
    if not name:
        return ""
    name = canonicalize_name(name)
    name = name.strip().lower()
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = re.sub(r"\.", "", name)
    name = re.sub(r"\s+\d{4}$", "", name)
    if strip_initials:
        name = re.sub(r"\b[a-z]\s+", "", name)
        name = name.lstrip("_").strip()
    name = re.sub(r"\s+", " ", name).strip()
    return name


def normalize_title(title: str) -> str:
    """Normalize a paper title for fuzzy matching.

    Lower-cases, strips punctuation (keeping word chars and spaces),
    and collapses whitespace.  Used for deduplication and cross-source
    title matching.
    """
    if not title:
        return ""
    return " ".join(re.sub(r"[^\w\s]", "", title.lower()).split())


# ── Committee member cleaning ────────────────────────────────────────────────

PLACEHOLDER_NAMES = frozenset({"you?", "you", "tba", "tbd", "n/a", "", "title: organizers"})

_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_BR_TAG = re.compile(r"<br\s*/?>$")


# ── DBLP venue → conference mapping ──────────────────────────────────────────

DBLP_VENUE_MAP = {
    "EuroSys": "EUROSYS",
    "SOSP": "SOSP",
    "SC ": "SC",  # space after to avoid false matches
    "Supercomputing": "SC",
    "FAST": "FAST",
    "USENIX Security": "USENIXSEC",
    "ACSAC": "ACSAC",
    "PoPETs": "PETS",
    "Privacy Enhancing": "PETS",
    "Priv. Enhancing Technol": "PETS",  # DBLP journal abbreviation
    "CHES": "CHES",
    "IACR Trans. Cryptogr. Hardw. Embed. Syst": "CHES",  # DBLP journal form (post-2017)
    "NDSS": "NDSS",
    "WOOT": "WOOT",
    "SysTEX": "SYSTEX",
    "OSDI": "OSDI",
    "ATC": "ATC",
    "NSDI": "NSDI",
}


def venue_to_conference(booktitle: str) -> str | None:
    """Map a DBLP booktitle to our conference identifier, or None."""
    if not booktitle:
        return None
    bt = booktitle.strip()

    # Handle SC explicitly to avoid false positives (e.g., matching inside "ACSAC")
    if bt == "SC" or bt.startswith("SC "):
        return "SC"

    for pattern, conf in DBLP_VENUE_MAP.items():
        if pattern in booktitle:
            return conf
    return None


# ── Committee member cleaning ────────────────────────────────────────────────


def clean_member_name(raw_name: str) -> str | None:
    """Clean a committee member name.

    Strips markdown links, trailing ``<br>`` tags, and skips placeholder names.
    Returns the cleaned name, or ``None`` if the entry should be dropped.
    """
    name = raw_name.strip()
    link_match = _MARKDOWN_LINK.match(name)
    if link_match:
        name = link_match.group(1)
    name = _BR_TAG.sub("", name).strip()
    # Strip trailing footnote markers (e.g., "Cen Zhang¹" → "Cen Zhang")
    name = name.rstrip("¹²³⁴⁵⁶⁷⁸⁹⁰").strip()
    if name.lower() in PLACEHOLDER_NAMES or len(name) <= 1:
        return None
    if "contact" in name.lower() or "reach" in name.lower() or "mailto:" in name.lower():
        return None
    lower = name.lower()
    if "award" in lower or "distinguished" in lower or "participated in" in lower:
        return None
    # Skip footnote markers (superscript digits at start of line)
    if name.lstrip().startswith(("¹", "²", "³", "⁴", "⁵")):
        return None
    return clean_name(name)
