"""
Utilities for reading and updating the canonical author index.

The author index (``author_index.json``) is the single source of truth for
author identity (stable integer IDs) and affiliation data.  Enrichers call
``update_author_affiliation()`` to write back discovered affiliations with
proper source tracking and history.
"""

import json
import os
from datetime import datetime
from typing import Optional

from src.utils.io.io import resolve_data_path


def load_author_index(data_dir: str) -> tuple[list, dict[str, dict]]:
    """Load ``author_index.json`` and return (entries, name→entry dict).

    ``data_dir`` is the website repo root (contains ``_build/`` and ``assets/data/``).
    """
    from pathlib import Path

    path = resolve_data_path(Path(data_dir), "author_index.json")
    if not path.exists():
        return [], {}
    with open(path, "r", encoding="utf-8") as f:
        entries = json.load(f)
    by_name = {e["name"]: e for e in entries}
    return entries, by_name


def build_name_to_id(data_dir: str) -> dict[str, int]:
    """Return a ``{name: author_id}`` dict. Returns empty dict if no index."""
    _, by_name = load_author_index(data_dir)
    return {name: entry["id"] for name, entry in by_name.items()}


def save_author_index(data_dir: str, entries: list[dict]) -> str:
    """Write ``author_index.json`` back to disk.  Returns the file path."""
    path = os.path.join(data_dir, "_build", "author_index.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)
    return path


def update_author_affiliation(
    entry: dict,
    new_affiliation: str,
    source: str,
    *,
    external_id_key: Optional[str] = None,
    external_id_value: Optional[str] = None,
) -> bool:
    """Update an index entry's affiliation if it changed.

    Sets ``affiliation``, ``affiliation_source``, ``affiliation_updated``
    and appends the old value to ``affiliation_history`` when the affiliation
    actually changes.

    Optionally records an external ID (e.g. ``dblp_pid``, ``openalex_id``).

    Returns True if the entry was modified.
    """
    if not new_affiliation:
        # Nothing to do — caller found no affiliation
        # But still record external ID if provided
        if external_id_key and external_id_value:
            entry.setdefault("external_ids", {})[external_id_key] = external_id_value
            return True
        return False

    today = datetime.now().strftime("%Y-%m-%d")
    old_affiliation = entry.get("affiliation", "")
    changed = False

    if new_affiliation != old_affiliation:
        # Record old value in history
        if old_affiliation:
            entry.setdefault("affiliation_history", []).append(
                {
                    "affiliation": old_affiliation,
                    "source": entry.get("affiliation_source", ""),
                    "date": entry.get("affiliation_updated", ""),
                }
            )
        entry["affiliation"] = new_affiliation
        entry["affiliation_source"] = source
        entry["affiliation_updated"] = today
        changed = True
    elif entry.get("affiliation_source", "") != source:
        # Same affiliation but different/better source
        entry["affiliation_source"] = source
        entry["affiliation_updated"] = today
        changed = True

    if external_id_key and external_id_value:
        entry.setdefault("external_ids", {})[external_id_key] = external_id_value
        changed = True

    return changed
