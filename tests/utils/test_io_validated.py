"""Tests for load_validated_json() and resolve_data_path() in src.utils.io."""

import json

from pydantic import BaseModel, Field, model_validator

from src.utils.io.io import load_validated_json, resolve_data_path

# ── Test model ───────────────────────────────────────────────────────────────


class _SampleModel(BaseModel):
    name: str
    value: int = Field(ge=0)

    @model_validator(mode="before")
    @classmethod
    def _migrate(cls, data):
        if isinstance(data, dict) and "val" in data and "value" not in data:
            data["value"] = data.pop("val")
        return data

    model_config = {"extra": "forbid"}


# ── resolve_data_path ────────────────────────────────────────────────────────


class TestResolveDataPath:
    def test_prefers_build_dir(self, tmp_path):
        build = tmp_path / "_build"
        build.mkdir()
        (build / "data.json").write_text("{}")
        (tmp_path / "assets" / "data").mkdir(parents=True)
        (tmp_path / "assets" / "data" / "data.json").write_text("{}")

        result = resolve_data_path(tmp_path, "data.json")
        assert result == build / "data.json"

    def test_falls_back_to_assets_data(self, tmp_path):
        (tmp_path / "assets" / "data").mkdir(parents=True)
        (tmp_path / "assets" / "data" / "data.json").write_text("{}")

        result = resolve_data_path(tmp_path, "data.json")
        assert result == tmp_path / "assets" / "data" / "data.json"

    def test_returns_assets_data_path_when_neither_exists(self, tmp_path):
        result = resolve_data_path(tmp_path, "data.json")
        assert result == tmp_path / "assets" / "data" / "data.json"

    def test_accepts_string_data_dir(self, tmp_path):
        result = resolve_data_path(str(tmp_path), "data.json")
        assert result == tmp_path / "assets" / "data" / "data.json"


# ── load_validated_json ──────────────────────────────────────────────────────


class TestLoadValidatedJson:
    def test_validates_list_data(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text(json.dumps([{"name": "a", "value": 1}, {"name": "b", "value": 2}]))
        result = load_validated_json(f, _SampleModel)
        assert len(result) == 2
        assert isinstance(result[0], _SampleModel)
        assert result[0].name == "a"

    def test_validates_single_object(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text(json.dumps({"name": "x", "value": 10}))
        result = load_validated_json(f, _SampleModel)
        assert isinstance(result, _SampleModel)
        assert result.name == "x"

    def test_migrates_old_field_names(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text(json.dumps([{"name": "old", "val": 5}]))
        result = load_validated_json(f, _SampleModel)
        assert result[0].value == 5

    def test_returns_default_on_missing_file(self, tmp_path):
        result = load_validated_json(tmp_path / "missing.json", _SampleModel)
        assert result is None

    def test_returns_custom_default_on_missing_file(self, tmp_path):
        result = load_validated_json(tmp_path / "missing.json", _SampleModel, default=[])
        assert result == []

    def test_returns_raw_on_validation_failure(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text(json.dumps([{"name": "bad", "value": -1}]))
        result = load_validated_json(f, _SampleModel)
        # Falls back to raw data since value=-1 violates ge=0
        assert isinstance(result, list)
        assert result[0]["name"] == "bad"
