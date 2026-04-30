"""Tests for committee web scrapers (CHES, ACSAC, PETS)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.scrapers.scrape_committee_web import (
    scrape_ches_committee,
)
from src.utils.io.cache import _MISSING


@pytest.fixture(autouse=True)
def _no_disk_cache():
    """Prevent disk cache from interfering between tests."""
    with (
        patch("src.utils.io.cache.read_cache", return_value=_MISSING),
        patch("src.utils.io.cache.write_cache"),
    ):
        yield


# ── CHES JSON: artifact_chairs field (2025 format) ──────────────────────────


def _mock_session(json_data=None, html_text="", json_status=200, html_status=200):
    """Build a mock session that returns different responses per URL."""
    session = MagicMock()

    def fake_get(url, **kwargs):
        resp = MagicMock()
        if url.endswith(".json"):
            resp.status_code = json_status
            resp.json.return_value = json_data or {}
            resp.text = json.dumps(json_data or {})
            resp.raise_for_status = MagicMock()
        else:
            resp.status_code = html_status
            resp.text = html_text
            resp.raise_for_status = MagicMock()
        return resp

    session.get = fake_get
    return session


class TestChesJsonArtifactChairs:
    """CHES 2025+ JSON contains an ``artifact_chairs`` field."""

    def test_chairs_from_json_artifact_chairs(self):
        data = {
            "committee": [
                {"name": "Alice A", "affiliation": "Uni A"},
                {"name": "Bob B", "affiliation": "Uni B"},
            ],
            "artifact_chairs": [
                {"name": "Carol C", "affiliation": "Uni C"},
            ],
        }
        session = _mock_session(json_data=data)
        result = scrape_ches_committee(2025, session=session)

        names = {m["name"] for m in result}
        assert "Carol C" in names
        chairs = [m for m in result if m["role"] == "chair"]
        assert len(chairs) == 1
        assert chairs[0]["name"] == "Carol C"
        assert chairs[0]["affiliation"] == "Uni C"

    def test_members_are_not_chairs(self):
        data = {
            "committee": [
                {"name": "Alice A", "affiliation": "Uni A"},
            ],
            "artifact_chairs": [
                {"name": "Carol C", "affiliation": "Uni C"},
            ],
        }
        session = _mock_session(json_data=data)
        result = scrape_ches_committee(2025, session=session)

        members = [m for m in result if m["role"] == "member"]
        assert len(members) == 1
        assert members[0]["name"] == "Alice A"


class TestChesJsonChairAnnotation:
    """CHES 2024 embeds ``(Chair)`` in the committee member name."""

    def test_chair_annotation_stripped_and_role_set(self):
        data = {
            "committee": [
                {"name": "Alice A", "affiliation": "Uni A"},
                {"name": "Markku-Juhani O. Saarinen (Chair)", "affiliation": "Tampere University"},
            ],
        }
        session = _mock_session(json_data=data)
        result = scrape_ches_committee(2024, session=session)

        saarinen = next(m for m in result if "Saarinen" in m["name"])
        assert saarinen["role"] == "chair"
        assert saarinen["name"] == "Markku-Juhani O. Saarinen"
        assert "(Chair)" not in saarinen["name"]

    def test_co_chair_annotation(self):
        data = {
            "committee": [
                {"name": "Alice A (Co-Chair)", "affiliation": "Uni A"},
                {"name": "Bob B (Co-Chair)", "affiliation": "Uni B"},
                {"name": "Carol C", "affiliation": "Uni C"},
            ],
        }
        session = _mock_session(json_data=data)
        result = scrape_ches_committee(2025, session=session)

        chairs = [m for m in result if m["role"] == "chair"]
        members = [m for m in result if m["role"] == "member"]
        assert len(chairs) == 2
        assert len(members) == 1
        assert all("(Co-Chair)" not in c["name"] for c in chairs)


class TestChesDedup:
    """Chair appearing in both JSON chairs and HTML should be deduped."""

    def test_json_chair_takes_precedence_over_html(self):
        data = {
            "committee": [
                {"name": "Alice A", "affiliation": "Uni A"},
            ],
            "artifact_chairs": [
                {"name": "Carol C", "affiliation": "Uni C"},
            ],
        }
        # HTML also lists Carol as chair
        html = """
        <h3>Artifact Review Chair</h3>
        <div class="row"><aside><h4>Carol C</h4><p>Uni C</p></aside></div>
        """
        session = _mock_session(json_data=data, html_text=html)
        result = scrape_ches_committee(2025, session=session)

        # Carol should appear only once
        carol_entries = [m for m in result if m["name"] == "Carol C"]
        assert len(carol_entries) == 1
        assert carol_entries[0]["role"] == "chair"


class TestChesHtmlFallback:
    """When JSON has no members, HTML <ul> parsing kicks in (CHES 2022)."""

    def test_html_members_and_chairs(self):
        html = """
        <h3>Artifact Review Chair</h3>
        <div class="row"><aside><h4>Chair Person</h4><p>Chair Uni</p></aside></div>
        <h3>Artifact Review Committee Members</h3>
        <ul>
          <li>Alice A (Uni A, Country)</li>
          <li>Bob B (Uni B)</li>
        </ul>
        """
        session = _mock_session(json_data={}, json_status=404, html_text=html)
        result = scrape_ches_committee(2022, session=session)

        assert len(result) == 3
        chairs = [m for m in result if m["role"] == "chair"]
        assert len(chairs) == 1
        assert chairs[0]["name"] == "Chair Person"


class TestChesJsonOnlyFallback:
    """When HTML fetch fails, JSON-only results (including chairs) returned."""

    def test_json_only_with_chairs(self):
        data = {
            "committee": [
                {"name": "Alice A", "affiliation": "Uni A"},
            ],
            "artifact_chairs": [
                {"name": "Carol C", "affiliation": "Uni C"},
            ],
        }
        session = _mock_session(json_data=data, html_status=404)
        # Override HTML get to raise
        original_get = session.get

        def failing_html_get(url, **kwargs):
            if url.endswith(".php"):
                resp = MagicMock()
                resp.status_code = 404
                return resp
            return original_get(url, **kwargs)

        session.get = failing_html_get
        result = scrape_ches_committee(2025, session=session)

        assert len(result) == 2
        chairs = [m for m in result if m["role"] == "chair"]
        assert len(chairs) == 1
        assert chairs[0]["name"] == "Carol C"
