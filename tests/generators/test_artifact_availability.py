"""Tests for src/generators/generate_artifact_availability.

Network calls (``check_artifact_exists``) are mocked via ``monkeypatch``;
we focus on the pure helpers ``_detect_platform``, ``generate_availability``
(with stubbed liveness checks) and ``build_summary``.
"""

from src.generators.repository import generate_artifact_availability as gaa


class TestDetectPlatform:
    def test_github(self):
        assert gaa._detect_platform("https://github.com/foo/bar") == "GitHub"

    def test_gitlab(self):
        assert gaa._detect_platform("https://gitlab.com/foo/bar") == "GitLab"

    def test_bitbucket(self):
        assert gaa._detect_platform("https://bitbucket.org/foo/bar") == "Bitbucket"

    def test_zenodo_explicit_host(self):
        assert gaa._detect_platform("https://zenodo.org/record/123") == "Zenodo"

    def test_zenodo_via_doi_prefix(self):
        assert gaa._detect_platform("https://doi.org/10.5281/zenodo.123") == "Zenodo"

    def test_figshare_via_doi_prefix(self):
        assert gaa._detect_platform("https://doi.org/10.6084/foo") == "Figshare"

    def test_doi_other(self):
        assert gaa._detect_platform("https://doi.org/10.1145/foo") == "DOI-other"

    def test_other(self):
        assert gaa._detect_platform("https://example.com/foo") == "other"

    def test_empty_string(self):
        assert gaa._detect_platform("") == "unknown"

    def test_non_string(self):
        assert gaa._detect_platform(None) == "unknown"
        assert gaa._detect_platform(123) == "unknown"


class TestGenerateAvailability:
    def _stub_check(self, monkeypatch, *, accessible=True):
        """Stub ``check_artifact_exists`` to mark every URL with the given accessibility."""

        def fake_check(results, url_keys):
            for artifacts in results.values():
                for a in artifacts:
                    for url_key in url_keys:
                        if a.get(url_key):
                            a[f"{url_key}_exists"] = accessible
            return results, {}, []

        monkeypatch.setattr(gaa, "check_artifact_exists", fake_check)

    def test_basic_record_shape(self, monkeypatch):
        self._stub_check(monkeypatch, accessible=True)
        results = {
            "sosp2023": [
                {
                    "title": "P1",
                    "repository_url": "https://github.com/x/y",
                    "artifact_url": "https://zenodo.org/record/1",
                }
            ]
        }
        records, counts, failed = gaa.generate_availability(results)
        # Two URL keys per artifact → two records
        assert len(records) == 2
        urls = {r["url"] for r in records}
        assert urls == {"https://github.com/x/y", "https://zenodo.org/record/1"}
        for r in records:
            assert r["conference"].lower() == "sosp"
            assert r["year"] == 2023
            assert r["accessible"] is True

    def test_empty_url_skipped(self, monkeypatch):
        self._stub_check(monkeypatch, accessible=True)
        results = {"sosp2023": [{"title": "P1", "repository_url": "", "artifact_url": "  "}]}
        records, _, _ = gaa.generate_availability(results)
        assert records == []

    def test_list_url_takes_first(self, monkeypatch):
        self._stub_check(monkeypatch, accessible=False)
        results = {
            "sosp2023": [
                {
                    "title": "P1",
                    "repository_url": ["https://github.com/x/y", "https://example.com/z"],
                }
            ]
        }
        records, _, _ = gaa.generate_availability(results)
        assert len(records) == 1
        assert records[0]["url"] == "https://github.com/x/y"
        assert records[0]["accessible"] is False

    def test_unparseable_conf_year_skipped(self, monkeypatch):
        self._stub_check(monkeypatch, accessible=True)
        results = {"badformat": [{"title": "P1", "repository_url": "https://github.com/x/y"}]}
        records, _, _ = gaa.generate_availability(results)
        assert records == []


class TestBuildSummary:
    def _records(self):
        return [
            {
                "conference": "sosp",
                "year": 2023,
                "area": "systems",
                "title": "A",
                "url_key": "repository_url",
                "url": "https://github.com/x/y",
                "platform": "GitHub",
                "accessible": True,
            },
            {
                "conference": "sosp",
                "year": 2023,
                "area": "systems",
                "title": "B",
                "url_key": "repository_url",
                "url": "https://github.com/x/z",
                "platform": "GitHub",
                "accessible": False,
            },
            {
                "conference": "ndss",
                "year": 2024,
                "area": "security",
                "title": "C",
                "url_key": "artifact_url",
                "url": "https://zenodo.org/r/1",
                "platform": "Zenodo",
                "accessible": True,
            },
        ]

    def test_overall_counts(self):
        s = gaa.build_summary(self._records())
        assert s["total_urls"] == 3
        assert s["accessible_urls"] == 2
        assert s["accessibility_pct"] == round(100 * 2 / 3, 1)

    def test_by_platform_breakdown(self):
        s = gaa.build_summary(self._records())
        gh = s["by_platform"]["GitHub"]
        assert gh["total"] == 2
        assert gh["accessible"] == 1
        assert gh["pct"] == 50.0
        zen = s["by_platform"]["Zenodo"]
        assert zen["total"] == 1
        assert zen["accessible"] == 1
        assert zen["pct"] == 100.0

    def test_by_area(self):
        s = gaa.build_summary(self._records())
        assert s["by_area"]["systems"]["total"] == 2
        assert s["by_area"]["security"]["total"] == 1

    def test_empty_records(self):
        s = gaa.build_summary([])
        assert s["total_urls"] == 0
        assert s["accessible_urls"] == 0
        assert s["accessibility_pct"] == 0
        assert s["by_platform"] == {}
        assert s["by_area"] == {}

    def test_year_keys_are_strings(self):
        s = gaa.build_summary(self._records())
        # Years are coerced to strings in the summary so JSON keys are stable.
        assert set(s["by_year"].keys()) == {"2023", "2024"}
