"""Tests for new Pydantic models added for schema coverage.

Validates schema constraints (required fields, value ranges, extra-field
rejection) and basic round-trip serialisation for every new model.
"""

import pytest
from pydantic import ValidationError

from src.models.aggregates.ranking_history import RankingHistoryEntry, RankingSnapshot
from src.models.aggregates.repo_stats_yearly import RepoStatsYearly, YearlyRepoMetrics
from src.models.aggregates.top_repos import TopRepo
from src.models.artifacts.artifact_availability import (
    ArtifactAvailability,
    AvailabilityRecord,
    AvailabilitySummary,
    PlatformStats,
)
from src.models.artifacts.artifact_citations import ArtifactCitation
from src.models.authors.author_profiles import AuthorProfile
from src.models.committees.ae_members import AEMember
from src.models.committees.committee_stats import (
    CommitteeSize,
    CommitteeStats,
    CommitteeSummary,
    FailedClassification,
    NameCount,
    SplitCounts,
)
from src.models.committees.participation_stats import (
    AreaTrend,
    ConferenceYearStats,
    ParticipationStats,
)
from src.models.institutions.institution_ranking_history import (
    InstitutionRankingSnapshot,
)

# ── AEMember ───────────────────────────────────────────────────────


class TestAEMember:
    def test_valid(self):
        m = AEMember(
            name="Alice",
            display_name="Alice",
            affiliation="MIT",
            total_memberships=3,
            chair_count=1,
            conferences=[["OSDI", 2023, "member"]],
            years={"2023": 1},
            area="systems",
            first_year=2023,
            last_year=2023,
        )
        assert m.total_memberships == 3

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            AEMember(
                name="A",
                display_name="A",
                affiliation="X",
                total_memberships=0,
                chair_count=0,
                conferences=[],
                years={},
                area="systems",
                extra=1,
            )


# ── TopRepo ────────────────────────────────────────────────────────


class TestTopRepo:
    def test_valid(self):
        r = TopRepo(
            title="Paper",
            url="https://github.com/org/repo",
            year=2023,
            stars=100,
            forks=20,
            authors="Alice, Bob",
            conference="OSDI",
            area="systems",
        )
        assert r.stars == 100

    def test_negative_stars_rejected(self):
        with pytest.raises(ValidationError):
            TopRepo(
                title="P",
                url="u",
                year=2023,
                stars=-1,
                forks=0,
                authors="A",
                conference="X",
                area="systems",
            )

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            TopRepo(
                title="P",
                url="u",
                year=2023,
                stars=0,
                forks=0,
                authors="A",
                conference="X",
                area="systems",
                extra=1,
            )


# ── ArtifactCitation ───────────────────────────────────────────────


class TestArtifactCitation:
    def test_valid(self):
        c = ArtifactCitation(
            title="Paper",
            conference="NDSS",
            year=2023,
        )
        assert c.cited_by_count is None

    def test_with_counts(self):
        c = ArtifactCitation(
            title="P",
            conference="C",
            year=2023,
            doi="10.1234/test",
            doi_source="zenodo",
            cited_by_count=5,
            citations_openalex=3,
            citations_semantic_scholar=5,
        )
        assert c.cited_by_count == 5

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            ArtifactCitation(
                title="P",
                conference="C",
                year=2023,
                extra=1,
            )


# ── ParticipationStats ────────────────────────────────────────────


class TestConferenceYearStats:
    def test_valid(self):
        s = ConferenceYearStats(
            conference="OSDI",
            year=2023,
            category="systems",
            venue_type="conference",
            total_papers=50,
            ae_papers=30,
            participation_pct=60.0,
            available=25,
            functional=20,
            reproduced=10,
            available_pct=83.3,
            functional_pct=66.7,
            reproduced_pct=33.3,
        )
        assert s.participation_pct == 60.0


class TestParticipationStats:
    def test_valid(self):
        cys = ConferenceYearStats(
            conference="OSDI",
            year=2023,
            category="systems",
            venue_type="conference",
            total_papers=50,
            ae_papers=30,
            participation_pct=60.0,
            available=25,
            functional=20,
            reproduced=10,
            available_pct=83.3,
            functional_pct=66.7,
            reproduced_pct=33.3,
        )
        trend = AreaTrend(
            years=[2023],
            participation_pct=[60.0],
            available_pct=[83.3],
            functional_pct=[66.7],
            reproduced_pct=[33.3],
        )
        ps = ParticipationStats(by_conference_year=[cys], by_area={"systems": trend})
        assert len(ps.by_conference_year) == 1

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            ParticipationStats(by_conference_year=[], by_area={}, extra=1)


# ── CommitteeStats ─────────────────────────────────────────────────


class TestCommitteeStats:
    def test_valid(self):
        summary = CommitteeSummary(
            total_members=100,
            total_systems=60,
            total_security=40,
            total_countries=10,
            total_continents=5,
            total_institutions=50,
        )
        nc = NameCount(name="US", count=50)
        split = SplitCounts(overall=[nc], systems=[nc], security=[nc])
        cs = CommitteeSize(conference="OSDI", year=2023, conf_year="osdi2023", area="systems", size=20)
        fc = FailedClassification(conference="osdi2023", name="Alice", affiliation="Unknown")
        stats = CommitteeStats(
            summary=summary,
            by_country=split,
            by_continent=split,
            by_institution=split,
            by_year={"country": {"2023": {"US": 10}}},
            committee_sizes=[cs],
            failed_classifications=[fc],
        )
        assert stats.summary.total_members == 100

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            NameCount(name="US", count=50, extra=1)


# ── RankingHistory ─────────────────────────────────────────────────


class TestRankingSnapshot:
    def test_valid(self):
        s = RankingSnapshot(
            **{
                "rank": 1,
                "score": 60,
                "as": 10,
                "aes": 50,
                "tp": 5,
                "ta": 3,
                "ar": 60.0,
                "rr": 80.0,
            }
        )
        assert s.score == 60
        assert s.as_ == 10

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            RankingSnapshot(
                **{
                    "rank": 1,
                    "score": 0,
                    "as": 0,
                    "aes": 0,
                    "tp": 0,
                    "ta": 0,
                    "ar": 0,
                    "rr": 0,
                    "extra": 1,
                }
            )


class TestRankingHistoryEntry:
    def test_valid(self):
        snap = RankingSnapshot(
            **{
                "rank": 1,
                "score": 60,
                "as": 10,
                "aes": 50,
                "tp": 5,
                "ta": 3,
                "ar": 60.0,
                "rr": 80.0,
            }
        )
        entry = RankingHistoryEntry(date="2024-01-01", entries={"Alice": snap})
        assert len(entry.entries) == 1


# ── InstitutionRankingHistory ──────────────────────────────────────


class TestInstitutionRankingSnapshot:
    def test_valid(self):
        s = InstitutionRankingSnapshot(
            **{
                "rank": 1,
                "score": 500,
                "as": 200,
                "aes": 300,
                "tp": 100,
                "ta": 70,
                "ar": 70.0,
                "rr": 80.0,
                "r": 50,
            }
        )
        assert s.r == 50

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            InstitutionRankingSnapshot(
                **{
                    "rank": 1,
                    "score": 0,
                    "as": 0,
                    "aes": 0,
                    "tp": 0,
                    "ta": 0,
                    "ar": 0,
                    "rr": 0,
                    "r": 0,
                    "extra": 1,
                }
            )


# ── AuthorProfile ─────────────────────────────────────────────────


class TestAuthorProfile:
    def test_ae_only_profile(self):
        """AE-only member: no papers, conferences coerced from tuples to strings."""
        p = AuthorProfile(
            name="Alice",
            affiliation="MIT",
            papers=[],
            conferences=[["OSDI", 2023, "member"]],
            years=[2023],
            artifact_count=0,
            total_papers=0,
            artifact_pct=0,
            artifact_citations=0,
            badges_available=0,
            badges_functional=0,
            badges_reproducible=0,
            category="systems",
            combined_score=3,
            artifact_score=0,
            citation_score=0,
            ae_score=3,
            ae_memberships=1,
            chair_count=0,
            ae_conferences=[["OSDI", 2023, "member"]],
            ae_years={"2023": 1},
        )
        assert p.ae_memberships == 1
        # conferences coerced from tuples to unique strings
        assert p.conferences == ["OSDI"]
        # ae_conferences coerced to AEMembership objects
        assert p.ae_conferences[0].conference == "OSDI"
        assert p.ae_conferences[0].year == 2023
        assert p.ae_conferences[0].role == "member"

    def test_author_only_profile(self):
        """Author with papers, no AE service."""
        p = AuthorProfile(
            name="Bob",
            affiliation="Stanford",
            papers=[
                {
                    "title": "Paper",
                    "conference": "OSDI",
                    "year": 2023,
                    "badges": ["available"],
                    "category": "systems",
                    "artifact_citations": 0,
                }
            ],
            conferences=["OSDI"],
            years=[2023],
            artifact_count=1,
            total_papers=1,
            artifact_pct=100,
            artifact_citations=0,
            badges_available=1,
            badges_functional=0,
            badges_reproducible=0,
            category="systems",
            combined_score=1,
            artifact_score=1,
            citation_score=0,
            ae_score=0,
            rank=1,
            author_id=5,
        )
        assert p.ae_memberships == 0
        assert p.author_id == 5
        assert p.papers[0].title == "Paper"

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            AuthorProfile(
                name="X",
                affiliation="X",
                papers=[],
                conferences=[],
                years=[],
                artifact_count=0,
                total_papers=0,
                artifact_pct=0,
                artifact_citations=0,
                badges_available=0,
                badges_functional=0,
                badges_reproducible=0,
                category="systems",
                combined_score=0,
                artifact_score=0,
                citation_score=0,
                ae_score=0,
                extra=1,
            )


# ── ArtifactAvailability ──────────────────────────────────────────


class TestArtifactAvailability:
    def test_valid(self):
        ps = PlatformStats(total=10, accessible=9, pct=90.0)
        summary = AvailabilitySummary(
            checked_at="2024-01-01 00:00:00 UTC",
            total_urls=10,
            accessible_urls=9,
            accessibility_pct=90.0,
            by_platform={"GitHub": ps},
            by_area={"systems": ps},
            by_year={"2023": ps},
            by_year_area={"2023": {"systems": ps}},
            by_year_platform={"2023": {"GitHub": ps}},
            by_conference={"OSDI": ps},
        )
        rec = AvailabilityRecord(
            conference="OSDI",
            year=2023,
            area="systems",
            title="Paper",
            url_key="repository_url",
            url="https://github.com/org/repo",
            platform="GitHub",
            accessible=True,
        )
        aa = ArtifactAvailability(summary=summary, records=[rec])
        assert aa.summary.total_urls == 10

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            PlatformStats(total=10, accessible=9, pct=90.0, extra=1)


# ── RepoStatsYearly ───────────────────────────────────────────────


class TestRepoStatsYearly:
    def test_all_areas(self):
        m = YearlyRepoMetrics(
            github_repos=10,
            avg_stars=5.0,
            avg_forks=2.0,
            min_stars=1.0,
            max_stars=20.0,
            min_forks=0.0,
            max_forks=10.0,
        )
        r = RepoStatsYearly(year=2023, all=m, systems=m, security=m)
        assert r.year == 2023

    def test_without_systems(self):
        m = YearlyRepoMetrics(
            github_repos=5,
            avg_stars=3.0,
            avg_forks=1.0,
            min_stars=1.0,
            max_stars=5.0,
            min_forks=0.0,
            max_forks=2.0,
        )
        r = RepoStatsYearly(year=2017, all=m, security=m)
        assert r.systems is None

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            YearlyRepoMetrics(
                github_repos=1,
                avg_stars=0,
                avg_forks=0,
                min_stars=0,
                max_stars=0,
                min_forks=0,
                max_forks=0,
                extra=1,
            )
