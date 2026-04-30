"""Tests for Pydantic models in src/models/.

Validates schema constraints (required fields, value ranges, extra-field
rejection) and basic round-trip serialisation for every model.
"""

import pytest
from pydantic import ValidationError

from src.models.aggregates.artifacts_by_conference import ConferenceEntry, YearBreakdown
from src.models.aggregates.artifacts_by_year import ArtifactsByYear
from src.models.aggregates.repo_stats import (
    OverallStats,
    RepoStatsEntry,
    RepoStatsSummary,
)
from src.models.aggregates.summary import Summary
from src.models.artifacts.paper_index import Paper
from src.models.artifacts.search_data import SearchEntry
from src.models.authors.author_index import AffiliationHistoryEntry, AuthorIndexEntry, ExternalIds
from src.models.authors.author_stats import ArtifactPaper, AuthorStats, PlainPaper
from src.models.institutions.institution_rankings import InstitutionRanking, TopAuthor

# ── YearBreakdown & ConferenceEntry ────────────────────────────────


class TestYearBreakdown:
    def test_valid(self):
        yb = YearBreakdown(year=2023, total=10, available=8, functional=6, reproducible=3, reusable=1)
        assert yb.year == 2023
        assert yb.total == 10

    def test_negative_count_rejected(self):
        with pytest.raises(ValidationError):
            YearBreakdown(year=2023, total=-1, available=0, functional=0, reproducible=0, reusable=0)

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            YearBreakdown(year=2023, total=0, available=0, functional=0, reproducible=0, reusable=0, extra=1)


class TestConferenceEntry:
    def test_valid(self):
        yb = YearBreakdown(year=2023, total=5, available=5, functional=3, reproducible=2, reusable=0)
        ce = ConferenceEntry(name="OSDI", category="systems", venue_type="conference", total_artifacts=5, years=[yb])
        assert ce.name == "OSDI"
        assert ce.category == "systems"
        assert len(ce.years) == 1

    def test_invalid_category(self):
        with pytest.raises(ValidationError):
            ConferenceEntry(name="X", category="other", venue_type="conference", total_artifacts=0, years=[])

    def test_invalid_venue_type(self):
        with pytest.raises(ValidationError):
            ConferenceEntry(name="X", category="systems", venue_type="journal", total_artifacts=0, years=[])


# ── ArtifactsByYear ────────────────────────────────────────────────


class TestArtifactsByYear:
    def test_valid(self):
        yc = ArtifactsByYear(year=2024, count=50, systems=30, security=20)
        assert yc.count == 50

    def test_negative_rejected(self):
        with pytest.raises(ValidationError):
            ArtifactsByYear(year=2024, count=-1, systems=0, security=0)

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            ArtifactsByYear(year=2024, count=0, systems=0, security=0, extra=True)


# ── AuthorIndex models ─────────────────────────────────────────────


class TestExternalIds:
    def test_defaults_none(self):
        ext = ExternalIds()
        assert ext.dblp_pid is None
        assert ext.orcid is None
        assert ext.openalex_id is None

    def test_valid_orcid(self):
        ext = ExternalIds(orcid="0000-0001-2345-6789")
        assert ext.orcid == "0000-0001-2345-6789"

    def test_invalid_orcid_rejected(self):
        with pytest.raises(ValidationError):
            ExternalIds(orcid="not-an-orcid")

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            ExternalIds(foo="bar")


class TestAffiliationHistoryEntry:
    def test_valid(self):
        e = AffiliationHistoryEntry(affiliation="MIT", source="csrankings", date="2025-01-01")
        assert e.affiliation == "MIT"


class TestAuthorIndexEntry:
    def test_minimal(self):
        e = AuthorIndexEntry(id=1, name="Alice", display_name="Alice", affiliation="")
        assert e.id == 1
        assert e.affiliation_source == ""
        assert e.category == "systems"

    def test_full(self):
        e = AuthorIndexEntry(
            id=42,
            name="Bob 0001",
            display_name="Bob",
            affiliation="Stanford",
            affiliation_source="openalex",
            affiliation_updated="2025-06-15",
            affiliation_history=[AffiliationHistoryEntry(affiliation="MIT", source="csrankings", date="2024-01-01")],
            external_ids=ExternalIds(orcid="0000-0001-2345-6789"),
            category="both",
        )
        assert e.external_ids.orcid is not None
        assert len(e.affiliation_history) == 1

    def test_id_zero_rejected(self):
        with pytest.raises(ValidationError):
            AuthorIndexEntry(id=0, name="X", display_name="X", affiliation="")

    def test_invalid_affiliation_source(self):
        with pytest.raises(ValidationError):
            AuthorIndexEntry(id=1, name="X", display_name="X", affiliation="", affiliation_source="unknown")


# ── AuthorStats models ─────────────────────────────────────────────


class TestArtifactPaper:
    def test_valid(self):
        p = ArtifactPaper(
            title="Test Paper",
            conference="OSDI",
            year=2023,
            badges=["available", "functional"],
            category="systems",
            artifact_citations=10,
        )
        assert p.badges == ["available", "functional"]

    def test_negative_citations_rejected(self):
        with pytest.raises(ValidationError):
            ArtifactPaper(title="X", conference="X", year=2023, badges=[], category="systems", artifact_citations=-1)


class TestPlainPaper:
    def test_valid(self):
        p = PlainPaper(title="No Artifact", conference="SOSP", year=2022)
        assert p.year == 2022


class TestAuthorStats:
    def _make(self, **overrides):
        defaults = dict(
            name="Alice",
            display_name="Alice",
            affiliation="MIT",
            artifact_count=3,
            total_papers=10,
            total_papers_by_conf={"OSDI": 5, "SOSP": 5},
            total_papers_by_conf_year={"OSDI": {"2023": 3}, "SOSP": {"2022": 2}},
            artifact_pct=30.0,
            repro_pct=50.0,
            functional_pct=66.7,
            category="systems",
            conferences=["OSDI", "SOSP"],
            years=[2022, 2023],
            year_range="2022-2023",
            recent_count=2,
            artifact_citations=15,
            badges_available=3,
            badges_functional=2,
            badges_reproducible=1,
        )
        defaults.update(overrides)
        return AuthorStats(**defaults)

    def test_valid(self):
        s = self._make()
        assert s.artifact_count == 3

    def test_artifact_pct_out_of_range(self):
        with pytest.raises(ValidationError):
            self._make(artifact_pct=101.0)

    def test_year_range_format(self):
        with pytest.raises(ValidationError):
            self._make(year_range="2023")

    def test_round_trip(self):
        s = self._make()
        d = s.model_dump()
        s2 = AuthorStats(**d)
        assert s2 == s


# ── InstitutionRanking models ──────────────────────────────────────


class TestTopAuthor:
    def test_valid(self):
        t = TopAuthor(
            name="Alice",
            affiliation="MIT",
            combined_score=10,
            artifact_count=3,
            ae_memberships=2,
            total_papers=8,
        )
        assert t.combined_score == 10


class TestInstitutionRanking:
    def _make(self, **overrides):
        defaults = dict(
            affiliation="MIT",
            combined_score=50,
            artifact_score=30,
            ae_score=20,
            role="Balanced",
            artifact_count=10,
            badges_functional=8,
            badges_reproducible=5,
            ae_memberships=4,
            chair_count=1,
            total_papers=20,
            artifact_pct=50.0,
            author_count=5,
            conferences=["OSDI", "SOSP"],
            years={"2023": 3, "2022": 2},
            top_authors=[],
        )
        defaults.update(overrides)
        return InstitutionRanking(**defaults)

    def test_valid(self):
        ir = self._make()
        assert ir.affiliation == "MIT"

    def test_combined_score_minimum(self):
        with pytest.raises(ValidationError):
            self._make(combined_score=2)

    def test_invalid_role(self):
        with pytest.raises(ValidationError):
            self._make(role="Unknown")

    def test_ae_ratio_nullable(self):
        ir = self._make(ae_ratio=None)
        assert ir.ae_ratio is None

    def test_round_trip(self):
        ir = self._make(ae_ratio=1.5)
        d = ir.model_dump()
        ir2 = InstitutionRanking(**d)
        assert ir2 == ir


# ── RepoStats models ──────────────────────────────────────────────


class TestRepoStatsEntry:
    def test_valid_github(self):
        e = RepoStatsEntry(
            conference="OSDI",
            year=2023,
            title="My Paper",
            url="https://github.com/user/repo",
            source="github",
            github_stars=100,
            github_forks=20,
        )
        assert e.source == "github"

    def test_valid_zenodo(self):
        e = RepoStatsEntry(
            conference="CCS",
            year=2023,
            title="Sec Paper",
            url="https://zenodo.org/record/123",
            source="zenodo",
            zenodo_views=500,
            zenodo_downloads=200,
        )
        assert e.zenodo_views == 500

    def test_invalid_source(self):
        with pytest.raises(ValidationError):
            RepoStatsEntry(
                conference="X",
                year=2023,
                title="X",
                url="http://example.com",
                source="npm",
            )


class TestRepoStatsSummary:
    def test_valid(self):
        summary = RepoStatsSummary(
            overall=OverallStats(
                github_repos=100,
                total_stars=5000,
                total_forks=1000,
                max_stars=500,
                max_forks=100,
                zenodo_repos=20,
                total_views=10000,
                total_downloads=5000,
                avg_stars=50.0,
                avg_forks=10.0,
                median_stars=25.0,
                median_forks=6.0,
                p25_stars=8.0,
                p75_stars=80.0,
                p25_forks=2.0,
                p75_forks=18.0,
                last_updated="2025-01-01T00:00:00Z",
            ),
            by_conference=[],
            by_year=[],
        )
        assert summary.overall.github_repos == 100


# ── SearchEntry ────────────────────────────────────────────────────


class TestSearchEntry:
    def test_valid(self):
        se = SearchEntry(
            title="Test Paper",
            conference="OSDI",
            category="systems",
            year=2023,
            badges=["available"],
            artifact_urls=["https://github.com/user/repo"],
            doi_url="https://doi.org/10.1234/test",
            authors=["Alice", "Bob"],
            affiliations=["MIT"],
        )
        assert se.authors == ["Alice", "Bob"]

    def test_invalid_category(self):
        with pytest.raises(ValidationError):
            SearchEntry(
                title="X",
                conference="X",
                category="other",
                year=2023,
                badges=[],
                artifact_urls=[],
                doi_url="",
                authors=[],
                affiliations=[],
            )


# ── Paper (paper_index) ───────────────────────────────────────────


class TestPaper:
    def test_round_trip(self):
        p = Paper(id=1, title="Test", conference="OSDI", year=2023)
        d = p.model_dump()
        assert Paper(**d) == p


# ── Summary ────────────────────────────────────────────────────────


class TestSummary:
    def test_round_trip(self):
        s = Summary(
            schema_version="1.0.0",
            total_artifacts=100,
            total_conferences=5,
            systems_artifacts=60,
            security_artifacts=40,
            conferences_list=["OSDI", "SOSP", "CCS"],
            systems_conferences=["OSDI", "SOSP"],
            security_conferences=["CCS"],
            year_range="2020-2025",
            last_updated="2025-01-01",
        )
        d = s.model_dump()
        assert Summary(**d) == s
