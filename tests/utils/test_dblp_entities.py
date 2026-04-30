"""Tests for DBLP XML entity resolution in dblp_extract.

Verifies that HTML entities used in the DBLP XML dump (e.g. &ouml; for ö)
are correctly resolved to Unicode characters, preventing name corruption
like "Jörg" → "Jrg".
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import lxml.etree as ET
import pytest

from src.utils.apis.dblp_extract import (
    _HTML_ENTITY_MAP,
    _INTERNAL_DOCTYPE,
    _PatchedDTDStream,
    extract_dblp,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_dblp_gz(xml_body: str, tmp_path: Path) -> str:
    """Create a gzipped DBLP XML file with the external DTD reference."""
    xml = f'<?xml version="1.0" encoding="ISO-8859-1"?>\n<!DOCTYPE dblp SYSTEM "dblp.dtd">\n<dblp>\n{xml_body}\n</dblp>'
    gz_path = tmp_path / "dblp.xml.gz"
    with gzip.open(gz_path, "wb") as f:
        f.write(xml.encode("iso-8859-1"))
    return str(gz_path)


def _parse_authors_from_gz(gz_path: str) -> list[str]:
    """Parse a gzipped DBLP XML using our PatchedDTDStream and return author names."""
    with gzip.open(gz_path, "rb") as raw:
        stream = _PatchedDTDStream(raw)
        authors = []
        for _, elem in ET.iterparse(
            stream,
            events=("end",),
            tag="www",
            load_dtd=True,
            recover=True,
            huge_tree=True,
        ):
            for a in elem.findall("author"):
                if a.text:
                    authors.append(a.text)
            elem.clear()
    return authors


# ---------------------------------------------------------------------------
# Entity map completeness
# ---------------------------------------------------------------------------


class TestEntityMapCompleteness:
    """Ensure _HTML_ENTITY_MAP covers all required entities."""

    # XML built-in entities that don't need to be in our map
    XML_BUILTINS = {"amp", "lt", "gt", "apos", "quot"}

    # All entities found in the DBLP XML dump (scraped with grep -oP '&[a-zA-Z]+;')
    DBLP_ENTITIES = {
        "AElig",
        "Aacute",
        "Acirc",
        "Agrave",
        "Aring",
        "Atilde",
        "Auml",
        "Ccedil",
        "ETH",
        "Eacute",
        "Ecirc",
        "Egrave",
        "Euml",
        "Iacute",
        "Icirc",
        "Igrave",
        "Iuml",
        "Ntilde",
        "Oacute",
        "Ocirc",
        "Ograve",
        "Oslash",
        "Otilde",
        "Ouml",
        "THORN",
        "Uacute",
        "Ucirc",
        "Ugrave",
        "Uuml",
        "Yacute",
        "aacute",
        "acirc",
        "aelig",
        "agrave",
        "aring",
        "atilde",
        "auml",
        "ccedil",
        "eacute",
        "ecirc",
        "egrave",
        "eth",
        "euml",
        "iacute",
        "icirc",
        "igrave",
        "iuml",
        "micro",
        "ntilde",
        "oacute",
        "ocirc",
        "ograve",
        "oslash",
        "otilde",
        "ouml",
        "reg",
        "szlig",
        "thorn",
        "times",
        "uacute",
        "ucirc",
        "ugrave",
        "uuml",
        "yacute",
        "yuml",
    }

    def test_all_dblp_entities_mapped(self):
        needed = self.DBLP_ENTITIES - self.XML_BUILTINS
        mapped = set(_HTML_ENTITY_MAP.keys())
        missing = needed - mapped
        assert not missing, f"Entities in DBLP but missing from _HTML_ENTITY_MAP: {missing}"

    def test_uses_stdlib_html_entities(self):
        """Verify we delegate to html.entities.name2codepoint (no manual map)."""
        import html.entities

        assert _HTML_ENTITY_MAP is html.entities.name2codepoint

    def test_entity_values_are_valid_unicode(self):
        for name, cp in _HTML_ENTITY_MAP.items():
            char = chr(cp)
            assert char, f"Entity {name} maps to invalid codepoint {cp:#x}"

    def test_internal_doctype_well_formed(self):
        assert _INTERNAL_DOCTYPE.startswith(b"<!DOCTYPE dblp [")
        assert _INTERNAL_DOCTYPE.endswith(b"]>")
        # Spot-check a few key entities
        for entity in ("ouml", "eacute", "ccedil", "ntilde", "auml"):
            assert f"<!ENTITY {entity}".encode() in _INTERNAL_DOCTYPE


# ---------------------------------------------------------------------------
# PatchedDTDStream
# ---------------------------------------------------------------------------


class TestPatchedDTDStream:
    """Verify that _PatchedDTDStream correctly replaces the external DTD."""

    def test_replaces_external_dtd(self, tmp_path):
        gz_path = _make_dblp_gz("<www/>", tmp_path)
        with gzip.open(gz_path, "rb") as raw:
            stream = _PatchedDTDStream(raw)
            content = stream.read()
        assert b'SYSTEM "dblp.dtd"' not in content
        assert b"<!DOCTYPE dblp [" in content

    def test_read_in_chunks(self, tmp_path):
        """Verify chunked reading reassembles correctly."""
        gz_path = _make_dblp_gz("<www/>", tmp_path)
        with gzip.open(gz_path, "rb") as raw:
            stream = _PatchedDTDStream(raw)
            chunks = []
            while True:
                chunk = stream.read(64)
                if not chunk:
                    break
                chunks.append(chunk)
        full = b"".join(chunks)
        assert b"<!DOCTYPE dblp [" in full
        assert b"</dblp>" in full


# ---------------------------------------------------------------------------
# End-to-end entity resolution
# ---------------------------------------------------------------------------


class TestEntityResolution:
    """Test that HTML entities in author names are resolved to correct Unicode."""

    # German umlauts & Eszett
    @pytest.mark.parametrize(
        "entity,expected",
        [
            ("J&ouml;rg Schwenk", "Jörg Schwenk"),
            ("Fabian B&auml;umer", "Fabian Bäumer"),
            ("Hans M&uuml;ller", "Hans Müller"),
            ("Ren&eacute; M&uuml;ller", "René Müller"),
            ("G&uuml;nter Gro&szlig;", "Günter Groß"),
        ],
        ids=["ouml", "auml", "uuml", "eacute+uuml", "szlig"],
    )
    def test_german_names(self, tmp_path, entity, expected):
        xml_body = f'<www key="test"><author>{entity}</author></www>'
        gz_path = _make_dblp_gz(xml_body, tmp_path)
        authors = _parse_authors_from_gz(gz_path)
        assert authors == [expected]

    # French accented characters
    @pytest.mark.parametrize(
        "entity,expected",
        [
            ("Ren&eacute; Dupont", "René Dupont"),
            ("Fran&ccedil;ois Larose", "François Larose"),
            ("J&eacute;r&ocirc;me Qu&eacute;vremont", "Jérôme Quévremont"),
            ("H&egrave;l&egrave;ne Duval", "Hèlène Duval"),
            ("No&euml;lle Martin", "Noëlle Martin"),
            ("S&eacute;bastien Andr&eacute;", "Sébastien André"),
        ],
        ids=["eacute", "ccedil", "eacute+ocirc", "egrave", "euml", "multi-eacute"],
    )
    def test_french_names(self, tmp_path, entity, expected):
        xml_body = f'<www key="test"><author>{entity}</author></www>'
        gz_path = _make_dblp_gz(xml_body, tmp_path)
        authors = _parse_authors_from_gz(gz_path)
        assert authors == [expected]

    # Spanish
    @pytest.mark.parametrize(
        "entity,expected",
        [
            ("Jos&eacute; Antonio S&aacute;nchez", "José Antonio Sánchez"),
            ("Jes&uacute;s Garc&iacute;a", "Jesús García"),
            ("Carlos Espa&ntilde;ol", "Carlos Español"),
        ],
        ids=["eacute+aacute", "uacute+iacute", "ntilde"],
    )
    def test_spanish_names(self, tmp_path, entity, expected):
        xml_body = f'<www key="test"><author>{entity}</author></www>'
        gz_path = _make_dblp_gz(xml_body, tmp_path)
        authors = _parse_authors_from_gz(gz_path)
        assert authors == [expected]

    # Scandinavian
    @pytest.mark.parametrize(
        "entity,expected",
        [
            ("Nils H&ouml;glund", "Nils Höglund"),
            ("Lars &Oslash;stergaard", "Lars Østergaard"),
            ("Bj&ouml;rn &Aring;kesson", "Björn Åkesson"),
        ],
        ids=["ouml", "Oslash", "ouml+Aring"],
    )
    def test_scandinavian_names(self, tmp_path, entity, expected):
        xml_body = f'<www key="test"><author>{entity}</author></www>'
        gz_path = _make_dblp_gz(xml_body, tmp_path)
        authors = _parse_authors_from_gz(gz_path)
        assert authors == [expected]

    # Portuguese / Brazilian
    @pytest.mark.parametrize(
        "entity,expected",
        [
            ("Gon&ccedil;alves", "Gonçalves"),
            ("Jo&atilde;o Silva", "João Silva"),
            ("Lu&iacute;s &Aacute;lvarez", "Luís Álvarez"),
        ],
        ids=["ccedil", "atilde", "iacute+Aacute"],
    )
    def test_portuguese_names(self, tmp_path, entity, expected):
        xml_body = f'<www key="test"><author>{entity}</author></www>'
        gz_path = _make_dblp_gz(xml_body, tmp_path)
        authors = _parse_authors_from_gz(gz_path)
        assert authors == [expected]

    # Multiple authors in one entry
    def test_multiple_authors_resolved(self, tmp_path):
        xml_body = (
            '<www key="test">'
            "<author>J&ouml;rg Schwenk</author>"
            "<author>Ren&eacute; Dupont</author>"
            "<author>Jos&eacute; Garc&iacute;a</author>"
            "</www>"
        )
        gz_path = _make_dblp_gz(xml_body, tmp_path)
        authors = _parse_authors_from_gz(gz_path)
        assert authors == ["Jörg Schwenk", "René Dupont", "José García"]

    # Paper titles with entities
    def test_paper_title_entities(self, tmp_path):
        xml_body = (
            '<inproceedings key="conf/test/Example23" mdate="2023-01-01">'
            "<author>J&ouml;rg Schwenk</author>"
            "<title>Caf&eacute; Verification: A Sch&ouml;n Approach.</title>"
            "<booktitle>USENIX Security Symposium</booktitle>"
            "<year>2023</year>"
            "</inproceedings>"
        )
        gz_path = _make_dblp_gz(xml_body, tmp_path)
        with gzip.open(gz_path, "rb") as raw:
            stream = _PatchedDTDStream(raw)
            titles = []
            authors = []
            for _, elem in ET.iterparse(
                stream,
                events=("end",),
                tag="inproceedings",
                load_dtd=True,
                recover=True,
                huge_tree=True,
            ):
                t = elem.findtext("title") or ""
                titles.append(t)
                for a in elem.findall("author"):
                    if a.text:
                        authors.append(a.text)
                elem.clear()
        assert authors == ["Jörg Schwenk"]
        assert titles == ["Café Verification: A Schön Approach."]


# ---------------------------------------------------------------------------
# Full extract_dblp integration
# ---------------------------------------------------------------------------


class TestExtractDblpIntegration:
    """Integration test: extract_dblp writes correct JSON with resolved entities."""

    def test_extract_preserves_unicode_in_json(self, tmp_path, monkeypatch):
        # Create a fake DBLP XML with entities
        xml_body = (
            '<www key="homepages/s/Schwenk">'
            "<author>J&ouml;rg Schwenk</author>"
            '<note type="affiliation">Ruhr Universit&auml;t Bochum</note>'
            "</www>"
            '<inproceedings key="conf/uss/Schwenk23" mdate="2023-01-01">'
            "<author>J&ouml;rg Schwenk</author>"
            "<author>Fabian B&auml;umer 0001</author>"
            "<title>Terrapin Attack.</title>"
            "<booktitle>USENIX Security Symposium</booktitle>"
            "<year>2024</year>"
            "</inproceedings>"
        )
        gz_path = _make_dblp_gz(xml_body, tmp_path)

        # Point extract_dblp to use tmp_path for cache
        monkeypatch.setattr(
            "src.utils.apis.dblp_extract._extract_dir",
            lambda repo_root=None: str(tmp_path / "cache"),
        )

        papers_path, affiliations_path = extract_dblp(gz_path)

        # Verify affiliations JSON
        with open(affiliations_path, encoding="utf-8") as f:
            affiliations = json.load(f)
        assert "Jörg Schwenk" in affiliations
        assert affiliations["Jörg Schwenk"] == "Ruhr Universität Bochum"

        # Verify papers JSON
        with open(papers_path, encoding="utf-8") as f:
            papers = json.load(f)
        usenixsec_papers = papers.get("USENIXSEC", {}).get("2024", [])
        assert len(usenixsec_papers) == 1
        assert "Jörg Schwenk" in usenixsec_papers[0]["authors"]
        assert "Fabian Bäumer 0001" in usenixsec_papers[0]["authors"]
