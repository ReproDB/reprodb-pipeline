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


class TestPerConferenceInstitutionRankings:
    """``{conf}_institution_rankings.json`` is produced for every ``{conf}_combined_rankings.json``."""

    def test_main_generates_file_per_conference(self, tmp_path, monkeypatch):
        from src.generators.rankings import generate_institution_rankings as gir
        from tests.conftest import read_json, write_json

        data_dir = tmp_path / "assets" / "data"
        person = _person(conferences=["SP"], years={"2026": 2})
        # Overall + per-area files plus two conferences, one of them brand new.
        for name in ("combined", "systems_combined", "security_combined", "sp_combined", "cais_combined"):
            write_json(str(data_dir / f"{name}_rankings.json"), [person])

        # Keep the test hermetic: the real classifier downloads a university list.
        monkeypatch.setattr(gir, "_build_classifier", lambda: (None, {}))
        monkeypatch.setattr(gir, "_classify_country", lambda *_a, **_k: ("United States", "US"))
        monkeypatch.setattr("sys.argv", ["prog", "--data_dir", str(tmp_path)])
        gir.main()

        for conf in ("sp", "cais"):
            ranked = read_json(str(data_dir / f"{conf}_institution_rankings.json"))
            assert [r["affiliation"] for r in ranked] == ["Massachusetts Institute of Technology"]
            assert "country_code" in ranked[0]
        # Area-level outputs are unchanged and not mistaken for conferences.
        assert (data_dir / "security_institution_rankings.json").exists()
        assert not (data_dir / "systems_combined_institution_rankings.json").exists()
        assert not (data_dir / "combined_institution_rankings.json").exists()
        assert not (data_dir / "security_institution_rankings_institution_rankings.json").exists()
