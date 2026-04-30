"""Tests for src/generators/generate_search_data."""

import json

import pytest

from src.generators.output.generate_search_data import _title_key, generate_search_data


class TestTitleKey:
    def test_basic(self):
        assert _title_key("My Cool Paper") == "mycoolpaper"

    def test_punctuation_stripped(self):
        assert _title_key("Hello, World! (2023)") == "helloworld2023"

    def test_case_insensitive(self):
        assert _title_key("ABC") == _title_key("abc")

    def test_empty(self):
        assert _title_key("") == ""


class TestGenerateSearchData:
    @pytest.fixture()
    def data_dir(self, tmp_path):
        assets = tmp_path / "assets" / "data"
        assets.mkdir(parents=True)
        return tmp_path

    def _write(self, data_dir, filename, data):
        path = data_dir / "assets" / "data" / filename
        with open(path, "w") as f:
            json.dump(data, f)

    def test_basic_merge(self, data_dir):
        self._write(
            data_dir,
            "artifacts.json",
            [
                {
                    "title": "My Paper",
                    "conference": "OSDI",
                    "category": "systems",
                    "year": 2023,
                    "badges": ["available"],
                    "artifact_urls": ["https://github.com/user/repo"],
                },
            ],
        )
        self._write(
            data_dir,
            "paper_authors_map.json",
            [{"title": "My Paper", "authors": ["Alice Smith"], "doi_url": "https://doi.org/10.1234/test"}],
        )
        self._write(
            data_dir,
            "authors.json",
            [{"name": "Alice Smith", "display_name": "Alice Smith", "affiliation": "MIT"}],
        )

        result = generate_search_data(str(data_dir))
        assert len(result) == 1
        assert result[0]["authors"] == ["Alice Smith"]
        assert result[0]["affiliations"] == ["MIT"]
        assert result[0]["doi_url"] == "https://doi.org/10.1234/test"

    def test_missing_authors_file(self, data_dir):
        self._write(
            data_dir,
            "artifacts.json",
            [
                {
                    "title": "Paper",
                    "conference": "SOSP",
                    "category": "systems",
                    "year": 2023,
                    "badges": [],
                    "artifact_urls": [],
                },
            ],
        )
        result = generate_search_data(str(data_dir))
        assert len(result) == 1
        assert result[0]["authors"] == []
        assert result[0]["affiliations"] == []

    def test_disambiguation_suffix_stripped(self, data_dir):
        self._write(
            data_dir,
            "artifacts.json",
            [
                {
                    "title": "Paper",
                    "conference": "OSDI",
                    "category": "systems",
                    "year": 2023,
                    "badges": [],
                    "artifact_urls": [],
                },
            ],
        )
        self._write(
            data_dir,
            "paper_authors_map.json",
            [{"title": "Paper", "authors": ["Haibo Chen 0001"]}],
        )
        result = generate_search_data(str(data_dir))
        assert result[0]["authors"] == ["Haibo Chen"]

    def test_sorted_by_year_desc(self, data_dir):
        self._write(
            data_dir,
            "artifacts.json",
            [
                {
                    "title": "Old",
                    "conference": "OSDI",
                    "category": "systems",
                    "year": 2020,
                    "badges": [],
                    "artifact_urls": [],
                },
                {
                    "title": "New",
                    "conference": "OSDI",
                    "category": "systems",
                    "year": 2024,
                    "badges": [],
                    "artifact_urls": [],
                },
            ],
        )
        result = generate_search_data(str(data_dir))
        assert result[0]["year"] == 2024
        assert result[1]["year"] == 2020

    def test_output_file_written(self, data_dir):
        self._write(
            data_dir,
            "artifacts.json",
            [
                {
                    "title": "Paper",
                    "conference": "OSDI",
                    "category": "systems",
                    "year": 2023,
                    "badges": ["available"],
                    "artifact_urls": [],
                },
            ],
        )
        generate_search_data(str(data_dir))
        out = data_dir / "assets" / "data" / "search_data.json"
        assert out.exists()
        with open(out) as f:
            data = json.load(f)
        assert len(data) == 1
