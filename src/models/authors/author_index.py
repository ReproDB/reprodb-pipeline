"""Author index schema — ``author_index.json``."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AffiliationHistoryEntry(BaseModel):
    """A previous affiliation with date, tracking institution moves."""

    affiliation: str = Field(description="Previous institution name, e.g. 'MIT', 'Google'.", examples=["ETH Zurich"])
    source: str = Field(
        description="Enrichment source that set this affiliation, e.g. 'dblp', 'openalex', 'manual'.",
        examples=["csrankings"],
    )
    date: str = Field(
        description="ISO 8601 date when this affiliation was recorded, e.g. '2025-01-15'.", examples=["2025-01-15"]
    )


class ExternalIds(BaseModel):
    """Optional identifiers from external databases."""

    dblp_pid: str | None = Field(
        default=None,
        description="DBLP person identifier, e.g. 'homepages/c/HaiboChen0001'. Used to link to DBLP author pages.",
        examples=["homepages/p/MathiasPayer"],
    )
    orcid: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$",
        description="ORCID identifier, e.g. '0000-0001-2345-6789'. 16-digit hyphenated format.",
        examples=["0000-0001-2345-6789"],
    )
    openalex_id: str | None = Field(
        default=None,
        description="OpenAlex author ID, e.g. 'A1234567890'. Used for citation lookups.",
        examples=["https://openalex.org/W4388218060"],
    )

    model_config = {"extra": "forbid"}


class AuthorIndexEntry(BaseModel):
    """Canonical author record: stable ID, name, affiliation, external identifiers, and enrichment history."""

    id: int = Field(
        ge=1,
        description="Stable integer identifier (starts at 1). Assigned once and never reused or changed across pipeline runs.",
        examples=[42],
    )
    name: str = Field(
        description=(
            "Full name in DBLP format. Includes disambiguation suffix "
            "when present (e.g., 'Haibo Chen 0001'). Unique across all authors."
        ),
        examples=["Mathias Payer"],
    )
    display_name: str = Field(
        description="Human-readable name without DBLP disambiguation suffix, e.g. 'Haibo Chen' (from 'Haibo Chen 0001').",
        examples=["Mathias Payer"],
    )
    affiliation: str = Field(
        description="Normalized institution affiliation, e.g. 'Shanghai Jiao Tong University'. Empty string if unknown.",
        examples=["ETH Zurich"],
    )
    affiliation_source: Literal["csrankings", "dblp", "openalex", "crossref", "manual", ""] = Field(
        default="",
        description="Which enrichment layer last set the affiliation: 'dblp', 'openalex', or 'manual'. Empty if never set.",
        examples=["dblp"],
    )
    affiliation_updated: str = Field(
        default="",
        pattern=r"^(\d{4}-\d{2}-\d{2})?$",
        description="ISO 8601 date when affiliation was last updated, e.g. '2026-04-07'. Empty string if never updated.",
        examples=["2025-03-15"],
    )
    affiliation_history: list[AffiliationHistoryEntry] = Field(
        default_factory=list,
        description="Previous affiliations with dates, tracking institution moves. Most recent first.",
    )
    external_ids: ExternalIds = Field(
        default_factory=ExternalIds,
        description="Optional identifiers from external databases (DBLP, ORCID, OpenAlex). Empty dict if none known.",
    )
    category: Literal["systems", "security", "both"] = Field(
        default="systems",
        description="Primary research area: 'systems', 'security', or 'both'. Based on conferences published at.",
    )

    model_config = {"extra": "forbid"}
