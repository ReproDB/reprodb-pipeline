"""Tests for the ArtiFinder-Data loader (``src.scrapers.artifinder``)."""

from __future__ import annotations

import json

import pytest

import src.scrapers.artifinder as af

# ── Inline fixture data mimicking the ArtiFinder-Data repo layout ────────────
_TOP = [
    {"name": "ndss", "type": "dir"},
    {"name": "usenix", "type": "dir"},
    {"name": "readme.txt", "type": "file"},  # ignored (not a mapped venue dir)
    {"name": "twitter", "type": "dir"},  # unmapped venue → skipped
]
_NDSS_FILES = [
    {"name": "2016.yaml", "type": "file"},
    {"name": "2023.yaml", "type": "file"},
    {"name": "notes.md", "type": "file"},  # ignored (not <year>.yaml)
]
_USENIX_FILES = [{"name": "2023.yaml", "type": "file"}]

_NDSS_2023 = """
- title: "Matched Paper."
  authors: ["Jane Doe", "John Roe 0001"]
  page_link: "https://doi.org/10.1/ndss.2023.1"
  discovered_artifact: "github.com/org/repo"
- title: "No Artifact Paper."
  authors: ["Nobody Here"]
  page_link: null
  discovered_artifact: null
"""
_NDSS_2016 = """
- title: "Old Paper Before AE."
  authors: ["Ancient Author"]
  discovered_artifact: "https://github.com/old/repo"
"""
_USENIX_2023 = """
- title: "USENIX Paper."
  authors: ["Alice Smith"]
  page_link: "https://www.usenix.org/x"
  discovered_artifact: "https://github.com/alice/tool/"
"""


def _fake_download(url: str):
    if url == af.ARTIFINDER_API_BASE:
        return json.dumps(_TOP)
    if url == f"{af.ARTIFINDER_API_BASE}/ndss":
        return json.dumps(_NDSS_FILES)
    if url == f"{af.ARTIFINDER_API_BASE}/usenix":
        return json.dumps(_USENIX_FILES)
    if url == f"{af.ARTIFINDER_API_BASE}/twitter":
        return json.dumps([])
    if url.endswith("/ndss/2023.yaml"):
        return _NDSS_2023
    if url.endswith("/ndss/2016.yaml"):
        return _NDSS_2016
    if url.endswith("/usenix/2023.yaml"):
        return _USENIX_2023
    return None


@pytest.fixture(autouse=True)
def _patch_download(monkeypatch):
    monkeypatch.setattr(af, "download_file", _fake_download)


class TestNormalizeArtifactUrl:
    def test_adds_https_scheme_to_bare_host(self):
        assert af.normalize_artifact_url("github.com/org/repo") == "https://github.com/org/repo"

    def test_keeps_existing_scheme(self):
        assert af.normalize_artifact_url("http://example.com/x") == "http://example.com/x"

    def test_strips_trailing_slash(self):
        assert af.normalize_artifact_url("https://github.com/a/b/") == "https://github.com/a/b"

    def test_empty(self):
        assert af.normalize_artifact_url("") == ""
        assert af.normalize_artifact_url(None) == ""


class TestCleanAuthor:
    def test_strips_dblp_suffix(self):
        assert af._clean_author("John Roe 0001") == "John Roe"

    def test_collapses_whitespace(self):
        assert af._clean_author("  Jane   Doe ") == "Jane Doe"


class TestLoadArtifinder:
    def test_maps_venues_and_skips_unmapped(self):
        data = af.load_artifinder(min_year=None)
        confs = {e["conference"] for e in data.entries}
        assert confs == {"NDSS", "USENIXSEC"}  # 'twitter' venue skipped

    def test_only_entries_with_discovered_artifact(self):
        data = af.load_artifinder(min_year=None)
        titles = {e["title"] for e in data.entries}
        assert "No Artifact Paper." not in titles
        # But it is counted in totals.
        ndss23 = next(c for c in data.counts if c["conference"] == "NDSS" and c["year"] == 2023)
        assert ndss23["total_papers"] == 2
        assert ndss23["discovered"] == 1

    def test_min_year_filter_default_excludes_2016(self):
        data = af.load_artifinder()  # default min_year=2017
        years = {e["year"] for e in data.entries}
        assert 2016 not in years
        assert "Old Paper Before AE." not in {e["title"] for e in data.entries}

    def test_min_year_none_includes_old(self):
        data = af.load_artifinder(min_year=None)
        assert 2016 in {e["year"] for e in data.entries}

    def test_bare_github_url_normalized(self):
        data = af.load_artifinder(min_year=None)
        matched = next(e for e in data.entries if e["title"] == "Matched Paper.")
        assert matched["artifact_urls"] == ["https://github.com/org/repo"]

    def test_category_is_security(self):
        data = af.load_artifinder(min_year=None)
        assert all(e["category"] == "security" for e in data.entries)

    def test_conf_regex_filter(self):
        data = af.load_artifinder(conf_regex=r"usenixsec2023", min_year=None)
        assert {e["conference"] for e in data.entries} == {"USENIXSEC"}

    def test_empty_top_dir_returns_empty(self, monkeypatch):
        monkeypatch.setattr(af, "download_file", lambda url: None)
        data = af.load_artifinder()
        assert data.entries == []
        assert data.counts == []


class TestNewSchema:
    """Current ArtiFinder-Data schema: ``artifacts_discovered`` is a list."""

    _YAML = """
- title: "Two Links."
  authors: ["Jane Doe"]
  page_link: "https://doi.org/10.1/x"
  artifacts_discovered:
    - github.com/org/repo/
    - https://doi.org/10.5281/zenodo.1
    - https://github.com/org/repo
  validated: false
- title: "Empty List."
  authors: ["A B"]
  artifacts_discovered: []
  validated: false
- title: "Org Page First."
  authors: ["A B"]
  artifacts_discovered:
    - https://github.com/someorg
    - https://github.com/someorg/tool
    - https://github.com/
  validated: false
"""

    def test_parse(self):
        entries, total = af._parse_year_file("NDSS", "security", 2024, self._YAML)
        assert total == 3
        assert len(entries) == 2  # the empty-list paper has no link
        # Normalised, de-duplicated, order (= score) preserved.
        assert entries[0]["artifact_urls"] == [
            "https://github.com/org/repo",
            "https://doi.org/10.5281/zenodo.1",
        ]

    def test_all_links_kept_in_order_no_filtering(self):
        entries, _ = af._parse_year_file("NDSS", "security", 2024, self._YAML)
        e = next(e for e in entries if e["title"] == "Org Page First.")
        # Org pages and bare hosts are ordinary links: nothing dropped or reordered.
        assert e["artifact_urls"] == [
            "https://github.com/someorg",
            "https://github.com/someorg/tool",
            "https://github.com",
        ]

    def test_legacy_key_still_supported(self):
        urls = af._extract_artifact_urls({"discovered_artifact": "github.com/a/b"})
        assert urls == ["https://github.com/a/b"]

    def test_acsac_venue_mapped(self):
        assert af._VENUE_MAP["acsac"] == ("ACSAC", "security")


class TestLoadArtifinderLocal:
    """The loader can read from a local ArtiFinder-Data checkout (offline)."""

    @pytest.fixture
    def local_repo(self, tmp_path):
        data = tmp_path / "data"
        (data / "ndss").mkdir(parents=True)
        (data / "usenix").mkdir()
        (data / "unmapped").mkdir()
        (data / "ndss" / "2023.yaml").write_text(_NDSS_2023)
        (data / "ndss" / "2016.yaml").write_text(_NDSS_2016)
        (data / "usenix" / "2023.yaml").write_text(_USENIX_2023)
        return tmp_path

    def test_reads_from_repo_root(self, local_repo, monkeypatch):
        # Ensure no network is used.
        monkeypatch.setattr(af, "download_file", lambda url: (_ for _ in ()).throw(AssertionError("network used")))
        data = af.load_artifinder(min_year=None, local_dir=str(local_repo))
        assert {e["conference"] for e in data.entries} == {"NDSS", "USENIXSEC"}
        assert 2016 in {e["year"] for e in data.entries}

    def test_reads_from_data_dir_directly(self, local_repo):
        data = af.load_artifinder(min_year=2017, local_dir=str(local_repo / "data"))
        assert 2016 not in {e["year"] for e in data.entries}

    def test_env_var_fallback(self, local_repo, monkeypatch):
        monkeypatch.setenv(af.ARTIFINDER_LOCAL_ENV, str(local_repo))
        monkeypatch.setattr(af, "download_file", lambda url: (_ for _ in ()).throw(AssertionError("network used")))
        data = af.load_artifinder(min_year=None)
        assert data.entries

    def test_missing_local_dir_falls_back_to_remote(self, monkeypatch, tmp_path):
        monkeypatch.setattr(af, "download_file", _fake_download)
        data = af.load_artifinder(min_year=None, local_dir=str(tmp_path / "does-not-exist"))
        # Falls back to the (patched) remote source.
        assert {e["conference"] for e in data.entries} == {"NDSS", "USENIXSEC"}
