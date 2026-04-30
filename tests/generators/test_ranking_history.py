"""Tests for src/generators/generate_ranking_history."""

import json
from unittest.mock import patch

from src.generators.rankings.generate_ranking_history import (
    _has_snapshot,
    _snapshot_date,
    _update_history,
    generate_ranking_history,
)


class TestSnapshotDate:
    def test_format(self):
        d = _snapshot_date()
        assert len(d) == 7  # YYYY-MM
        assert d[4] == "-"


class TestHasSnapshot:
    def test_found(self):
        history = [{"date": "2025-01", "entries": {}}]
        assert _has_snapshot(history, "2025-01") is True

    def test_not_found(self):
        history = [{"date": "2025-01", "entries": {}}]
        assert _has_snapshot(history, "2025-02") is False

    def test_empty(self):
        assert _has_snapshot([], "2025-01") is False


class TestUpdateHistory:
    def test_appends(self):
        history = [{"date": "2025-01", "entries": {"Alice": {"rank": 1}}}]
        result = _update_history(history, {"Bob": {"rank": 1}}, "2025-02")
        assert len(result) == 2
        assert result[-1]["date"] == "2025-02"

    def test_replaces_existing(self):
        history = [{"date": "2025-01", "entries": {"old": {}}}]
        result = _update_history(history, {"new": {}}, "2025-01")
        assert len(result) == 1
        assert "new" in result[0]["entries"]
        assert "old" not in result[0]["entries"]

    def test_sorted_chronologically(self):
        history = [{"date": "2025-03", "entries": {}}]
        result = _update_history(history, {}, "2025-01")
        assert result[0]["date"] == "2025-01"
        assert result[1]["date"] == "2025-03"


class TestGenerateRankingHistory:
    def _setup_data(self, tmp_path):
        assets = tmp_path / "assets" / "data"
        assets.mkdir(parents=True)

        rankings = [
            {
                "name": "Alice",
                "combined_score": 50,
                "artifact_score": 30,
                "ae_score": 20,
                "total_papers": 10,
                "artifact_count": 5,
                "artifact_pct": 50.0,
                "repro_pct": 40.0,
            },
        ]
        with open(assets / "combined_rankings.json", "w") as f:
            json.dump(rankings, f)

        inst_rankings = [
            {
                "affiliation": "MIT",
                "combined_score": 80,
                "artifact_score": 50,
                "ae_score": 30,
                "total_papers": 20,
                "artifact_count": 10,
                "artifact_pct": 50.0,
                "badges_reproducible": 4,
                "author_count": 3,
            },
        ]
        with open(assets / "institution_rankings.json", "w") as f:
            json.dump(inst_rankings, f)

        return tmp_path

    @patch("src.generators.rankings.generate_ranking_history._snapshot_date", return_value="2025-06")
    def test_creates_snapshots(self, _mock_date, tmp_path):
        data_dir = self._setup_data(tmp_path)
        generate_ranking_history(str(data_dir))

        author_hist = json.loads((data_dir / "assets/data/ranking_history.json").read_text())
        assert len(author_hist) == 1
        assert author_hist[0]["date"] == "2025-06"
        assert "Alice" in author_hist[0]["entries"]

        inst_hist = json.loads((data_dir / "assets/data/institution_ranking_history.json").read_text())
        assert len(inst_hist) == 1
        assert "MIT" in inst_hist[0]["entries"]

    @patch("src.generators.rankings.generate_ranking_history._snapshot_date", return_value="2025-06")
    def test_skips_existing_without_force(self, _mock_date, tmp_path):
        data_dir = self._setup_data(tmp_path)

        # Pre-populate history
        assets = data_dir / "assets" / "data"
        existing = [{"date": "2025-06", "entries": {"Old": {"rank": 1}}}]
        (assets / "ranking_history.json").write_text(json.dumps(existing))
        (assets / "institution_ranking_history.json").write_text(json.dumps(existing))

        generate_ranking_history(str(data_dir), force=False)

        author_hist = json.loads((assets / "ranking_history.json").read_text())
        assert "Old" in author_hist[0]["entries"]  # Not replaced

    @patch("src.generators.rankings.generate_ranking_history._snapshot_date", return_value="2025-06")
    def test_force_overwrites(self, _mock_date, tmp_path):
        data_dir = self._setup_data(tmp_path)

        assets = data_dir / "assets" / "data"
        existing = [{"date": "2025-06", "entries": {"Old": {"rank": 1}}}]
        (assets / "ranking_history.json").write_text(json.dumps(existing))
        (assets / "institution_ranking_history.json").write_text(json.dumps(existing))

        generate_ranking_history(str(data_dir), force=True)

        author_hist = json.loads((assets / "ranking_history.json").read_text())
        assert "Alice" in author_hist[0]["entries"]
        assert "Old" not in author_hist[0]["entries"]
