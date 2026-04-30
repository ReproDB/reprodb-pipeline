#!/usr/bin/env python3
"""
Generate visualizations (charts) for the research artifacts website.
Creates SVG charts that can be embedded in the Jekyll site.
"""

import argparse
import logging
from collections import defaultdict
from pathlib import Path

import matplotlib

logger = logging.getLogger(__name__)
matplotlib.use("Agg")  # Use non-interactive backend
import matplotlib.pyplot as plt

from src.utils.io.io import load_json, load_yaml


def load_data(data_dir):
    """Load YAML/JSON data files needed for chart generation."""
    data_dir = Path(data_dir)
    by_year = load_yaml(data_dir / "_data/artifacts_by_year.yml")

    by_conference = load_yaml(data_dir / "_data/artifacts_by_conference.yml")

    all_artifacts = load_json(data_dir / "assets/data/artifacts.json")

    return by_year, by_conference, all_artifacts


def _normalize_badges(badges):
    """Normalize a badges field to a list (handles comma-separated strings)."""
    if isinstance(badges, str):
        return [b.strip() for b in badges.split(",")]
    return badges or []


def _conf_label(conf):
    """Return display label, appending '(W)' suffix for workshops."""
    vtype = conf.get("venue_type", "conference")
    suffix = " (W)" if vtype == "workshop" else ""
    return conf["name"] + suffix


# ---------- Category-specific charts ----------

MARKERS = ["o", "s", "^", "D", "v", "P", "X", "*", "h", "<", ">", "p", "H", "8"]

# Hand-picked, maximally-distinct palettes so every conference is easy to tell apart.
# Each colour was chosen for hue separation + colourblind-friendliness.
_CONF_COLORS = {
    # --- systems ---
    "ATC": "#E6194B",  # red
    "EuroSys": "#3CB44B",  # green
    "FAST": "#4363D8",  # blue
    "OSDI": "#F58231",  # orange
    "SC": "#911EB4",  # purple
    "SOSP": "#42D4F4",  # cyan
    # --- security ---
    "ACSAC": "#BFEF45",  # lime
    "CHES": "#469990",  # teal
    "NDSS": "#E6BEFF",  # lavender
    "PETS": "#9A6324",  # brown
    "SysTEX": "#800000",  # maroon
    "USENIX Security": "#000075",  # navy
    "WOOT": "#AAFFC3",  # mint
}

# Fallback palette for conferences not listed above (e.g. new additions)
_EXTRA_COLORS = [
    "#FFD700",
    "#DC143C",
    "#00CED1",
    "#FF69B4",
    "#7FFF00",
    "#8B008B",
    "#FF4500",
    "#2F4F4F",
    "#DAA520",
    "#00FA9A",
    "#C71585",
    "#1E90FF",
    "#B22222",
    "#556B2F",
]


def _color_for(label, idx):
    """Return a distinct colour for a conference label."""
    # Strip workshop suffix for lookup
    base = label.replace(" (W)", "").strip()
    if base in _CONF_COLORS:
        return _CONF_COLORS[base]
    return _EXTRA_COLORS[idx % len(_EXTRA_COLORS)]


def create_category_timeline_chart(by_conference, category, output_path):
    """Create a line chart of artifacts per year for one category (systems or security)."""
    confs = [c for c in by_conference if c["category"] == category]
    if not confs:
        return

    # Collect all years and per-conf data
    conf_year_data = {}
    all_years = set()
    for conf in confs:
        label = _conf_label(conf)
        conf_year_data[label] = {}
        for yd in conf["years"]:
            conf_year_data[label][yd["year"]] = yd["total"]
            all_years.add(yd["year"])

    years = sorted(all_years)
    labels = sorted(conf_year_data.keys())

    fig, ax = plt.subplots(figsize=(max(10, len(years) * 1.0), 6))

    for i, label in enumerate(labels):
        vals = [conf_year_data[label].get(y, 0) for y in years]
        is_workshop = label.endswith("(W)")
        ax.plot(
            years,
            vals,
            label=label,
            color=_color_for(label, i),
            marker=MARKERS[i % len(MARKERS)],
            linewidth=2,
            linestyle="--" if is_workshop else "-",
            markersize=6,
        )

    ax.set_xlabel("Year", fontsize=12)
    ax.set_ylabel("Artifacts Evaluated", fontsize=12)
    title = "Systems" if category == "systems" else "Security"
    ax.set_title(f"{title} Artifacts by Conference", fontsize=14, fontweight="bold")
    ax.legend(fontsize=9, loc="upper center", bbox_to_anchor=(0.5, 1.12), ncol=min(len(labels), 4), framealpha=0.9)
    ax.grid(True, alpha=0.3)
    ax.set_xticks(years)
    fig.tight_layout()
    fig.savefig(output_path, format="svg", bbox_inches="tight")
    plt.close(fig)


def create_total_artifacts_chart(by_year, output_path):
    """Create a line chart of total artifacts per year, split by systems vs security."""
    years = [item["year"] for item in by_year]
    sys_counts = [item.get("systems", 0) for item in by_year]
    sec_counts = [item.get("security", 0) for item in by_year]
    totals = [s + c for s, c in zip(sys_counts, sec_counts)]

    fig, ax = plt.subplots(figsize=(max(10, len(years) * 0.8), 6))
    ax.plot(years, totals, marker="o", label="Total", color="#333333", linewidth=2.5)
    ax.plot(years, sys_counts, marker="s", label="Systems", color="#2E86AB", linewidth=2)
    ax.plot(years, sec_counts, marker="^", label="Security", color="#A23B72", linewidth=2)

    ax.set_xlabel("Year", fontsize=12)
    ax.set_ylabel("Number of Artifacts", fontsize=12)
    ax.set_title("Total Artifact Evaluations by Year", fontsize=14, fontweight="bold")
    ax.legend(fontsize=10, loc="upper center", bbox_to_anchor=(0.5, 1.12), ncol=3, framealpha=0.9)
    ax.grid(True, alpha=0.3)
    ax.set_xticks(years)
    fig.tight_layout()
    fig.savefig(output_path, format="svg", bbox_inches="tight")
    plt.close(fig)


# ---------- Existing charts (kept, with badge string fix) ----------


def create_badge_distribution_chart(all_artifacts, output_path):
    """Create a line chart showing badge counts over years by type"""
    year_badges = defaultdict(lambda: {"Available": 0, "Functional": 0, "Reproducible": 0, "Reusable": 0})

    for artifact in all_artifacts:
        year = artifact["year"]
        for badge in _normalize_badges(artifact.get("badges")):
            badge_lower = badge.lower()
            if "available" in badge_lower:
                year_badges[year]["Available"] += 1
            elif "functional" in badge_lower:
                year_badges[year]["Functional"] += 1
            elif "reproduc" in badge_lower or "replicated" in badge_lower:
                year_badges[year]["Reproducible"] += 1
            elif "reusable" in badge_lower:
                year_badges[year]["Reusable"] += 1

    years = sorted(year_badges.keys())
    badge_types = ["Available", "Functional", "Reproducible", "Reusable"]
    colors = ["#06A77D", "#2E86AB", "#A23B72", "#F18F01"]
    markers = ["o", "s", "^", "D"]

    fig, ax = plt.subplots(figsize=(12, 6))
    for btype, color, marker in zip(badge_types, colors, markers):
        vals = [year_badges[y][btype] for y in years]
        if any(v > 0 for v in vals):
            ax.plot(years, vals, marker=marker, label=btype, color=color, linewidth=2, markersize=6)
    ax.set_xlabel("Year", fontsize=12)
    ax.set_ylabel("Number Awarded", fontsize=12)
    ax.set_title("Artifact Badge Distribution Over Time", fontsize=14, fontweight="bold")
    ax.legend(fontsize=10, loc="upper center", bbox_to_anchor=(0.5, 1.12), ncol=4, framealpha=0.9)
    ax.grid(True, alpha=0.3)
    ax.set_xticks(years)
    fig.tight_layout()
    fig.savefig(output_path, format="svg", bbox_inches="tight")
    plt.close(fig)


def create_coverage_table(by_conference, output_path):
    """Create an SVG table showing which conference/year combos have data."""
    # Build conference→years mapping
    conf_years = {}
    all_years = set()
    for conf in by_conference:
        label = _conf_label(conf)
        yrs = {yd["year"]: yd["total"] for yd in conf["years"]}
        conf_years[label] = yrs
        all_years.update(yrs.keys())

    if not all_years:
        return

    years = sorted(all_years)
    labels = sorted(conf_years.keys())

    n_rows = len(labels)
    n_cols = len(years)
    cell_w, cell_h = 1.0, 0.6
    header_w = max(2.5, max(len(label) for label in labels) * 0.12)

    fig_w = header_w + n_cols * cell_w + 0.5
    fig_h = (n_rows + 1) * cell_h + 0.5

    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.set_xlim(0, header_w + n_cols * cell_w)
    ax.set_ylim(0, (n_rows + 1) * cell_h)
    ax.axis("off")

    # Header row
    for j, year in enumerate(years):
        x = header_w + j * cell_w + cell_w / 2
        y = n_rows * cell_h + cell_h / 2
        ax.text(x, y, str(year), ha="center", va="center", fontsize=8, fontweight="bold")

    # Data rows
    for i, label in enumerate(labels):
        row_y = (n_rows - 1 - i) * cell_h
        ax.text(header_w - 0.15, row_y + cell_h / 2, label, ha="right", va="center", fontsize=8)
        for j, year in enumerate(years):
            x = header_w + j * cell_w
            count = conf_years[label].get(year, None)
            if count is not None and count > 0:
                color = "#c6efce"
                text = str(count)
            elif count == 0:
                color = "#fff2cc"
                text = "0"
            else:
                color = "#f2f2f2"
                text = "—"
            rect = plt.Rectangle((x, row_y), cell_w, cell_h, facecolor=color, edgecolor="#cccccc", linewidth=0.5)
            ax.add_patch(rect)
            ax.text(
                x + cell_w / 2,
                row_y + cell_h / 2,
                text,
                ha="center",
                va="center",
                fontsize=7,
                color="#333333" if count else "#999999",
            )

    ax.set_title("Conference Coverage (artifact count per year)", fontsize=11, fontweight="bold", pad=10)
    fig.tight_layout()
    fig.savefig(output_path, format="svg", bbox_inches="tight")
    plt.close(fig)


# ---------- Main ----------


def generate_all_charts(data_dir):
    """Generate all charts"""
    by_year, by_conference, all_artifacts = load_data(data_dir)

    charts_dir = Path(data_dir) / "assets/charts"
    charts_dir.mkdir(parents=True, exist_ok=True)

    # Per-category timelines
    create_category_timeline_chart(by_conference, "systems", charts_dir / "systems_artifacts.svg")
    create_category_timeline_chart(by_conference, "security", charts_dir / "security_artifacts.svg")

    # Total artifacts (stacked systems + security)
    create_total_artifacts_chart(by_year, charts_dir / "total_artifacts.svg")

    # Badge chart (combines distribution + trends into one line chart)
    create_badge_distribution_chart(all_artifacts, charts_dir / "badge_distribution.svg")

    # Coverage table
    create_coverage_table(by_conference, charts_dir / "coverage_table.svg")

    logger.info(f"Charts generated in {charts_dir}")


def main():
    parser = argparse.ArgumentParser(description="Generate visualizations for research artifacts website")
    parser.add_argument("--data_dir", type=str, required=True, help="Directory containing the generated data files")

    args = parser.parse_args()

    if not Path(args.data_dir).exists():
        logger.error(f"Error: Data directory '{args.data_dir}' not found")
        logger.info("Please run generate_statistics.py first")
        return 1

    generate_all_charts(args.data_dir)
    return 0


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()

    exit(main())
