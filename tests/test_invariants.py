"""Tests for post-pipeline invariant assertions."""

import json
from pathlib import Path

import pytest

from src.invariants import (
    check_all,
    check_combined_rankings,
    check_cross_file_consistency,
    check_institution_rankings,
    check_search_data,
    check_summary,
)


@pytest.fixture
def output_dir(tmp_path):
    """Create a minimal output directory layout."""
    (tmp_path / "assets" / "data").mkdir(parents=True)
    (tmp_path / "_data").mkdir(parents=True)
    return tmp_path


def _write_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2))


def _write_yaml(path: Path, text: str):
    path.write_text(text)


class TestCombinedRankings:
    def test_valid_record_passes(self, output_dir):
        _write_json(
            output_dir / "assets/data/combined_rankings.json",
            [
                {
                    "name": "Alice",
                    "combined_score": 15,
                    "artifact_score": 12,
                    "ae_score": 3,
                    "citation_score": 0,
                    "artifact_count": 5,
                    "ae_memberships": 1,
                    "badges_available": 5,
                    "badges_functional": 4,
                    "badges_reproducible": 3,
                    "artifact_pct": 50.0,
                }
            ],
        )
        vs = check_combined_rankings(output_dir)
        errors = [v for v in vs if v.severity == "error"]
        assert errors == []

    def test_negative_score_flagged(self, output_dir):
        _write_json(
            output_dir / "assets/data/combined_rankings.json",
            [
                {
                    "name": "Bad",
                    "combined_score": -5,
                    "artifact_score": -5,
                    "ae_score": 0,
                    "citation_score": 0,
                    "artifact_count": 0,
                }
            ],
        )
        vs = check_combined_rankings(output_dir)
        assert any("score_nonneg" in v.check for v in vs)

    def test_score_sum_mismatch_flagged(self, output_dir):
        _write_json(
            output_dir / "assets/data/combined_rankings.json",
            [
                {
                    "name": "Mismatch",
                    "combined_score": 100,
                    "artifact_score": 10,
                    "ae_score": 5,
                    "citation_score": 0,
                    "artifact_count": 10,
                }
            ],
        )
        vs = check_combined_rankings(output_dir)
        assert any("score_sum" in v.check for v in vs)

    def test_badge_exceeds_artifacts_flagged(self, output_dir):
        _write_json(
            output_dir / "assets/data/combined_rankings.json",
            [
                {
                    "name": "TooManyBadges",
                    "combined_score": 5,
                    "artifact_score": 5,
                    "ae_score": 0,
                    "citation_score": 0,
                    "artifact_count": 2,
                    "badges_available": 2,
                    "badges_functional": 3,
                    "badges_reproducible": 0,
                }
            ],
        )
        vs = check_combined_rankings(output_dir)
        assert any("badge_le_artifacts" in v.check for v in vs)

    def test_empty_name_flagged(self, output_dir):
        _write_json(
            output_dir / "assets/data/combined_rankings.json",
            [{"name": "", "combined_score": 0, "artifact_score": 0, "ae_score": 0, "citation_score": 0}],
        )
        vs = check_combined_rankings(output_dir)
        assert any("name_nonempty" in v.check for v in vs)

    def test_duplicate_name_flagged(self, output_dir):
        rec = {
            "name": "Dupe",
            "combined_score": 5,
            "artifact_score": 5,
            "ae_score": 0,
            "citation_score": 0,
            "artifact_count": 5,
        }
        _write_json(output_dir / "assets/data/combined_rankings.json", [rec, rec])
        vs = check_combined_rankings(output_dir)
        assert any("name_unique" in v.check for v in vs)

    def test_artifact_pct_out_of_range(self, output_dir):
        _write_json(
            output_dir / "assets/data/combined_rankings.json",
            [
                {
                    "name": "HighRate",
                    "combined_score": 0,
                    "artifact_score": 0,
                    "ae_score": 0,
                    "citation_score": 0,
                    "artifact_count": 0,
                    "artifact_pct": 150.0,
                }
            ],
        )
        vs = check_combined_rankings(output_dir)
        assert any("rate_range" in v.check for v in vs)

    def test_missing_file_flagged(self, output_dir):
        vs = check_combined_rankings(output_dir)
        assert any("exists" in v.check for v in vs)


class TestInstitutionRankings:
    def test_valid_passes(self, output_dir):
        _write_json(
            output_dir / "assets/data/institution_rankings.json",
            [{"institution": "MIT", "total_score": 100, "total_artifacts": 50, "total_ae_memberships": 10}],
        )
        vs = check_institution_rankings(output_dir)
        errors = [v for v in vs if v.severity == "error"]
        assert errors == []

    def test_negative_score_flagged(self, output_dir):
        _write_json(output_dir / "assets/data/institution_rankings.json", [{"institution": "Bad", "total_score": -1}])
        vs = check_institution_rankings(output_dir)
        assert any("score_nonneg" in v.check for v in vs)


class TestSearchData:
    def test_valid_passes(self, output_dir):
        _write_json(
            output_dir / "assets/data/search_data.json", [{"title": "Paper A", "conference": "SOSP", "year": 2023}]
        )
        vs = check_search_data(output_dir)
        assert vs == []

    def test_empty_title_flagged(self, output_dir):
        _write_json(output_dir / "assets/data/search_data.json", [{"title": "", "conference": "SOSP", "year": 2023}])
        vs = check_search_data(output_dir)
        assert any("title_nonempty" in v.check for v in vs)

    def test_year_out_of_range(self, output_dir):
        _write_json(output_dir / "assets/data/search_data.json", [{"title": "Old", "conference": "SOSP", "year": 1990}])
        vs = check_search_data(output_dir)
        assert any("year_range" in v.check for v in vs)


class TestSummary:
    def test_valid_passes(self, output_dir):
        _write_yaml(
            output_dir / "_data/summary.yml", "schema_version: 0.1.4\ntotal_artifacts: 100\ntotal_conferences: 10\n"
        )
        vs = check_summary(output_dir)
        errors = [v for v in vs if v.severity == "error"]
        assert errors == []

    def test_missing_key_flagged(self, output_dir):
        _write_yaml(output_dir / "_data/summary.yml", "schema_version: 0.1.4\ntotal_artifacts: 100\n")
        vs = check_summary(output_dir)
        assert any("required_key" in v.check for v in vs)

    def test_negative_artifacts_flagged(self, output_dir):
        _write_yaml(
            output_dir / "_data/summary.yml", "schema_version: 0.1.4\ntotal_artifacts: -1\ntotal_conferences: 10\n"
        )
        vs = check_summary(output_dir)
        assert any("nonneg" in v.check for v in vs)

    def test_missing_schema_version_flagged(self, output_dir):
        _write_yaml(output_dir / "_data/summary.yml", "total_artifacts: 100\ntotal_conferences: 10\n")
        vs = check_summary(output_dir)
        assert any("schema_version_present" in v.check for v in vs)

    def test_wrong_schema_version_flagged(self, output_dir):
        _write_yaml(
            output_dir / "_data/summary.yml", "schema_version: 0.0.0\ntotal_artifacts: 100\ntotal_conferences: 10\n"
        )
        vs = check_summary(output_dir)
        assert any("schema_version_match" in v.check for v in vs)


class TestCrossFileConsistency:
    def test_search_data_drift_flagged(self, output_dir):
        _write_yaml(
            output_dir / "_data/summary.yml", "schema_version: 0.1.4\ntotal_artifacts: 100\ntotal_conferences: 10\n"
        )
        _write_json(
            output_dir / "assets/data/search_data.json",
            [{"title": f"P{i}", "conference": "A", "year": 2023} for i in range(50)],
        )
        vs = check_cross_file_consistency(output_dir)
        assert any("search_data_count" in v.check for v in vs)

    def test_no_drift_passes(self, output_dir):
        _write_yaml(
            output_dir / "_data/summary.yml", "schema_version: 0.1.4\ntotal_artifacts: 5\ntotal_conferences: 1\n"
        )
        _write_json(
            output_dir / "assets/data/search_data.json",
            [{"title": f"P{i}", "conference": "A", "year": 2023} for i in range(5)],
        )
        vs = check_cross_file_consistency(output_dir)
        errors = [v for v in vs if v.severity == "error"]
        assert errors == []


class TestCheckAll:
    def test_check_all_runs_without_crash(self, output_dir):
        """Even with no files, check_all should not raise — just report violations."""
        vs = check_all(output_dir)
        assert isinstance(vs, list)

    def test_check_all_on_valid_output(self, output_dir):
        _write_yaml(
            output_dir / "_data/summary.yml", "schema_version: 0.1.4\ntotal_artifacts: 1\ntotal_conferences: 1\n"
        )
        _write_json(
            output_dir / "assets/data/combined_rankings.json",
            [
                {
                    "name": "A",
                    "combined_score": 5,
                    "artifact_score": 5,
                    "ae_score": 0,
                    "citation_score": 0,
                    "artifact_count": 5,
                    "ae_memberships": 0,
                    "badges_available": 5,
                    "badges_functional": 3,
                    "badges_reproducible": 1,
                    "artifact_pct": 50.0,
                }
            ],
        )
        _write_json(
            output_dir / "assets/data/institution_rankings.json",
            [{"institution": "MIT", "total_score": 5, "total_artifacts": 3, "total_ae_memberships": 1}],
        )
        _write_json(
            output_dir / "assets/data/search_data.json", [{"title": "Paper", "conference": "SOSP", "year": 2023}]
        )
        vs = check_all(output_dir)
        errors = [v for v in vs if v.severity == "error"]
        assert errors == []
