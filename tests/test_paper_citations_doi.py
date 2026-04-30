"""Tests for src.generators.generate_paper_citations_doi."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.generators.citations.generate_paper_citations_doi import (
    _extract_paper_doi,
    _openalex_lookup,
    _openalex_title_search,
    _s2_lookup,
    _update_history,
    generate,
)

# ── DOI extraction ───────────────────────────────────────────────────────────


class TestExtractPaperDoi:
    def test_bare_doi(self):
        assert _extract_paper_doi("10.1145/3447786.3456244") == "10.1145/3447786.3456244"

    def test_doi_url(self):
        assert _extract_paper_doi("https://doi.org/10.1145/3447786.3456244") == "10.1145/3447786.3456244"

    def test_http_doi_url(self):
        assert _extract_paper_doi("http://doi.org/10.1145/3447786.3456244") == "10.1145/3447786.3456244"

    def test_non_doi(self):
        assert _extract_paper_doi("https://www.usenix.org/system/files/fast24-cho.pdf") == ""

    def test_none(self):
        assert _extract_paper_doi(None) == ""

    def test_empty(self):
        assert _extract_paper_doi("") == ""

    def test_trailing_punctuation(self):
        assert _extract_paper_doi("10.1145/1234567.1234568.") == "10.1145/1234567.1234568"


# ── API callers (mocked) ────────────────────────────────────────────────────


class TestOpenAlexLookup:
    def test_returns_data_on_success(self):
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "cited_by_count": 42,
            "id": "https://openalex.org/W123",
            "title": "Example Paper",
        }
        resp.raise_for_status = MagicMock()
        session.get.return_value = resp

        result = _openalex_lookup("10.1145/1234", session)
        assert result is not None
        assert result["cited_by_count"] == 42
        assert result["openalex_id"] == "https://openalex.org/W123"

    def test_returns_none_on_404(self):
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 404
        session.get.return_value = resp

        assert _openalex_lookup("10.9999/nonexistent", session) is None

    def test_returns_none_on_error(self):
        import requests

        session = MagicMock()
        session.get.side_effect = requests.ConnectionError
        assert _openalex_lookup("10.1145/1234", session) is None


class TestOpenAlexTitleSearch:
    def test_returns_best_match(self):
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "results": [
                {
                    "title": "My Cool Paper on Systems",
                    "cited_by_count": 10,
                    "id": "https://openalex.org/W999",
                }
            ]
        }
        resp.raise_for_status = MagicMock()
        session.get.return_value = resp

        result = _openalex_title_search("My Cool Paper on Systems", session)
        assert result is not None
        assert result["cited_by_count"] == 10

    def test_rejects_low_similarity(self):
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "results": [
                {
                    "title": "Completely Unrelated Paper",
                    "cited_by_count": 100,
                    "id": "https://openalex.org/W000",
                }
            ]
        }
        resp.raise_for_status = MagicMock()
        session.get.return_value = resp

        assert _openalex_title_search("My Cool Paper on Systems", session) is None


class TestS2Lookup:
    def test_returns_count(self):
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"citationCount": 55}
        resp.raise_for_status = MagicMock()
        session.get.return_value = resp

        assert _s2_lookup("10.1145/1234", session) == 55

    def test_returns_none_on_404(self):
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 404
        session.get.return_value = resp

        assert _s2_lookup("10.9999/bad", session) is None


# ── History tracking ─────────────────────────────────────────────────────────


class TestUpdateHistory:
    def test_creates_new_history(self, tmp_path):
        entries = [
            {
                "title": "Paper A",
                "conference": "SOSP",
                "year": 2023,
                "category": "systems",
                "paper_doi": "10.1145/1111",
                "cited_by_count": 20,
                "citations_openalex": 20,
                "citations_semantic_scholar": 18,
                "source": "openalex_doi",
                "openalex_id": "",
            }
        ]
        _update_history(entries, tmp_path)
        history = json.loads((tmp_path / "citation_history.json").read_text())
        assert len(history) == 1
        key = list(history.keys())[0]
        assert key.startswith("SOSP/2023/")
        assert history[key]["meta"]["paper_doi"] == "10.1145/1111"
        assert len(history[key]["snapshots"]) == 1
        assert history[key]["snapshots"][0]["cited_by_count"] == 20

    def test_same_day_idempotent(self, tmp_path):
        entries = [
            {
                "title": "Paper B",
                "conference": "ATC",
                "year": 2024,
                "paper_doi": "10.1145/2222",
                "cited_by_count": 5,
                "citations_openalex": 5,
                "citations_semantic_scholar": None,
                "source": "openalex_doi",
                "openalex_id": "",
            }
        ]
        _update_history(entries, tmp_path)
        # Update with new count same day
        entries[0]["cited_by_count"] = 7
        entries[0]["citations_openalex"] = 7
        _update_history(entries, tmp_path)
        history = json.loads((tmp_path / "citation_history.json").read_text())
        key = list(history.keys())[0]
        assert len(history[key]["snapshots"]) == 1
        assert history[key]["snapshots"][0]["cited_by_count"] == 7

    def test_skips_entries_without_citations(self, tmp_path):
        entries = [
            {
                "title": "No Data",
                "conference": "FAST",
                "year": 2024,
                "paper_doi": "",
                "cited_by_count": None,
                "citations_openalex": None,
                "citations_semantic_scholar": None,
                "source": "",
                "openalex_id": "",
            }
        ]
        _update_history(entries, tmp_path)
        history = json.loads((tmp_path / "citation_history.json").read_text())
        assert len(history) == 0


# ── Full generate (mocked) ──────────────────────────────────────────────────


class TestGenerate:
    @pytest.fixture()
    def data_dir(self, tmp_path):
        """Create a minimal data_dir with artifacts.json."""
        assets = tmp_path / "assets" / "data"
        assets.mkdir(parents=True)
        artifacts = [
            {
                "title": "Paper X",
                "conference": "SOSP",
                "year": 2023,
                "category": "systems",
                "paper_url": "10.1145/3600006.3613155",
                "artifact_urls": [],
                "badges": ["available"],
            },
            {
                "title": "Paper Y",
                "conference": "ATC",
                "year": 2023,
                "category": "systems",
                "paper_url": "https://www.usenix.org/paper.pdf",
                "artifact_urls": [],
                "badges": [],
            },
        ]
        (assets / "artifacts.json").write_text(json.dumps(artifacts))
        return tmp_path

    @patch("src.generators.citations.generate_paper_citations_doi._s2_lookup", return_value=None)
    @patch(
        "src.generators.citations.generate_paper_citations_doi._openalex_title_search",
        return_value={"cited_by_count": 3, "openalex_id": "W3", "title": "Paper Y"},
    )
    @patch(
        "src.generators.citations.generate_paper_citations_doi._openalex_lookup",
        return_value={"cited_by_count": 10, "openalex_id": "W1", "title": "Paper X"},
    )
    @patch("src.generators.citations.generate_paper_citations_doi.time.sleep")
    @patch("src.generators.citations.generate_paper_citations_doi.read_cache", return_value=_extract_paper_doi)
    def test_generate_writes_output(self, mock_cache, mock_sleep, mock_oa, mock_oa_title, mock_s2, data_dir):
        # Make cache always miss
        from src.utils.io.cache import _MISSING

        mock_cache.return_value = _MISSING

        entries = generate(str(data_dir))
        assert entries is not None
        assert len(entries) == 2

        # Paper X — DOI lookup
        assert entries[0]["paper_doi"] == "10.1145/3600006.3613155"
        assert entries[0]["cited_by_count"] == 10

        # Paper Y — title search fallback (no DOI)
        assert entries[1]["paper_doi"] == ""
        assert entries[1]["cited_by_count"] == 3

        # Check output files
        assert (data_dir / "_build" / "paper_citations.json").exists()
        assert (data_dir / "_build" / "citation_history.json").exists()
