"""Pydantic data models for all pipeline output formats.

These models are the single source of truth for data schemas.
JSON Schema files are generated from these models via ``export_schemas.py``.
"""

SCHEMA_VERSION = "0.2.0"
"""Semantic version for the schema bundle.

Bump this when any model changes:
- MAJOR: breaking changes (removed/renamed fields, tightened constraints)
- MINOR: backwards-compatible additions (new optional fields)
- PATCH: documentation-only or metadata changes
"""
