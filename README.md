# ReproDB Pipeline

[![Tests](https://github.com/ReproDB/reprodb-pipeline/actions/workflows/tests.yml/badge.svg)](https://github.com/ReproDB/reprodb-pipeline/actions/workflows/tests.yml)
[![Docs](https://github.com/ReproDB/reprodb-pipeline/actions/workflows/deploy-docs.yml/badge.svg)](https://reprodb.github.io/reprodb-pipeline/)
[![Schemas](https://github.com/ReproDB/reprodb-pipeline/actions/workflows/export-schemas.yml/badge.svg)](https://reprodb.github.io/data-schemas/)

Data pipeline that scrapes artifact evaluation results from
[sysartifacts](https://sysartifacts.github.io),
[secartifacts](https://secartifacts.github.io), and
[USENIX](https://www.usenix.org) conference pages, then produces statistics,
visualizations, and author/institution rankings for
[reprodb.github.io](https://reprodb.github.io).

**[Full Documentation](https://reprodb.github.io/reprodb-pipeline/)** ·
**[Data Schemas](https://reprodb.github.io/data-schemas/)**

---

## Quick Start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m src.orchestrator            # writes to output/staging by default
python -m src.orchestrator --deploy   # writes directly to ../reprodb.github.io
```

### Common Options

| Flag | Default | Description |
|------|---------|-------------|
| `--output_dir DIR` | `output/staging` | Where to write generated data |
| `--deploy` | off | Shorthand for `--output_dir ../reprodb.github.io` |
| `--conf_regex REGEX` | `.*20[12][0-9]` | Only process matching conferences |
| `--http_proxy URL` | — | Route HTTP traffic through a proxy |
| `--https_proxy URL` | — | Route HTTPS traffic through a proxy (auto-set from `--http_proxy`) |
| `--save-results` | off | Snapshot results into `reprodb-pipeline-results` |
| `--results_dir DIR` | `../reprodb-pipeline-results` | Where to save result snapshots |
| `--push` | off | Push results snapshot to GitHub |
| `--message TEXT` | — | Extra text for the results commit message |
| `--max-workers N` | `4` | Max parallel stages per tier |
| `--log-level LEVEL` | `info` | Set logging verbosity (`debug`, `info`, `warning`, `error`) |
| `--log-format FMT` | `text` | Log output format: `text` (human) or `json` (structured) |

---

## What the Pipeline Does

The pipeline runs **15 stages** organised in dependency tiers (see `src/stages.py`):

| # | Stage | Key script |
|---|-------|-----------|
| 1 | Download/refresh DBLP XML dump | `src/utils/download_dblp.py` |
| 1b | Extract DBLP lookup data (papers, affiliations) | `src/utils/dblp_extract.py` |
| 2 | Scrape artifact results from sysartifacts, secartifacts, USENIX | `generate_statistics.py` |
| 3 | Collect GitHub repo metadata (stars, forks, languages) | `generate_repo_stats.py` |
| 3b | Check artifact URL liveness | `generate_artifact_availability.py` |
| 3c | Compute AE participation rates against DBLP paper counts | `generate_participation_stats.py` |
| 4 | Match authors via DBLP, compute author metrics | `generate_author_stats.py` |
| 5 | Split author data into per-area files | `generate_area_authors.py` |
| 6 | Committee statistics | `generate_committee_stats.py` |
| 7 | Combined multi-source rankings | `generate_combined_rankings.py` |
| 8 | Institution-level rankings | `generate_institution_rankings.py` |
| 9 | Detailed author profiles | `generate_author_profiles.py` |
| 10 | Full-text search index | `generate_search_data.py` |
| 11 | Ranking history snapshots | `generate_ranking_history.py` |
| 12 | SVG chart generation | `generate_visualizations.py` |
| 13 | *(optional)* Paper citation counts via OpenAlex/Semantic Scholar | `generate_paper_citations_doi.py` |

> Stages 1b, 3b, 3c, and 13 are optional and will be skipped when their
> prerequisites (e.g. DBLP file) are unavailable.

---

## Source Layout

```
src/
├── scrapers/     Data collection from GitHub repos, ACM DL, USENIX, ACSAC, CHES, PETS
├── enrichers/    Affiliation enrichment (AE members, CSRankings, OpenAlex, co-author bridge)
├── generators/   Output generation — statistics, visualizations, rankings, profiles
├── models/       Pydantic data models → auto-exported as JSON Schemas
└── utils/        Shared helpers (conference normalization, caching, HTTP, I/O)
```

<details>
<summary><strong>Generators</strong> (19 scripts)</summary>

| Script | Purpose |
|--------|---------|
| `generate_statistics.py` | Scrapes artifact data, writes YAML/JSON |
| `generate_repo_stats.py` | GitHub repo metadata (stars, forks, languages) |
| `generate_participation_stats.py` | AE participation rates vs. total papers |
| `generate_artifact_citations.py` | Citation statistics (OpenAlex) |
| `generate_visualizations.py` | SVG charts (per-category, total, badges, trends) |
| `generate_author_stats.py` | Author rankings via DBLP matching |
| `generate_area_authors.py` | Per-area (systems/security) author splits |
| `generate_committee_stats.py` | Committee statistics |
| `generate_combined_rankings.py` | Combined multi-source rankings |
| `generate_institution_rankings.py` | Institution-level rankings |
| `generate_author_profiles.py` | Detailed author profile data |
| `generate_cited_artifacts_list.py` | Cited artifact lists |
| `generate_paper_index.py` | Paper title → artifact ID index |
| `generate_paper_citations_doi.py` | Paper-level citation statistics |
| `generate_search_data.py` | Full-text search data for website |
| `generate_ranking_history.py` | Historical ranking snapshots |
| `generate_artifact_availability.py` | Artifact URL liveness checks |
| `export_artifact_citations.py` | Citation data export |
| `verify_artifact_citations.py` | Citation accuracy verification |

</details>

<details>
<summary><strong>Scrapers</strong> (8 scripts)</summary>

| Script | Purpose |
|--------|---------|
| `acm_scrape.py` | ACM Digital Library badge scraping |
| `usenix_scrape.py` | USENIX conference page scraping |
| `acsac_scrape.py` | ACSAC artifact evaluation pages |
| `generate_results.py` | Generates `results.md` for sysartifacts/secartifacts |
| `repo_utils.py` | GitHub API fetching with caching |
| `parse_results_md.py` | Parses artifact YAML front-matter |
| `parse_committee_md.py` | Committee member scraping from repos |
| `scrape_committee_web.py` | Committee scraping from conference websites |

</details>

<details>
<summary><strong>Enrichers</strong> (4 scripts)</summary>

| Script | Purpose |
|--------|---------|
| `enrich_affiliations_ae_members.py` | AE committee member affiliations |
| `enrich_affiliations_csrankings.py` | CSRankings-based affiliations |
| `enrich_affiliations_openalex.py` | OpenAlex paper-title-based affiliations |
| `enrich_affiliations_author_search.py` | OpenAlex co-author bridge affiliations |

</details>

<details>
<summary><strong>Utilities & Models</strong></summary>

**Utilities** (`src/utils/`): conference normalization, DBLP extraction, HTTP helpers,
atomic caching, I/O, logging setup, author index lookups, repository testing,
committee analysis, artifact stats collection.

**Pydantic Models** (`src/models/`): formal schemas for every output format —
artifacts, authors, institutions, rankings, repo stats, search data, summaries.
CI auto-exports these as JSON Schema files to the
[data-schemas](https://github.com/reprodb/data-schemas) repo on each push.

</details>

---

## Output Files

The pipeline writes to `_data/` (YAML for Jekyll) and `assets/` (JSON + SVGs) in the
output directory:

| Directory | Key files | Format |
|-----------|-----------|--------|
| `_data/` | `summary.yml`, `artifacts_by_conference.yml`, `artifacts_by_year.yml` | YAML |
| `_data/` | `authors.yml`, `author_summary.yml`, `systems_authors.yml`, `security_authors.yml` | YAML |
| `_data/` | `repo_stats.yml`, `participation_stats.yml`, `committee_stats.yml` | YAML |
| `_data/` | `combined_summary.yml`, `coverage.yml`, `navigation.yml` | YAML |
| `assets/data/` | `artifacts.json`, `authors.json`, `summary.json`, `search_data.json` | JSON |
| `assets/data/` | `combined_rankings.json`, `institution_rankings.json`, `author_profiles.json` | JSON |
| `assets/data/` | `participation_stats.json`, `committee_stats.json`, `chair_stats.json` | JSON |
| `assets/data/` | `ae_members.json`, `ae_chairs.json` (+ `{area}_` variants) | JSON |
| `assets/charts/` | Per-conference and aggregate visualizations | SVG |

---

## Repository Layout

```
reprodb-pipeline/
├── src/                       Source code (see above)
├── tests/                     pytest test suite
├── docs/                      MkDocs documentation source
├── data/
│   ├── dblp/                  DBLP XML database (~3 GB, downloaded)
│   ├── affiliation_rules.yaml Affiliation normalization rules
│   ├── local_committees.yaml  Cached committee data for offline CI
│   ├── name_aliases.yaml      Author name alias mappings
│   └── university_country_overrides.yaml
├── logs/                      Pipeline logs and argument history
└── .github/workflows/         CI/CD (tests, monthly pipeline, schema export, docs)
```

---

## Caching

| What | Location | TTL |
|------|----------|-----|
| GitHub API responses | `.cache/` | 1 hour |
| DBLP extracted JSON | `.cache/dblp_extracted/` | Invalidated when XML changes |
| DBLP XML freshness | `Last-Modified` HTTP header | Checked each run |
| OpenAlex author bridge lookups | `.cache/author_search/` | 90 days |

The `.cache/` directory is gitignored and never committed.

## DBLP Data Policy

All DBLP lookups use the **local XML dump** (`data/dblp/dblp.xml.gz`), never the
HTTP API. `src/utils/download_dblp.py` fetches the file; `src/utils/dblp_extract.py`
parses it into JSON lookup files consumed by downstream modules.

## Conferences Tracked

Conferences are auto-discovered from the sysartifacts / secartifacts GitHub repos.
USENIX-hosted conferences are configured explicitly.

| Area | Conferences |
|------|-------------|
| Systems (sysartifacts) | EuroSys, SOSP, SC (+ OSDI, ATC when present) |
| Systems (USENIX direct) | FAST |
| Security (secartifacts) | ACSAC, CHES, NDSS, PETS, USENIX Security |
| Workshops | WOOT, SysTEX |

## Automation

A GitHub Actions workflow (`.github/workflows/update-stats.yml`) runs the full
pipeline monthly and pushes updated data to the website and results repos.
It can also be triggered manually from the Actions tab.

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `tests.yml` | Push / PR to main | Lint + tests (Python 3.10 & 3.12) |
| `update-stats.yml` | Monthly / manual | Full pipeline → website + results |
| `dblp-author-analysis.yml` | Monthly / manual | DBLP author analysis |
| `export-schemas.yml` | Push (when `src/models/` changes) | Export JSON Schemas to data-schemas |
| `deploy-docs.yml` | Push | Build & deploy MkDocs documentation |

## Related Repositories

| Repo | Purpose |
|------|---------|
| [reprodb.github.io](https://github.com/reprodb/reprodb.github.io) | Jekyll website (output target) |
| [reprodb-pipeline-results](https://github.com/reprodb/reprodb-pipeline-results) | Archived pipeline run snapshots |
| [data-schemas](https://github.com/reprodb/data-schemas) | JSON Schema definitions (auto-generated) |

## License

Apache License 2.0 — see [LICENSE](LICENSE) for details.