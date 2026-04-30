"""Tests for src/generators/generate_institution_rankings."""

import pytest

from src.generators.rankings.generate_institution_rankings import aggregate_by_institution


def _person(**overrides):
    """Build a minimal combined-ranking person dict."""
    defaults = {
        "name": "Alice",
        "affiliation": "Massachusetts Institute of Technology",
        "combined_score": 10,
        "artifact_score": 6,
        "artifact_citations": 5,
        "citation_score": 2,
        "ae_score": 4,
        "artifact_count": 2,
        "badges_functional": 1,
        "badges_reproducible": 1,
        "ae_memberships": 1,
        "chair_count": 0,
        "total_papers": 5,
        "conferences": ["OSDI"],
        "years": {"2023": 2},
    }
    defaults.update(overrides)
    return defaults


class TestAggregateByInstitution:
    def test_single_person(self):
        result = aggregate_by_institution([_person()])
        assert len(result) == 1
        assert result[0]["affiliation"] == "Massachusetts Institute of Technology"
        assert result[0]["combined_score"] == 10

    def test_aggregates_same_institution(self):
        data = [
            _person(name="Alice", combined_score=10, artifact_count=2, total_papers=5),
            _person(name="Bob", combined_score=8, artifact_count=1, total_papers=3),
        ]
        result = aggregate_by_institution(data)
        assert len(result) == 1
        assert result[0]["combined_score"] == 18
        assert result[0]["author_count"] == 2
        assert result[0]["artifact_count"] == 3
        assert result[0]["total_papers"] == 8

    def test_different_institutions(self):
        data = [
            _person(name="Alice", combined_score=10),
            _person(name="Bob", affiliation="Stanford University", combined_score=8),
        ]
        result = aggregate_by_institution(data)
        assert len(result) == 2

    def test_sorted_by_score_desc(self):
        data = [
            _person(name="Alice", combined_score=5),
            _person(name="Bob", affiliation="Stanford University", combined_score=15),
        ]
        result = aggregate_by_institution(data)
        assert result[0]["affiliation"] == "Stanford University"
        assert result[1]["affiliation"] == "Massachusetts Institute of Technology"

    def test_unknown_affiliation_filtered(self):
        data = [_person(affiliation="Unknown", combined_score=10)]
        result = aggregate_by_institution(data)
        assert all(r["affiliation"] != "Unknown" for r in result)

    def test_empty_affiliation_grouped_as_unknown(self):
        data = [_person(affiliation="", combined_score=10)]
        result = aggregate_by_institution(data)
        assert all(r["affiliation"] != "" for r in result)

    def test_below_threshold_excluded(self):
        data = [
            _person(
                affiliation="TinyU",
                combined_score=2,
                artifact_count=0,
                badges_functional=0,
                badges_reproducible=0,
                total_papers=1,
            )
        ]
        result = aggregate_by_institution(data)
        assert len(result) == 0

    def test_ae_ratio_balanced(self):
        data = [_person(artifact_score=10, ae_score=10, combined_score=20)]
        result = aggregate_by_institution(data)
        assert result[0]["ae_ratio"] == 1.0
        assert result[0]["role"] == "Balanced"

    def test_ae_ratio_producer(self):
        data = [_person(artifact_score=30, ae_score=5, combined_score=35)]
        result = aggregate_by_institution(data)
        assert result[0]["ae_ratio"] > 2.0
        assert result[0]["role"] == "Producer"

    def test_ae_ratio_consumer(self):
        data = [_person(artifact_score=2, ae_score=20, combined_score=22)]
        result = aggregate_by_institution(data)
        assert result[0]["ae_ratio"] < 0.5
        assert result[0]["role"] == "Consumer"

    def test_ae_ratio_none_when_ae_zero(self):
        data = [_person(artifact_score=10, ae_score=0, combined_score=10)]
        result = aggregate_by_institution(data)
        assert result[0]["ae_ratio"] is None
        assert result[0]["role"] == "Producer"

    def test_artifact_pct(self):
        data = [_person(artifact_count=5, total_papers=10, combined_score=10)]
        result = aggregate_by_institution(data)
        assert result[0]["artifact_pct"] == 50.0

    def test_conferences_merged(self):
        data = [
            _person(name="Alice", conferences=["OSDI", "SOSP"]),
            _person(name="Bob", conferences=["SOSP", "FAST"]),
        ]
        result = aggregate_by_institution(data)
        assert set(result[0]["conferences"]) == {"OSDI", "SOSP", "FAST"}

    def test_invariant_artifacts_gt_papers_raises(self):
        data = [_person(artifact_count=10, total_papers=5, combined_score=10)]
        with pytest.raises(ValueError, match="Invariant violation"):
            aggregate_by_institution(data)

    def test_invariant_badges_gt_artifacts_raises(self):
        data = [_person(artifact_count=2, badges_reproducible=5, combined_score=10)]
        with pytest.raises(ValueError, match="Invariant violation"):
            aggregate_by_institution(data)
