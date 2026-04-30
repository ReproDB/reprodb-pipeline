"""Tests for src/generators/generate_author_profiles.

These are end-to-end tests of the ``generate_profiles`` function, which reads
several JSON inputs from a website-shaped directory and writes
``author_profiles.json``.
"""

import json
from pathlib import Path

from src.generators.authors.generate_author_profiles import generate_profiles


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _setup_inputs(root: Path, *, authors=None, ae_members=None, combined=None) -> None:
    data_dir = root / "assets" / "data"
    _write(data_dir / "authors.json", authors or [])
    _write(data_dir / "ae_members.json", ae_members or [])
    _write(data_dir / "combined_rankings.json", combined or [])


def _read_profiles(root: Path):
    return json.loads((root / "assets" / "data" / "author_profiles.json").read_text())


class TestGenerateProfiles:
    def test_empty_inputs(self, tmp_path):
        _setup_inputs(tmp_path)
        generate_profiles(str(tmp_path))
        profiles = _read_profiles(tmp_path)
        assert profiles == []

    def test_single_author_no_ae_no_combined(self, tmp_path):
        # Author below combined-ranking threshold: scores are computed inline.
        authors = [
            {
                "name": "Alice Smith",
                "affiliation": "MIT",
                "papers": [{"title": "P1", "conference": "SOSP", "year": 2023}],
                "papers_without_artifacts": [],
                "conferences": ["SOSP"],
                "years": [2023],
                "artifact_count": 1,
                "total_papers": 2,
                "artifact_pct": 50.0,
                "artifact_citations": 0,
                "badges_available": 1,
                "badges_functional": 1,
                "badges_reproducible": 0,
                "category": "systems",
            }
        ]
        _setup_inputs(tmp_path, authors=authors)
        generate_profiles(str(tmp_path))
        profiles = _read_profiles(tmp_path)
        assert len(profiles) == 1
        p = profiles[0]
        assert p["name"] == "Alice Smith"
        # artifact_score = 1+1+0 = 2; ae_score = 0
        assert p["artifact_score"] == 2
        assert p["ae_score"] == 0
        assert p["combined_score"] == 2

    def test_ae_only_member_added(self, tmp_path):
        ae = [
            {
                "name": "Bob Jones",
                "affiliation": "Stanford",
                "total_memberships": 2,
                "chair_count": 1,
                "conferences": ["SOSP"],
                "years": {"2023": 2},
                "area": "systems",
            }
        ]
        _setup_inputs(tmp_path, ae_members=ae)
        generate_profiles(str(tmp_path))
        profiles = _read_profiles(tmp_path)
        assert len(profiles) == 1
        p = profiles[0]
        assert p["name"] == "Bob Jones"
        assert p["papers"] == []
        assert p["ae_memberships"] == 2
        assert p["chair_count"] == 1
        # ae_score = 2*3 + 1*2 = 8
        assert p["ae_score"] == 8
        assert p["combined_score"] == 8

    def test_combined_rankings_overrides_scores(self, tmp_path):
        authors = [
            {
                "name": "Alice",
                "affiliation": "MIT",
                "papers": [],
                "papers_without_artifacts": [],
                "conferences": [],
                "years": [],
                "artifact_count": 0,
                "total_papers": 0,
                "artifact_pct": 0,
                "artifact_citations": 0,
                "badges_available": 0,
                "badges_functional": 0,
                "badges_reproducible": 0,
                "category": "systems",
            }
        ]
        combined = [
            {
                "name": "Alice",
                "affiliation": "MIT",
                "combined_score": 42,
                "artifact_score": 20,
                "citation_score": 0,
                "ae_score": 22,
                "rank": 5,
                "artifact_count": 7,
                "total_papers": 10,
                "artifact_pct": 70.0,
                "artifact_citations": 3,
                "badges_available": 7,
                "badges_functional": 7,
                "badges_reproducible": 6,
                "ae_memberships": 4,
                "chair_count": 1,
            }
        ]
        _setup_inputs(tmp_path, authors=authors, combined=combined)
        generate_profiles(str(tmp_path))
        profiles = _read_profiles(tmp_path)
        p = profiles[0]
        assert p["combined_score"] == 42
        assert p["rank"] == 5
        # combined_rankings values should override the per-author defaults
        assert p["artifact_count"] == 7
        assert p["badges_reproducible"] == 6
        # ae fields propagate from combined when no ae_members entry present
        assert p["ae_memberships"] == 4
        assert p["chair_count"] == 1

    def test_sorted_by_combined_score_desc(self, tmp_path):
        ae = [
            {
                "name": "Low",
                "affiliation": "U",
                "total_memberships": 1,
                "chair_count": 0,
                "conferences": [],
                "years": {},
                "area": "systems",
            },
            {
                "name": "High",
                "affiliation": "U",
                "total_memberships": 5,
                "chair_count": 2,
                "conferences": [],
                "years": {},
                "area": "systems",
            },
        ]
        _setup_inputs(tmp_path, ae_members=ae)
        generate_profiles(str(tmp_path))
        profiles = _read_profiles(tmp_path)
        assert profiles[0]["name"] == "High"
        assert profiles[1]["name"] == "Low"
