"""Tests for legacy-format JSON migration through Pydantic model validators.

Ensures old-format data (pre-schema) is accepted and normalised to the
current field names by ``@model_validator(mode="before")`` on models.
"""

import pytest
from pydantic import ValidationError

from src.models.aggregates.repo_stats import RepoStatsEntry
from src.models.committees.ae_members import AEMember, AEMembership

# ── RepoStatsEntry legacy migration ─────────────────────────────────────────


class TestRepoStatsEntryMigration:
    """Old ``repo_stats_detail.json`` entries used ``stars``/``forks`` and
    omitted ``source``.  The model_validator should accept these."""

    def test_stars_renamed_to_github_stars(self):
        entry = RepoStatsEntry.model_validate(
            {
                "conference": "OSDI",
                "year": 2023,
                "title": "Test Paper",
                "url": "https://github.com/org/repo",
                "stars": 42,
                "forks": 10,
            }
        )
        assert entry.github_stars == 42
        assert entry.github_forks == 10

    def test_source_inferred_from_github_url(self):
        entry = RepoStatsEntry.model_validate(
            {
                "conference": "OSDI",
                "year": 2023,
                "title": "Test Paper",
                "url": "https://github.com/org/repo",
                "stars": 5,
            }
        )
        assert entry.source == "github"

    def test_source_inferred_from_zenodo_url(self):
        entry = RepoStatsEntry.model_validate(
            {
                "conference": "OSDI",
                "year": 2023,
                "title": "Test Paper",
                "url": "https://zenodo.org/records/12345",
            }
        )
        assert entry.source == "zenodo"

    def test_source_inferred_from_figshare_url(self):
        entry = RepoStatsEntry.model_validate(
            {
                "conference": "OSDI",
                "year": 2023,
                "title": "Test Paper",
                "url": "https://figshare.com/articles/12345",
            }
        )
        assert entry.source == "figshare"

    def test_source_defaults_to_github_for_unknown_url(self):
        entry = RepoStatsEntry.model_validate(
            {
                "conference": "OSDI",
                "year": 2023,
                "title": "Test Paper",
                "url": "https://example.com/artifact",
            }
        )
        assert entry.source == "github"

    def test_new_format_unaffected(self):
        """Current-format data passes through the validator unchanged."""
        entry = RepoStatsEntry.model_validate(
            {
                "conference": "OSDI",
                "year": 2023,
                "title": "Test Paper",
                "url": "https://github.com/org/repo",
                "source": "github",
                "github_stars": 100,
                "github_forks": 20,
            }
        )
        assert entry.github_stars == 100
        assert entry.github_forks == 20
        assert entry.source == "github"

    def test_new_fields_take_precedence_over_old(self):
        """If both ``stars`` and ``github_stars`` are present, keep ``github_stars``."""
        entry = RepoStatsEntry.model_validate(
            {
                "conference": "OSDI",
                "year": 2023,
                "title": "Test Paper",
                "url": "https://github.com/org/repo",
                "source": "github",
                "stars": 10,
                "github_stars": 100,
            }
        )
        assert entry.github_stars == 100

    def test_extra_unknown_field_still_rejected(self):
        """``extra='forbid'`` still rejects truly unknown fields."""
        with pytest.raises(ValidationError):
            RepoStatsEntry.model_validate(
                {
                    "conference": "OSDI",
                    "year": 2023,
                    "title": "Test Paper",
                    "url": "https://github.com/org/repo",
                    "source": "github",
                    "totally_unknown_field": 42,
                }
            )


# ── AEMember conference tuple migration ─────────────────────────────────────


class TestAEMemberConferenceMigration:
    """Old AE member data stored conferences as ``[conf, year, role]`` tuples.
    The ``_coerce_conferences`` field_validator converts them to dicts."""

    def test_tuple_conferences_coerced_to_dicts(self):
        member = AEMember.model_validate(
            {
                "name": "Alice Smith",
                "display_name": "Alice Smith",
                "affiliation": "MIT",
                "total_memberships": 2,
                "chair_count": 0,
                "conferences": [
                    ["OSDI", 2023, "member"],
                    ["ATC", 2024, "chair"],
                ],
                "years": {"2023": 1, "2024": 1},
                "area": "systems",
            }
        )
        assert len(member.conferences) == 2
        assert isinstance(member.conferences[0], AEMembership)
        assert member.conferences[0].conference == "OSDI"
        assert member.conferences[0].year == 2023
        assert member.conferences[0].role == "member"
        assert member.conferences[1].role == "chair"

    def test_dict_conferences_pass_through(self):
        """Current-format dicts are unaffected."""
        member = AEMember.model_validate(
            {
                "name": "Bob Jones",
                "display_name": "Bob Jones",
                "affiliation": "Stanford",
                "total_memberships": 1,
                "chair_count": 0,
                "conferences": [
                    {"conference": "USENIXSEC", "year": 2024, "role": "member"},
                ],
                "years": {"2024": 1},
                "area": "security",
            }
        )
        assert member.conferences[0].conference == "USENIXSEC"
