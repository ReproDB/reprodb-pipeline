"""Tests for scoring formulas, ranking invariants, and data contracts.

Covers cross-module invariants shared by generate_combined_rankings,
generate_area_authors, and generate_institution_rankings.
"""

import pytest

# ── Scoring formulas (from generate_combined_rankings._build_entry) ──────────


class TestScoringFormulas:
    """Test the exact scoring formulas used in combined rankings."""

    def test_artifact_score_additive(self):
        from src.generators.rankings.generate_combined_rankings import _build_entry

        entry = _build_entry(
            name="Test",
            affiliation="MIT",
            artifact_count=5,
            total_papers=10,
            artifact_pct=50,
            ae_memberships=0,
            chair_count=0,
            conferences=["SOSP"],
            years={"2022": 3},
            artifact_citations=0,
            badges_available=5,
            badges_functional=4,
            badges_reproducible=3,
        )
        # artifact_score = artifacts*1 + functional*1 + reproducible*1
        assert entry["artifact_score"] == 5 + 4 + 3  # = 12

    def test_ae_score(self):
        from src.generators.rankings.generate_combined_rankings import _build_entry

        entry = _build_entry(
            name="Test",
            affiliation="MIT",
            artifact_count=0,
            total_papers=0,
            artifact_pct=0,
            ae_memberships=3,
            chair_count=1,
            conferences=["SOSP"],
            years={"2022": 1},
            artifact_citations=0,
            badges_available=0,
            badges_functional=0,
            badges_reproducible=0,
        )
        # ae_score = memberships*3 + chairs*2
        assert entry["ae_score"] == 3 * 3 + 1 * 2  # = 11

    def test_combined_score_is_sum(self):
        from src.generators.rankings.generate_combined_rankings import _build_entry

        entry = _build_entry(
            name="Test",
            affiliation="MIT",
            artifact_count=2,
            total_papers=5,
            artifact_pct=40,
            ae_memberships=1,
            chair_count=0,
            conferences=["CCS"],
            years={"2023": 2},
            artifact_citations=0,
            badges_available=2,
            badges_functional=1,
            badges_reproducible=0,
        )
        assert entry["combined_score"] == entry["artifact_score"] + entry["ae_score"]

    def test_ae_ratio_none_when_ae_score_zero(self):
        from src.generators.rankings.generate_combined_rankings import _build_entry

        entry = _build_entry(
            name="Test",
            affiliation="MIT",
            artifact_count=1,
            total_papers=1,
            artifact_pct=100,
            ae_memberships=0,
            chair_count=0,
            conferences=[],
            years={},
            artifact_citations=0,
            badges_available=1,
            badges_functional=0,
            badges_reproducible=0,
        )
        assert entry["ae_ratio"] is None

    def test_ae_ratio_computed(self):
        from src.generators.rankings.generate_combined_rankings import _build_entry

        entry = _build_entry(
            name="Test",
            affiliation="MIT",
            artifact_count=3,
            total_papers=5,
            artifact_pct=60,
            ae_memberships=1,
            chair_count=0,
            conferences=[],
            years={},
            artifact_citations=0,
            badges_available=3,
            badges_functional=2,
            badges_reproducible=1,
        )
        # artifact_score = 3+2+1=6, ae_score = 3, ratio = 2.0
        assert entry["ae_ratio"] == 2.0

    def test_repro_pct_zero_when_no_artifacts(self):
        from src.generators.rankings.generate_combined_rankings import _build_entry

        entry = _build_entry(
            name="Test",
            affiliation="MIT",
            artifact_count=0,
            total_papers=0,
            artifact_pct=0,
            ae_memberships=1,
            chair_count=0,
            conferences=[],
            years={},
            artifact_citations=0,
            badges_available=0,
            badges_functional=0,
            badges_reproducible=0,
        )
        assert entry["repro_pct"] == 0


# ── Invariant violations ─────────────────────────────────────────────────────


class TestInvariantViolations:
    """Hard invariant checks that must raise ValueError."""

    def test_reproducible_exceeds_artifacts_raises(self):
        from src.generators.rankings.generate_combined_rankings import _build_entry

        with pytest.raises(ValueError, match="reproduced_badges.*>.*artifact_count"):
            _build_entry(
                name="Bad",
                affiliation="",
                artifact_count=2,
                total_papers=5,
                artifact_pct=40,
                ae_memberships=0,
                chair_count=0,
                conferences=[],
                years={},
                artifact_citations=0,
                badges_available=2,
                badges_functional=1,
                badges_reproducible=3,  # > artifacts (2)
            )

    def test_functional_exceeds_artifacts_raises(self):
        from src.generators.rankings.generate_combined_rankings import _build_entry

        with pytest.raises(ValueError, match="functional_badges.*>.*artifact_count"):
            _build_entry(
                name="Bad",
                affiliation="",
                artifact_count=1,
                total_papers=5,
                artifact_pct=20,
                ae_memberships=0,
                chair_count=0,
                conferences=[],
                years={},
                artifact_citations=0,
                badges_available=1,
                badges_functional=2,  # > artifacts (1)
                badges_reproducible=0,
            )

    def test_artifacts_exceeding_total_papers_clamped(self, caplog):
        import logging

        caplog.set_level(logging.INFO, logger="src.generators.rankings.generate_combined_rankings")
        """artifacts > total_papers is soft-clamped (warning, not error)."""
        from src.generators.rankings.generate_combined_rankings import _build_entry

        entry = _build_entry(
            name="Clamped",
            affiliation="",
            artifact_count=5,
            total_papers=3,  # < artifact_count
            artifact_pct=100,
            ae_memberships=0,
            chair_count=0,
            conferences=[],
            years={},
            artifact_citations=0,
            badges_available=5,
            badges_functional=0,
            badges_reproducible=0,
        )
        # total_papers is clamped up to artifacts
        assert entry["total_papers"] >= entry["artifact_count"]
        assert "undercount" in caplog.text.lower() or "clamping" in caplog.text.lower()


# ── Ranking contract ─────────────────────────────────────────────────────────


class TestRankingContract:
    """Verify rankings are assigned with correct tie-breaking."""

    def test_ranks_monotonic_with_ties(self):
        from src.generators.rankings.generate_combined_rankings import _build_entry

        entries = []
        for name, arts, ae in [("A", 3, 0), ("B", 3, 0), ("C", 1, 0)]:
            entries.append(
                _build_entry(
                    name=name,
                    affiliation="",
                    artifact_count=arts,
                    total_papers=arts,
                    artifact_pct=100,
                    ae_memberships=ae,
                    chair_count=0,
                    conferences=[],
                    years={},
                    artifact_citations=0,
                    badges_available=arts,
                    badges_functional=0,
                    badges_reproducible=0,
                )
            )

        # Sort and assign ranks like the generator does
        entries.sort(key=lambda x: (-x["combined_score"], -x["artifact_count"], x["name"]))
        rank = 1
        for i, c in enumerate(entries):
            if i > 0 and c["combined_score"] < entries[i - 1]["combined_score"]:
                rank = i + 1
            c["rank"] = rank

        # A and B have the same score → same rank
        assert entries[0]["rank"] == 1
        assert entries[1]["rank"] == 1
        # C has lower score → rank jumps to 3
        assert entries[2]["rank"] == 3


# ── Affiliation normalization ────────────────────────────────────────────────


class TestAffiliationNormalization:
    """Spot-check the regex-based normalization."""

    def test_ethz_variants(self):
        from src.utils.normalization.affiliation import normalize_affiliation as _normalize_affiliation

        assert _normalize_affiliation("ETH Zürich") == _normalize_affiliation("ETHZ")
        assert "ETH" in _normalize_affiliation("ETH Zürich")

    def test_empty_string(self):
        from src.utils.normalization.affiliation import normalize_affiliation as _normalize_affiliation

        assert _normalize_affiliation("") == ""
        assert _normalize_affiliation("   ") == ""

    def test_preserves_leading_underscore(self):
        """Underscores are stripped at display time, not during normalization."""
        from src.utils.normalization.affiliation import normalize_affiliation as _normalize_affiliation

        result = _normalize_affiliation("_MIT")
        # Normalization preserves the underscore (display layer strips it)
        assert result == "_MIT"
