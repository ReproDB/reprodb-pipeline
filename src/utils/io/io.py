"""Safe JSON and YAML file I/O helpers.

All generators and enrichers should use these helpers instead of raw
``json.load`` / ``yaml.safe_load`` to get consistent error messages and
graceful handling of missing or malformed files.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, TypeAdapter

logger = logging.getLogger(__name__)


def load_json(path: str | Path, *, default: Any = None) -> Any:
    """Read and parse a JSON file.

    Returns *default* (``None``) when the file is missing or unparseable,
    logging a warning in either case.
    """
    path = Path(path)
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        logger.warning("File not found: %s", path)
        return default
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return default


def save_json(
    path: str | Path,
    data: Any,
    *,
    indent: int | None = 2,
    compact: bool = False,
) -> None:
    """Write *data* as JSON, creating parent dirs as needed.

    *indent* controls pretty-printing (default ``2``).  Pass ``indent=None``
    to omit indentation but keep standard separators.  Set ``compact=True``
    for fully minified output (no whitespace).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = {"ensure_ascii": False}
    if compact:
        kwargs["separators"] = (",", ":")
    else:
        kwargs["indent"] = indent
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, **kwargs)
        fh.write("\n")


def save_validated_json(
    path: str | Path,
    data: Any,
    model: type[BaseModel],
    *,
    indent: int | None = 2,
    compact: bool = False,
) -> None:
    """Validate *data* against a Pydantic *model* and write as JSON.

    When *data* is a list, each item is validated individually.  A single
    ``BaseModel`` instance or dict is validated as-is.

    Validation errors are logged and re-raised so callers can decide how to
    handle them.  The ``TypeAdapter`` approach supports both ``list[Model]``
    and single-model payloads.
    """
    tp = list[model] if isinstance(data, list) else model  # type: ignore[valid-type]
    adapter = TypeAdapter(tp)
    validated = adapter.validate_python(data)
    # Serialize through Pydantic so aliases and custom encoders are honoured
    serialized = adapter.dump_python(validated, mode="python")
    save_json(path, serialized, indent=indent, compact=compact)


def load_yaml(path: str | Path, *, default: Any = None) -> Any:
    """Read and parse a YAML file.

    Returns *default* (``None``) when the file is missing or unparseable,
    logging a warning in either case.
    """
    path = Path(path)
    try:
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except FileNotFoundError:
        logger.warning("File not found: %s", path)
        return default
    except (yaml.YAMLError, OSError) as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return default


def save_yaml(path: str | Path, data: Any) -> None:
    """Write *data* as YAML, creating parent dirs as needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)


# ── Data-path resolution ────────────────────────────────────────────────────


def resolve_data_path(data_dir: str | Path, filename: str) -> Path:
    """Return ``_build/{filename}`` if it exists, else ``assets/data/{filename}``.

    Many generators write intermediate files to ``_build/`` but older pipeline
    runs stored them under ``assets/data/``.  This helper centralises the
    fallback logic so individual generators don't need to duplicate it.
    """
    data_dir = Path(data_dir)
    build_path = data_dir / "_build" / filename
    if build_path.exists():
        return build_path
    return data_dir / "assets" / "data" / filename


# ── Validated loading ────────────────────────────────────────────────────────


def load_validated_json(
    path: str | Path,
    model: type[BaseModel],
    *,
    default: Any = None,
) -> Any:
    """Load a JSON file and validate/migrate each record through a Pydantic model.

    Old field names are automatically mapped via ``@model_validator(mode="before")``
    or ``validation_alias`` defined on the model.  For list data, each item is
    validated individually; for dict/object data, the whole payload is validated
    as a single model instance.

    Returns *default* when the file is missing or unparseable (same behaviour
    as :func:`load_json`).
    """
    raw = load_json(path, default=default)
    if raw is default:
        return default
    try:
        if isinstance(raw, list):
            adapter = TypeAdapter(list[model])  # type: ignore[valid-type]
        else:
            adapter = TypeAdapter(model)
        return adapter.validate_python(raw)
    except Exception:
        logger.warning("Validation failed for %s, returning raw data", path, exc_info=True)
        return raw
