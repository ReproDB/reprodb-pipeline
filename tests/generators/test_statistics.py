"""Tests for src.generators.generate_statistics – badge counting logic."""

from src.generators.output.generate_statistics import count_badges


class TestCountBadges:
    def test_empty_list(self):
        result = count_badges([])
        assert result == {"available": 0, "functional": 0, "reproducible": 0, "reusable": 0, "replicated": 0}

    def test_single_available(self):
        artifacts = [{"badges": ["Artifacts Available"]}]
        result = count_badges(artifacts)
        assert result["available"] == 1
        assert result["functional"] == 0

    def test_multiple_badges(self):
        artifacts = [{"badges": ["Artifacts Available", "Artifacts Functional", "Results Reproduced"]}]
        result = count_badges(artifacts)
        assert result["available"] == 1
        assert result["functional"] == 1
        assert result["reproducible"] == 1

    def test_string_badges(self):
        """Badges can be a comma-separated string."""
        artifacts = [{"badges": "Artifacts Available, Artifacts Functional"}]
        result = count_badges(artifacts)
        assert result["available"] == 1
        assert result["functional"] == 1

    def test_reusable_counts_as_reproducible(self):
        artifacts = [{"badges": ["Artifacts Reusable"]}]
        result = count_badges(artifacts)
        assert result["reusable"] == 1
        assert result["reproducible"] == 1  # reusable also counted as reproducible

    def test_no_badges_key(self):
        artifacts = [{"title": "Some Paper"}]
        result = count_badges(artifacts)
        assert result["available"] == 0

    def test_empty_badges(self):
        artifacts = [{"badges": []}]
        result = count_badges(artifacts)
        assert result["available"] == 0

    def test_multiple_artifacts(self):
        artifacts = [
            {"badges": ["Artifacts Available"]},
            {"badges": ["Artifacts Available", "Artifacts Functional"]},
            {"badges": ["Results Reproduced"]},
        ]
        result = count_badges(artifacts)
        assert result["available"] == 2
        assert result["functional"] == 1
        assert result["reproducible"] == 1
