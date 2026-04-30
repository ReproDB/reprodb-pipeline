"""Tests for generate_statistics — pure helper functions."""

from src.generators.output.generate_statistics import (
    _build_artifact_entry,
    _collect_artifact_urls,
    count_badges,
)


class TestCountBadges:
    def test_empty_list(self):
        assert count_badges([]) == {
            "available": 0,
            "functional": 0,
            "reproducible": 0,
            "reusable": 0,
            "replicated": 0,
        }

    def test_counts_available(self):
        arts = [{"badges": ["Artifacts Available"]}]
        result = count_badges(arts)
        assert result["available"] == 1

    def test_counts_functional(self):
        arts = [{"badges": ["Artifacts Functional"]}]
        assert count_badges(arts)["functional"] == 1

    def test_counts_reproducible_from_reusable(self):
        arts = [{"badges": ["Results Reusable"]}]
        result = count_badges(arts)
        assert result["reproducible"] == 1
        assert result["reusable"] == 1

    def test_counts_replicated(self):
        arts = [{"badges": ["Results Replicated"]}]
        result = count_badges(arts)
        assert result["reproducible"] == 1

    def test_comma_separated_string(self):
        arts = [{"badges": "Available, Functional"}]
        result = count_badges(arts)
        assert result["available"] == 1
        assert result["functional"] == 1

    def test_no_badges_key(self):
        arts = [{"title": "test"}]
        result = count_badges(arts)
        assert result["available"] == 0

    def test_empty_badges(self):
        arts = [{"badges": []}]
        result = count_badges(arts)
        assert result["available"] == 0

    def test_multiple_artifacts(self):
        arts = [
            {"badges": ["Available"]},
            {"badges": ["Available", "Functional"]},
            {"badges": ["Available", "Reproducible"]},
        ]
        result = count_badges(arts)
        assert result["available"] == 3
        assert result["functional"] == 1
        assert result["reproducible"] == 1


class TestCollectArtifactUrls:
    def test_empty_artifact(self):
        assert _collect_artifact_urls({}) == []

    def test_github_url(self):
        art = {"repository_url": "https://github.com/user/repo"}
        assert _collect_artifact_urls(art) == ["https://github.com/user/repo"]

    def test_multiple_url_fields(self):
        art = {
            "repository_url": "https://github.com/u/r",
            "artifact_url": "https://zenodo.org/record/123",
        }
        urls = _collect_artifact_urls(art)
        assert "https://github.com/u/r" in urls
        assert "https://zenodo.org/record/123" in urls

    def test_deduplicates(self):
        art = {
            "repository_url": "https://github.com/u/r",
            "github_url": "https://github.com/u/r",
        }
        assert len(_collect_artifact_urls(art)) == 1

    def test_doi_normalization(self):
        art = {"artifact_doi": "10.5281/zenodo.12345"}
        urls = _collect_artifact_urls(art)
        assert urls == ["https://doi.org/10.5281/zenodo.12345"]

    def test_doi_already_url(self):
        art = {"artifact_doi": "https://doi.org/10.5281/zenodo.12345"}
        urls = _collect_artifact_urls(art)
        assert urls == ["https://doi.org/10.5281/zenodo.12345"]

    def test_list_artifact_urls(self):
        art = {"artifact_urls": ["https://a.com", "https://b.com"]}
        assert _collect_artifact_urls(art) == ["https://a.com", "https://b.com"]

    def test_skips_empty_strings(self):
        art = {"repository_url": "", "artifact_url": ""}
        assert _collect_artifact_urls(art) == []


class TestBuildArtifactEntry:
    def test_minimal_entry(self):
        art = {"title": "My Paper", "badges": ["Available"]}
        entry = _build_artifact_entry(art, "sosp", "systems", 2024, "sosp2024", {}, {})
        assert entry["conference"] == "SOSP"
        assert entry["category"] == "systems"
        assert entry["year"] == 2024
        assert entry["title"] == "My Paper"
        assert entry["badges"] == ["Available"]

    def test_paper_url_from_doi(self):
        art = {"title": "T", "badges": [], "doi": "10.1145/12345"}
        entry = _build_artifact_entry(art, "ccs", "security", 2023, "ccs2023", {}, {})
        assert entry.get("paper_url") == "https://doi.org/10.1145/12345"

    def test_appendix_url_sec(self):
        art = {"title": "T", "badges": [], "appendix_url": "results.html"}
        entry = _build_artifact_entry(art, "usenix", "security", 2023, "usenixsec2023", {"usenixsec2023": {}}, {})
        assert entry["appendix_url"] == "https://secartifacts.github.io/usenixsec2023/results.html"

    def test_appendix_url_sys(self):
        art = {"title": "T", "badges": [], "appendix_url": "results.html"}
        entry = _build_artifact_entry(art, "osdi", "systems", 2023, "osdi2023", {}, {"osdi2023": {}})
        assert entry["appendix_url"] == "https://sysartifacts.github.io/osdi2023/results.html"

    def test_appendix_url_absolute_unchanged(self):
        art = {"title": "T", "badges": [], "appendix_url": "https://example.com/results"}
        entry = _build_artifact_entry(art, "sosp", "systems", 2023, "sosp2023", {}, {})
        assert entry["appendix_url"] == "https://example.com/results"

    def test_badges_string_split(self):
        art = {"title": "T", "badges": "Available, Functional"}
        entry = _build_artifact_entry(art, "ccs", "security", 2024, "ccs2024", {}, {})
        assert entry["badges"] == ["Available", "Functional"]

    def test_unknown_title_default(self):
        art = {"badges": []}
        entry = _build_artifact_entry(art, "x", "y", 2024, "x2024", {}, {})
        assert entry["title"] == "Unknown"
