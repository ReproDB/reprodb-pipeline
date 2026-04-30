"""Stage dependency graph for the pipeline.

Each stage declares its module, the files it produces, and which stages
must run before it (``depends_on``).  The orchestrator uses this graph to
determine valid execution order and to parallelise independent stages.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Stage:
    """A single pipeline stage."""

    name: str
    module: str
    description: str
    depends_on: tuple[str, ...] = ()
    optional: bool = False
    outputs: tuple[str, ...] = ()
    #: Files this stage reads. When non-empty the stage participates in the
    #: content-hash skip cache (see :mod:`src.cache`).  Paths are resolved
    #: relative to the pipeline ``output_dir`` unless absolute.
    inputs: tuple[str, ...] = ()
    #: Maximum age (seconds) of a cached result before the stage must re-run,
    #: even when input hashes have not changed.  ``None`` means the cache
    #: never expires by time alone (content-hash only).  Useful for stages
    #: that fetch live data (GitHub stats, citation counts) whose per-URL
    #: caches have their own TTL.
    ttl: int | None = None


# ── Stage definitions ────────────────────────────────────────────────────────
# Order mirrors the orchestrator stage sequence; depends_on encodes the *data* dependencies.

STAGES: tuple[Stage, ...] = (
    Stage(
        name="dblp_extract",
        module="src.utils.apis.dblp_extract",
        description="Extract DBLP lookup data",
        optional=True,
        outputs=("data/dblp_lookup.json",),
    ),
    Stage(
        name="statistics",
        module="src.generators.output.generate_statistics",
        description="Generate statistics (sysartifacts + secartifacts + USENIX)",
        outputs=(
            "_data/summary.yml",
            "_data/artifacts_by_conference.yml",
            "_data/artifacts_by_year.yml",
            "assets/data/artifacts.json",
            "assets/data/summary.json",
        ),
    ),
    Stage(
        name="repo_stats",
        module="src.generators.repository.generate_repo_stats",
        description="Generate repository statistics (stars, forks, etc.)",
        depends_on=("statistics",),
        optional=True,
        # Hits the GitHub API for every artifact — by far the slowest stage.
        # The cache lets re-runs without input changes finish in <1s.
        # Per-URL disk caches expire after 30 days (CACHE_TTL_STATS), so
        # the stage-level TTL matches to ensure fresh API data.
        inputs=("_data/all_results_cache.yml",),
        ttl=30 * 86400,  # 30 days — re-run when per-URL caches expire
        outputs=(
            "_data/repo_stats.yml",
            "_build/repo_stats_detail.json",
            "assets/data/repo_stats_yearly.json",
            "_build/repo_stats_history.json",
        ),
    ),
    Stage(
        name="artifact_availability",
        module="src.generators.repository.generate_artifact_availability",
        description="Check artifact URL liveness",
        depends_on=("statistics",),
        optional=True,
        # HEAD-checks every artifact URL — slow, fully idempotent given
        # identical input artifacts.
        inputs=("assets/data/artifacts.json",),
        outputs=("assets/data/artifact_availability.json",),
        ttl=30 * 86400,  # 30 days — re-check URL liveness periodically
    ),
    Stage(
        name="participation_stats",
        module="src.generators.repository.generate_participation_stats",
        description="Generate AE participation statistics (DBLP paper counts)",
        depends_on=("statistics", "dblp_extract"),
        optional=True,
        outputs=("assets/data/participation_stats.json",),
    ),
    Stage(
        name="author_stats",
        module="src.generators.authors.generate_author_stats",
        description="Generate author statistics",
        depends_on=("statistics", "dblp_extract"),
        outputs=(
            "assets/data/authors.json",
            "_build/paper_authors_map.json",
            "assets/data/papers.json",
        ),
    ),
    Stage(
        name="area_authors",
        module="src.generators.authors.generate_area_authors",
        description="Generate per-area author data",
        depends_on=("author_stats", "statistics"),
        outputs=("assets/data/systems_authors.yml", "assets/data/security_authors.yml"),
    ),
    Stage(
        name="committee_stats",
        module="src.generators.generate_committee_stats",
        description="Generate committee statistics",
        depends_on=("statistics",),
        optional=True,
        outputs=("_data/committee_stats.yml", "assets/data/committee_stats.json"),
    ),
    Stage(
        name="combined_rankings",
        module="src.generators.rankings.generate_combined_rankings",
        description="Generate combined author rankings",
        depends_on=("author_stats", "committee_stats"),
        outputs=(
            "assets/data/combined_rankings.json",
            "assets/data/systems_combined_rankings.json",
            "assets/data/security_combined_rankings.json",
        ),
    ),
    Stage(
        name="institution_rankings",
        module="src.generators.rankings.generate_institution_rankings",
        description="Generate institution rankings",
        depends_on=("combined_rankings",),
        outputs=(
            "assets/data/institution_rankings.json",
            "assets/data/systems_institution_rankings.json",
            "assets/data/security_institution_rankings.json",
        ),
    ),
    Stage(
        name="author_profiles",
        module="src.generators.authors.generate_author_profiles",
        description="Generate author profile pages",
        depends_on=("author_stats", "combined_rankings"),
        outputs=("author/",),
    ),
    Stage(
        name="search_data",
        module="src.generators.output.generate_search_data",
        description="Generate search index",
        depends_on=("statistics", "author_stats"),
        outputs=("assets/data/search_data.json",),
    ),
    Stage(
        name="ranking_history",
        module="src.generators.rankings.generate_ranking_history",
        description="Update ranking history snapshots",
        depends_on=("combined_rankings", "institution_rankings"),
        outputs=("assets/data/ranking_history.json",),
    ),
    Stage(
        name="visualizations",
        module="src.generators.output.generate_visualizations",
        description="Generate SVG charts",
        depends_on=("statistics", "author_stats", "committee_stats"),
        outputs=("assets/charts/",),
    ),
    Stage(
        name="paper_citations_doi",
        module="src.generators.citations.generate_paper_citations_doi",
        description="Collect paper citation counts via OpenAlex/Semantic Scholar",
        depends_on=("statistics",),
        optional=True,
        outputs=("_build/paper_citations.json", "_build/citation_history.json"),
    ),
    Stage(
        name="baseline_citations",
        module="src.generators.citations.generate_baseline_citations",
        description="Collect citation counts for non-AE papers (baseline comparison)",
        depends_on=("statistics", "dblp_extract"),
        optional=True,
        outputs=("_build/baseline_citations.json",),
    ),
)

STAGE_MAP: dict[str, Stage] = {s.name: s for s in STAGES}


def topological_order(stages: tuple[Stage, ...] = STAGES) -> list[Stage]:
    """Return stages in a valid execution order (Kahn's algorithm)."""
    stage_map = {s.name: s for s in stages}
    in_degree = {s.name: 0 for s in stages}
    dependants: dict[str, list[str]] = {s.name: [] for s in stages}

    for s in stages:
        for dep in s.depends_on:
            if dep in stage_map:
                in_degree[s.name] += 1
                dependants[dep].append(s.name)

    queue = [name for name, deg in in_degree.items() if deg == 0]
    order: list[Stage] = []

    while queue:
        # Sort for deterministic output
        queue.sort()
        name = queue.pop(0)
        order.append(stage_map[name])
        for child in dependants[name]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    if len(order) != len(stages):
        executed = {s.name for s in order}
        missing = {s.name for s in stages} - executed
        msg = f"Cycle detected in stage dependencies: {missing}"
        raise ValueError(msg)

    return order


def parallel_groups(stages: tuple[Stage, ...] = STAGES) -> list[list[Stage]]:
    """Return stages grouped into parallel tiers.

    Each tier contains stages whose dependencies are fully satisfied by
    earlier tiers, so all stages within a tier can execute concurrently.
    """
    stage_map = {s.name: s for s in stages}
    in_degree = {s.name: 0 for s in stages}
    dependants: dict[str, list[str]] = {s.name: [] for s in stages}

    for s in stages:
        for dep in s.depends_on:
            if dep in stage_map:
                in_degree[s.name] += 1
                dependants[dep].append(s.name)

    groups: list[list[Stage]] = []
    current = sorted([name for name, deg in in_degree.items() if deg == 0])

    while current:
        groups.append([stage_map[name] for name in current])
        next_tier: list[str] = []
        for name in current:
            for child in dependants[name]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    next_tier.append(child)
        current = sorted(next_tier)

    return groups
