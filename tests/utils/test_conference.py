"""Tests for src.utils.conference – area classification and name helpers."""

from src.utils.normalization.conference import (
    ALL_CONFS,
    CONF_DISPLAY_NAMES,
    SECURITY_CONFS,
    SYSTEMS_CONFS,
    clean_name,
    conf_area,
    ensure_conference_pages,
    normalize_name,
    parse_conf_year,
)


class TestConfArea:
    def test_systems_conferences(self):
        for c in ("ATC", "CAIS", "EUROSYS", "FAST", "OSDI", "SC", "SOSP"):
            assert conf_area(c) == "systems"

    def test_security_conferences(self):
        for c in ("ACSAC", "CHES", "NDSS", "PETS", "SYSTEX", "USENIXSEC", "WOOT"):
            assert conf_area(c) == "security"

    def test_case_insensitive(self):
        assert conf_area("osdi") == "systems"
        assert conf_area("Ndss") == "security"

    def test_with_year_suffix(self):
        assert conf_area("osdi2024") == "systems"
        assert conf_area("usenixsec2023") == "security"

    def test_unknown(self):
        assert conf_area("FOOBAR") == "unknown"

    def test_all_confs_is_union(self):
        assert ALL_CONFS == SYSTEMS_CONFS | SECURITY_CONFS


class TestParseConfYear:
    def test_valid(self):
        assert parse_conf_year("osdi2024") == ("OSDI", 2024)
        assert parse_conf_year("NDSS2023") == ("NDSS", 2023)

    def test_no_year(self):
        name, year = parse_conf_year("OSDI")
        assert name == "OSDI"
        assert year is None

    def test_short_year(self):
        name, year = parse_conf_year("osdi24")
        assert name == "OSDI24"
        assert year is None  # only 4-digit years match


class TestCleanName:
    def test_strips_dblp_suffix(self):
        assert clean_name("Jane Doe 0001") == "Jane Doe"

    def test_collapses_whitespace(self):
        assert clean_name("Jane   Doe") == "Jane Doe"

    def test_empty(self):
        assert clean_name("") == ""

    def test_tabs_and_newlines(self):
        assert clean_name("Jane\tDoe\n") == "Jane Doe"


class TestNormalizeName:
    def test_lowercase_and_strip_accents(self):
        assert normalize_name("José García") == "jose garcia"

    def test_removes_dots(self):
        assert normalize_name("J. Smith") == "j smith"

    def test_strips_dblp_suffix(self):
        assert normalize_name("Jane Doe 0001") == "jane doe"

    def test_empty(self):
        assert normalize_name("") == ""


class TestEnsureConferencePages:
    """Tests for auto-creating conference .md pages."""

    def test_creates_missing_page(self, tmp_path):
        """A page is created for a conference dir not yet on the website."""
        (tmp_path / "content" / "systems").mkdir(parents=True)
        (tmp_path / "content" / "security").mkdir(parents=True)
        # Pre-existing page
        (tmp_path / "content" / "security" / "ndss.md").write_text("existing\n")

        created = ensure_conference_pages(
            sys_dirs=set(),
            sec_dirs={"ndss2024", "newconf2025"},
            website_root=str(tmp_path),
        )

        assert len(created) == 1
        page = tmp_path / "content" / "security" / "newconf.md"
        assert page.exists()
        content = page.read_text()
        assert 'conf_name: "NEWCONF"' in content
        assert "permalink: /security/newconf.html" in content
        assert "conference_page.html" in content

    def test_skips_existing_page(self, tmp_path):
        """No new page if the conference already has a .md file."""
        (tmp_path / "content" / "systems").mkdir(parents=True)
        (tmp_path / "content" / "security").mkdir(parents=True)
        (tmp_path / "content" / "security" / "ches.md").write_text("existing\n")

        created = ensure_conference_pages(
            sys_dirs=set(),
            sec_dirs={"ches2024"},
            website_root=str(tmp_path),
        )
        assert created == []

    def test_uses_display_name(self, tmp_path):
        """Pages use CONF_DISPLAY_NAMES when available."""
        (tmp_path / "content" / "systems").mkdir(parents=True)
        (tmp_path / "content" / "security").mkdir(parents=True)

        ensure_conference_pages(
            sys_dirs={"atc2024"},
            sec_dirs=set(),
            website_root=str(tmp_path),
        )
        content = (tmp_path / "content" / "systems" / "atc.md").read_text()
        assert CONF_DISPLAY_NAMES["ATC"] in content

    def test_deduplicates_across_years(self, tmp_path):
        """Multiple years of same conference produce only one page."""
        (tmp_path / "content" / "systems").mkdir(parents=True)
        (tmp_path / "content" / "security").mkdir(parents=True)

        created = ensure_conference_pages(
            sys_dirs={"osdi2023", "osdi2024"},
            sec_dirs=set(),
            website_root=str(tmp_path),
        )
        assert len(created) == 1

    def test_returns_empty_when_no_website(self, tmp_path):
        """Gracefully returns empty list when website root doesn't exist."""
        created = ensure_conference_pages(
            sys_dirs={"osdi2024"},
            sec_dirs=set(),
            website_root=str(tmp_path / "nonexistent"),
        )
        assert created == []

    def test_both_areas(self, tmp_path):
        """Creates pages in both systems and security areas."""
        (tmp_path / "content" / "systems").mkdir(parents=True)
        (tmp_path / "content" / "security").mkdir(parents=True)

        created = ensure_conference_pages(
            sys_dirs={"newsy2024"},
            sec_dirs={"newsec2024"},
            website_root=str(tmp_path),
        )
        assert len(created) == 2
        assert (tmp_path / "content" / "systems" / "newsy.md").exists()
        assert (tmp_path / "content" / "security" / "newsec.md").exists()

    def test_collapses_whitespace(self):
        assert normalize_name("  Jane   Doe  ") == "jane doe"
