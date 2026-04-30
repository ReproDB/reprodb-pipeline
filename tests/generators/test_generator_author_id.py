"""Tests for author_id injection across generators.

Verifies that when an author_index.json exists, each generator adds the
``author_id`` field to its output records.
"""

from tests.conftest import read_json, write_json

# ── Helper ────────────────────────────────────────────────────────────────────


def make_index(tmp_website, names_ids):
    """Write a minimal author_index.json.  *names_ids* is a list of (name, id)."""
    entries = [
        {
            "id": aid,
            "name": name,
            "display_name": name,
            "affiliation": "",
            "affiliation_source": "",
            "affiliation_updated": "",
            "affiliation_history": [],
            "external_ids": {},
            "category": "systems",
        }
        for name, aid in names_ids
    ]
    write_json(str(tmp_website / "assets" / "data" / "author_index.json"), entries)


# ── generate_author_profiles ─────────────────────────────────────────────────


class TestAuthorProfilesInjection:
    def test_author_id_injected(self, tmp_website, sample_authors, sample_index):
        from src.generators.authors.generate_author_profiles import generate_profiles

        # Write required input files
        write_json(str(tmp_website / "assets" / "data" / "authors.json"), sample_authors)
        write_json(str(tmp_website / "assets" / "data" / "ae_members.json"), [])
        write_json(str(tmp_website / "assets" / "data" / "combined_rankings.json"), [])
        write_json(
            str(tmp_website / "assets" / "data" / "author_index.json"),
            sample_index,
        )

        generate_profiles(str(tmp_website))

        profiles = read_json(str(tmp_website / "assets" / "data" / "author_profiles.json"))
        id_map = {p["name"]: p.get("author_id") for p in profiles}
        assert id_map["Alice Smith"] == 1
        assert id_map["Bob Jones"] == 2
        # Carol not in index → no author_id
        assert id_map.get("Carol White") is None

    def test_no_index_no_crash(self, tmp_website, sample_authors):
        """Works cleanly even when author_index.json doesn't exist."""
        from src.generators.authors.generate_author_profiles import generate_profiles

        write_json(str(tmp_website / "assets" / "data" / "authors.json"), sample_authors)
        write_json(str(tmp_website / "assets" / "data" / "ae_members.json"), [])
        write_json(str(tmp_website / "assets" / "data" / "combined_rankings.json"), [])

        generate_profiles(str(tmp_website))

        profiles = read_json(str(tmp_website / "assets" / "data" / "author_profiles.json"))
        # No author_id on any profile
        assert all("author_id" not in p for p in profiles)
