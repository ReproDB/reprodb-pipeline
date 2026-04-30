"""Tests for src/utils/artifact_urls — URL classification and extraction."""

from src.utils.normalization.artifact_urls import (
    extract_source,
    get_artifact_url,
    get_artifact_urls,
    resolve_doi_prefix,
)

# ── resolve_doi_prefix ─────────────────────────────────────────────


class TestResolvDoiPrefix:
    def test_zenodo(self):
        assert resolve_doi_prefix("https://doi.org/10.5281/zenodo.123") == "Zenodo"

    def test_figshare(self):
        assert resolve_doi_prefix("https://doi.org/10.6084/m9.figshare.123") == "Figshare"

    def test_osf(self):
        assert resolve_doi_prefix("https://doi.org/10.17605/OSF.IO/abc") == "OSF"

    def test_unknown_prefix(self):
        assert resolve_doi_prefix("https://doi.org/10.1145/12345") is None

    def test_no_doi(self):
        assert resolve_doi_prefix("https://github.com/user/repo") is None

    def test_bare_doi(self):
        assert resolve_doi_prefix("10.5281/zenodo.456") == "Zenodo"


# ── extract_source ─────────────────────────────────────────────────


class TestExtractSource:
    def test_github(self):
        assert extract_source("https://github.com/user/repo") == "GitHub"

    def test_github_io(self):
        assert extract_source("https://user.github.io/project") == "GitHub"

    def test_zenodo(self):
        assert extract_source("https://zenodo.org/record/123") == "Zenodo"

    def test_figshare(self):
        assert extract_source("https://figshare.com/articles/123") == "Figshare"

    def test_osf(self):
        assert extract_source("https://osf.io/abc") == "OSF"

    def test_gitlab(self):
        assert extract_source("https://gitlab.com/user/repo") == "GitLab"

    def test_bitbucket(self):
        assert extract_source("https://bitbucket.org/user/repo") == "Bitbucket"

    def test_archive_org(self):
        assert extract_source("https://archive.org/details/123") == "Archive"

    def test_arxiv(self):
        assert extract_source("https://arxiv.org/abs/1234.5678") == "Archive"

    def test_dataverse(self):
        assert extract_source("https://dataverse.harvard.edu/dataset.xhtml?id=1") == "Dataverse"

    def test_doi_zenodo(self):
        assert extract_source("https://doi.org/10.5281/zenodo.123") == "Zenodo"

    def test_doi_unknown(self):
        assert extract_source("https://doi.org/10.1145/12345") == "DOI"

    def test_other(self):
        assert extract_source("https://example.com/artifact.tar.gz") == "Other"

    def test_empty(self):
        assert extract_source("") == "unknown"

    def test_none(self):
        assert extract_source(None) == "unknown"


# ── get_artifact_url ───────────────────────────────────────────────


class TestGetArtifactUrl:
    def test_from_artifact_urls_list(self):
        art = {"artifact_urls": ["https://github.com/user/repo", "https://zenodo.org/record/1"]}
        assert get_artifact_url(art) == "https://github.com/user/repo"

    def test_legacy_repository_url(self):
        art = {"repository_url": "https://github.com/user/repo"}
        assert get_artifact_url(art) == "https://github.com/user/repo"

    def test_legacy_github_url(self):
        art = {"github_url": "https://github.com/user/repo"}
        assert get_artifact_url(art) == "https://github.com/user/repo"

    def test_empty_artifact_falls_back(self):
        art = {"artifact_urls": [], "repository_url": "https://github.com/user/repo"}
        assert get_artifact_url(art) == "https://github.com/user/repo"

    def test_no_urls(self):
        art = {}
        assert get_artifact_url(art) is None

    def test_normalise_fn(self):
        art = {"artifact_urls": ["  https://github.com/user/repo  "]}
        url = get_artifact_url(art, normalise_fn=lambda v: v.strip() if v else None)
        assert url == "https://github.com/user/repo"

    def test_normalise_fn_filters(self):
        art = {"artifact_urls": ["invalid", "https://github.com/user/repo"]}
        url = get_artifact_url(art, normalise_fn=lambda v: v if v.startswith("https://") else None)
        assert url == "https://github.com/user/repo"

    def test_legacy_list_value(self):
        art = {"repository_url": ["https://github.com/user/repo", "other"]}
        assert get_artifact_url(art) == "https://github.com/user/repo"


# ── get_artifact_urls ──────────────────────────────────────────────


class TestGetArtifactUrls:
    def test_from_artifact_urls(self):
        art = {"artifact_urls": ["https://github.com/a", "https://zenodo.org/b"]}
        assert get_artifact_urls(art) == ["https://github.com/a", "https://zenodo.org/b"]

    def test_legacy_fallback(self):
        art = {"repository_url": "https://github.com/a", "artifact_url": "https://zenodo.org/b"}
        urls = get_artifact_urls(art)
        assert "https://github.com/a" in urls
        assert "https://zenodo.org/b" in urls

    def test_no_duplicate_legacy(self):
        art = {"repository_url": "https://github.com/a", "github_url": "https://github.com/a"}
        urls = get_artifact_urls(art)
        assert len(urls) == 1

    def test_empty(self):
        assert get_artifact_urls({}) == []

    def test_artifact_urls_takes_priority(self):
        art = {"artifact_urls": ["https://github.com/a"], "repository_url": "https://other.com/b"}
        assert get_artifact_urls(art) == ["https://github.com/a"]
