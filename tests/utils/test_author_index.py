"""Tests for src.utils.author_index — the shared author index utility."""

import os

from src.utils.normalization.author_index import (
    build_name_to_id,
    load_author_index,
    save_author_index,
    update_author_affiliation,
)
from tests.conftest import read_json, write_json

# ── load_author_index ────────────────────────────────────────────────────────


class TestLoadAuthorIndex:
    def test_missing_file_returns_empty(self, tmp_website):
        entries, by_name = load_author_index(str(tmp_website))
        assert entries == []
        assert by_name == {}

    def test_loads_existing_index(self, tmp_website, sample_index):
        write_json(str(tmp_website / "assets" / "data" / "author_index.json"), sample_index)
        entries, by_name = load_author_index(str(tmp_website))
        assert len(entries) == 2
        assert "Alice Smith" in by_name
        assert by_name["Alice Smith"]["id"] == 1

    def test_name_key_maps_correctly(self, tmp_website, sample_index):
        write_json(str(tmp_website / "assets" / "data" / "author_index.json"), sample_index)
        _, by_name = load_author_index(str(tmp_website))
        for entry in sample_index:
            assert by_name[entry["name"]]["id"] == entry["id"]


# ── build_name_to_id ─────────────────────────────────────────────────────────


class TestBuildNameToId:
    def test_empty_when_no_file(self, tmp_website):
        result = build_name_to_id(str(tmp_website))
        assert result == {}

    def test_returns_name_to_int_mapping(self, tmp_website, sample_index):
        write_json(str(tmp_website / "assets" / "data" / "author_index.json"), sample_index)
        result = build_name_to_id(str(tmp_website))
        assert result == {"Alice Smith": 1, "Bob Jones": 2}
        assert all(isinstance(v, int) for v in result.values())


# ── save_author_index ─────────────────────────────────────────────────────────


class TestSaveAuthorIndex:
    def test_creates_file(self, tmp_website, sample_index):
        path = save_author_index(str(tmp_website), sample_index)
        assert os.path.exists(path)
        data = read_json(path)
        assert len(data) == 2

    def test_creates_missing_directories(self, tmp_path):
        # tmp_path has no assets/data/ yet
        root = tmp_path / "new_root"
        path = save_author_index(str(root), [{"id": 1, "name": "Test"}])
        assert os.path.exists(path)

    def test_roundtrip(self, tmp_website, sample_index):
        save_author_index(str(tmp_website), sample_index)
        entries, _ = load_author_index(str(tmp_website))
        assert entries == sample_index


# ── update_author_affiliation ─────────────────────────────────────────────────


class TestUpdateAuthorAffiliation:
    def _make_entry(self, **overrides):
        base = {
            "id": 1,
            "name": "Test Author",
            "display_name": "Test Author",
            "affiliation": "",
            "affiliation_source": "",
            "affiliation_updated": "",
            "affiliation_history": [],
            "external_ids": {},
            "category": "systems",
        }
        base.update(overrides)
        return base

    def test_empty_affiliation_returns_false(self):
        entry = self._make_entry()
        assert update_author_affiliation(entry, "", "csrankings") is False
        assert entry["affiliation"] == ""

    def test_empty_affiliation_with_external_id(self):
        entry = self._make_entry()
        result = update_author_affiliation(entry, "", "dblp", external_id_key="dblp_pid", external_id_value="p/TestA")
        assert result is True
        assert entry["external_ids"]["dblp_pid"] == "p/TestA"
        assert entry["affiliation"] == ""

    def test_new_affiliation_sets_all_fields(self):
        entry = self._make_entry()
        result = update_author_affiliation(entry, "MIT", "csrankings")
        assert result is True
        assert entry["affiliation"] == "MIT"
        assert entry["affiliation_source"] == "csrankings"
        assert entry["affiliation_updated"]  # non-empty date string
        assert entry["affiliation_history"] == []  # no old value to archive

    def test_changed_affiliation_archives_old(self):
        entry = self._make_entry(
            affiliation="Stanford",
            affiliation_source="dblp",
            affiliation_updated="2024-01-01",
        )
        result = update_author_affiliation(entry, "MIT", "csrankings")
        assert result is True
        assert entry["affiliation"] == "MIT"
        assert entry["affiliation_source"] == "csrankings"
        assert len(entry["affiliation_history"]) == 1
        hist = entry["affiliation_history"][0]
        assert hist["affiliation"] == "Stanford"
        assert hist["source"] == "dblp"
        assert hist["date"] == "2024-01-01"

    def test_same_affiliation_different_source_updates_source(self):
        entry = self._make_entry(
            affiliation="MIT",
            affiliation_source="dblp",
            affiliation_updated="2024-01-01",
        )
        result = update_author_affiliation(entry, "MIT", "csrankings")
        assert result is True
        assert entry["affiliation_source"] == "csrankings"
        assert entry["affiliation_history"] == []  # no change to archive

    def test_same_affiliation_same_source_no_change(self):
        entry = self._make_entry(
            affiliation="MIT",
            affiliation_source="csrankings",
            affiliation_updated="2024-01-01",
        )
        result = update_author_affiliation(entry, "MIT", "csrankings")
        assert result is False

    def test_external_id_recorded_alongside_affiliation(self):
        entry = self._make_entry()
        update_author_affiliation(
            entry, "ETH Zurich", "openalex", external_id_key="openalex_id", external_id_value="A12345"
        )
        assert entry["affiliation"] == "ETH Zurich"
        assert entry["external_ids"]["openalex_id"] == "A12345"

    def test_multiple_changes_build_history(self):
        entry = self._make_entry()
        update_author_affiliation(entry, "MIT", "dblp")
        update_author_affiliation(entry, "Stanford", "csrankings")
        update_author_affiliation(entry, "Google", "openalex")
        assert len(entry["affiliation_history"]) == 2
        assert entry["affiliation_history"][0]["affiliation"] == "MIT"
        assert entry["affiliation_history"][1]["affiliation"] == "Stanford"
        assert entry["affiliation"] == "Google"
