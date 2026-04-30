"""Tests for paper index generation (generate_paper_index.py)."""

import json

from src.generators.output.generate_paper_index import (
    build_paper_index,
    load_existing_index,
    normalize_title,
)


class TestNormalizeTitle:
    def test_basic(self):
        assert normalize_title("Hello World!") == "hello world"

    def test_strips_punctuation_and_extra_whitespace(self):
        assert normalize_title("  A  Title:  With -- Extras!  ") == "a title with extras"

    def test_empty_string(self):
        assert normalize_title("") == ""

    def test_none(self):
        assert normalize_title(None) == ""


class TestBuildPaperIndex:
    def _make_author(self, name, papers=None, papers_without_artifacts=None):
        return {
            "name": name,
            "papers": papers or [],
            "papers_without_artifacts": papers_without_artifacts or [],
        }

    def _make_paper(self, title, conference="SOSP", year=2023, badges=None, artifact_citations=0, category="systems"):
        return {
            "title": title,
            "conference": conference,
            "year": year,
            "badges": badges or [],
            "artifact_citations": artifact_citations,
            "category": category,
        }

    def test_deduplicates_coauthored_papers(self):
        paper = self._make_paper("My Great Paper")
        authors = [
            self._make_author("Alice", papers=[paper]),
            self._make_author("Bob", papers=[paper]),
        ]
        papers, norm_to_id = build_paper_index(authors, {}, 0)
        assert len(papers) == 1
        assert papers[0]["title"] == "My Great Paper"

    def test_assigns_sequential_ids(self):
        authors = [
            self._make_author(
                "Alice",
                papers=[
                    self._make_paper("Paper A"),
                    self._make_paper("Paper B"),
                ],
            ),
        ]
        papers, norm_to_id = build_paper_index(authors, {}, 0)
        ids = sorted(p["id"] for p in papers)
        assert ids == [1, 2]

    def test_preserves_existing_ids(self):
        existing = {
            "paper a": {"id": 42, "title": "Paper A"},
        }
        authors = [
            self._make_author(
                "Alice",
                papers=[
                    self._make_paper("Paper A"),
                    self._make_paper("Paper B"),
                ],
            ),
        ]
        papers, norm_to_id = build_paper_index(authors, existing, 42)
        id_map = {normalize_title(p["title"]): p["id"] for p in papers}
        assert id_map["paper a"] == 42  # preserved
        assert id_map["paper b"] == 43  # new, starts after max_id

    def test_keeps_highest_citation_count(self):
        p1 = self._make_paper("Same Paper", artifact_citations=10)
        p2 = self._make_paper("Same Paper", artifact_citations=25)
        authors = [
            self._make_author("Alice", papers=[p1]),
            self._make_author("Bob", papers=[p2]),
        ]
        papers, _ = build_paper_index(authors, {}, 0)
        assert len(papers) == 1
        assert papers[0]["artifact_citations"] == 25

    def test_papers_without_artifacts_marked(self):
        authors = [
            self._make_author("Alice", papers_without_artifacts=[self._make_paper("No Artifact Paper")]),
        ]
        papers, _ = build_paper_index(authors, {}, 0)
        assert len(papers) == 1
        assert papers[0]["has_artifact"] is False

    def test_papers_with_artifacts_default_true(self):
        authors = [
            self._make_author("Alice", papers=[self._make_paper("Artifact Paper")]),
        ]
        papers, _ = build_paper_index(authors, {}, 0)
        assert papers[0]["has_artifact"] is True

    def test_norm_to_id_mapping(self):
        authors = [
            self._make_author("Alice", papers=[self._make_paper("Paper A")]),
        ]
        papers, norm_to_id = build_paper_index(authors, {}, 0)
        assert norm_to_id["paper a"] == papers[0]["id"]

    def test_empty_titles_skipped(self):
        authors = [
            self._make_author(
                "Alice",
                papers=[
                    self._make_paper(""),
                    self._make_paper("Good Title"),
                ],
            ),
        ]
        papers, _ = build_paper_index(authors, {}, 0)
        assert len(papers) == 1
        assert papers[0]["title"] == "Good Title"


class TestLoadExistingIndex:
    def test_returns_empty_for_missing_file(self, tmp_path):
        entries, by_title = load_existing_index(str(tmp_path / "nonexistent.json"))
        assert entries == []
        assert by_title == {}

    def test_loads_existing_file(self, tmp_path):
        data = [
            {"id": 1, "title": "Paper A"},
            {"id": 2, "title": "Paper B"},
        ]
        path = tmp_path / "papers.json"
        path.write_text(json.dumps(data))
        entries, by_title = load_existing_index(str(path))
        assert len(entries) == 2
        assert by_title["paper a"]["id"] == 1
