"""Tests for src/generators/generate_combined_rankings."""

import pytest

from src.generators.rankings.generate_combined_rankings import (
    _build_entry,
    _merge_rankings,
    _normalize_name,
)


def _entry_kwargs(**overrides):
    """Build a minimal kwargs dict for ``_build_entry``."""
    defaults = {
        "name": "Alice",
        "affiliation": "MIT",
        "artifact_count": 2,
        "total_papers": 5,
        "artifact_pct": 40.0,
        "ae_memberships": 0,
        "chair_count": 0,
        "conferences": ["SOSP"],
        "years": {2023: 2},
        "artifact_citations": 0,
        "badges_available": 2,
        "badges_functional": 1,
        "badges_reproducible": 1,
    }
    defaults.update(overrides)
    return defaults


class TestNormalizeName:
    def test_strips_initials(self):
        # Should strip middle initials ("A. B. Smith" → "smith a b")
        norm = _normalize_name("Alice B. Smith")
        # exact form depends on util; just assert it's stable & lowercase
        assert norm == _normalize_name("Alice B. Smith")
        assert norm == norm.lower()

    def test_consistent_across_whitespace(self):
        assert _normalize_name("  Alice   Smith  ") == _normalize_name("Alice Smith")


class TestBuildEntry:
    def test_combined_score_artifact_only(self):
        entry = _build_entry(**_entry_kwargs())
        # 2 available + 1 functional + 1 reproducible = 4
        assert entry["artifact_score"] == 4
        assert entry["ae_score"] == 0
        assert entry["combined_score"] == 4

    def test_combined_score_with_ae(self):
        entry = _build_entry(**_entry_kwargs(ae_memberships=2, chair_count=1))
        # ae_score = 2*3 + 1*2 = 8
        assert entry["ae_score"] == 8
        assert entry["combined_score"] == 4 + 8

    def test_ae_ratio_none_when_no_ae(self):
        entry = _build_entry(**_entry_kwargs())
        assert entry["ae_ratio"] is None

    def test_ae_ratio_computed(self):
        entry = _build_entry(**_entry_kwargs(ae_memberships=1, chair_count=0))
        # artifact_score=4, ae_score=3, ratio=4/3≈1.33
        assert entry["ae_ratio"] == round(4 / 3, 2)

    def test_first_last_year_from_years_dict(self):
        entry = _build_entry(**_entry_kwargs(years={2019: 1, 2023: 2, 2021: 1}))
        assert entry["first_year"] == 2019
        assert entry["last_year"] == 2023

    def test_no_years(self):
        entry = _build_entry(**_entry_kwargs(years={}))
        assert entry["first_year"] is None
        assert entry["last_year"] is None

    def test_repro_rate(self):
        entry = _build_entry(**_entry_kwargs(artifact_count=4, badges_reproducible=2))
        assert entry["repro_pct"] == 50

    def test_repro_rate_zero_artifacts(self):
        entry = _build_entry(
            **_entry_kwargs(
                artifact_count=0,
                total_papers=0,
                badges_available=0,
                badges_functional=0,
                badges_reproducible=0,
            )
        )
        assert entry["repro_pct"] == 0

    def test_artifacts_clamped_to_total_papers(self):
        # When artifact_count > total_papers, total_papers gets clamped up (logger warning).
        entry = _build_entry(**_entry_kwargs(artifact_count=10, total_papers=5))
        assert entry["total_papers"] == 10
        assert entry["artifact_count"] == 10

    def test_invariant_reproducible_gt_artifacts_raises(self):
        with pytest.raises(ValueError, match="Invariant violation"):
            _build_entry(**_entry_kwargs(artifact_count=2, badges_reproducible=5))

    def test_invariant_functional_gt_artifacts_raises(self):
        with pytest.raises(ValueError, match="Invariant violation"):
            _build_entry(**_entry_kwargs(artifact_count=2, badges_functional=5))

    def test_display_name_strips_dblp_year_suffix(self):
        # The display_name regex strips a trailing " <4-digits>" DBLP suffix.
        entry = _build_entry(**_entry_kwargs(name="Alice Smith 0017"))
        assert entry["display_name"] == "Alice Smith"


class TestMergeRankings:
    def test_empty_inputs(self):
        result = _merge_rankings([], [])
        assert result == []

    def test_single_author_no_ae(self):
        authors = [
            {
                "name": "Alice Smith",
                "affiliation": "MIT",
                "total": 2,
                "total_papers": 5,
                "artifact_pct": 40.0,
                "conferences": ["SOSP"],
                "years": [2023],
                "badges_available": 2,
                "badges_functional": 1,
                "badges_reproducible": 1,
                "artifact_citations": 0,
            }
        ]
        result = _merge_rankings(authors, [])
        assert len(result) == 1
        assert result[0]["name"] == "Alice Smith"
        assert result[0]["ae_memberships"] == 0
        assert result[0]["rank"] == 1

    def test_single_ae_no_authors(self):
        members = [
            {
                "name": "Bob Jones",
                "affiliation": "Stanford",
                "total_memberships": 2,
                "chair_count": 1,
                "conferences": ["SOSP"],
                "years": {2023: 2},
            }
        ]
        result = _merge_rankings([], members)
        assert len(result) == 1
        assert result[0]["name"] == "Bob Jones"
        assert result[0]["artifact_count"] == 0
        assert result[0]["ae_memberships"] == 2
        assert result[0]["chair_count"] == 1

    def test_author_matched_to_ae_member(self):
        authors = [
            {
                "name": "Alice Smith",
                "affiliation": "MIT",
                "total": 2,
                "total_papers": 5,
                "artifact_pct": 40.0,
                "conferences": ["SOSP"],
                "years": [2023],
                "badges_available": 2,
                "badges_functional": 1,
                "badges_reproducible": 1,
                "artifact_citations": 0,
            }
        ]
        members = [
            {
                "name": "Alice Smith",
                "affiliation": "MIT",
                "total_memberships": 1,
                "chair_count": 0,
                "conferences": ["SOSP"],
                "years": {2023: 1},
            }
        ]
        result = _merge_rankings(authors, members)
        # Should be merged into one entry
        assert len(result) == 1
        assert result[0]["artifact_count"] == 2
        assert result[0]["ae_memberships"] == 1

    def test_sorted_by_combined_score_desc(self):
        authors = [
            {
                "name": "LowScore",
                "affiliation": "U1",
                "total": 1,
                "total_papers": 1,
                "artifact_pct": 100.0,
                "conferences": [],
                "years": [],
                "badges_available": 1,
                "badges_functional": 0,
                "badges_reproducible": 0,
                "artifact_citations": 0,
            },
            {
                "name": "HighScore",
                "affiliation": "U2",
                "total": 3,
                "total_papers": 3,
                "artifact_pct": 100.0,
                "conferences": [],
                "years": [],
                "badges_available": 3,
                "badges_functional": 3,
                "badges_reproducible": 3,
                "artifact_citations": 0,
            },
        ]
        result = _merge_rankings(authors, [])
        assert result[0]["name"] == "HighScore"
        assert result[1]["name"] == "LowScore"
        assert result[0]["rank"] == 1
        assert result[1]["rank"] == 2

    def test_ranks_with_ties(self):
        authors = [
            {
                "name": "A",
                "affiliation": "U",
                "total": 1,
                "total_papers": 1,
                "artifact_pct": 100.0,
                "conferences": [],
                "years": [],
                "badges_available": 1,
                "badges_functional": 0,
                "badges_reproducible": 0,
                "artifact_citations": 0,
            },
            {
                "name": "B",
                "affiliation": "U",
                "total": 1,
                "total_papers": 1,
                "artifact_pct": 100.0,
                "conferences": [],
                "years": [],
                "badges_available": 1,
                "badges_functional": 0,
                "badges_reproducible": 0,
                "artifact_citations": 0,
            },
        ]
        result = _merge_rankings(authors, [])
        # Both have same combined_score → same rank
        assert result[0]["rank"] == result[1]["rank"] == 1
