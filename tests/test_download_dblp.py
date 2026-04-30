"""Tests for src.utils.download_dblp — DBLP downloader."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.utils.apis.download_dblp import (
    MIN_SIZE_MB,
    _is_up_to_date,
    _remote_last_modified,
    download_dblp,
)


class TestRemoteLastModified:
    def test_returns_float_on_success(self):
        mock_resp = MagicMock()
        mock_resp.headers = {"Last-Modified": "Wed, 01 Jan 2025 00:00:00 GMT"}
        with patch("src.utils.apis.download_dblp.requests.head", return_value=mock_resp):
            ts = _remote_last_modified("https://example.com/f.gz")
        assert isinstance(ts, float)
        assert ts > 0

    def test_returns_none_on_missing_header(self):
        mock_resp = MagicMock()
        mock_resp.headers = {}
        with patch("src.utils.apis.download_dblp.requests.head", return_value=mock_resp):
            assert _remote_last_modified("https://example.com/f.gz") is None

    def test_returns_none_on_connection_error(self):
        import requests

        with patch("src.utils.apis.download_dblp.requests.head", side_effect=requests.ConnectionError):
            assert _remote_last_modified("https://example.com/f.gz") is None


class TestIsUpToDate:
    def test_up_to_date(self, tmp_path):
        f = tmp_path / "dblp.xml.gz"
        f.write_bytes(b"x")
        # remote mtime = 1000, local mtime > 1000
        with patch("src.utils.apis.download_dblp._remote_last_modified", return_value=1000.0):
            assert _is_up_to_date(f) is True

    def test_outdated(self, tmp_path):
        f = tmp_path / "dblp.xml.gz"
        f.write_bytes(b"x")
        import os
        import time

        # Set local mtime far in the past
        os.utime(f, (100, 100))
        with patch("src.utils.apis.download_dblp._remote_last_modified", return_value=time.time()):
            assert _is_up_to_date(f) is False

    def test_unknown(self, tmp_path):
        f = tmp_path / "dblp.xml.gz"
        f.write_bytes(b"x")
        with patch("src.utils.apis.download_dblp._remote_last_modified", return_value=None):
            assert _is_up_to_date(f) is None


class TestDownloadDblp:
    @pytest.fixture(autouse=True)
    def _set_paths(self, tmp_path, monkeypatch):
        """Point module-level paths to tmp_path."""
        monkeypatch.setattr("src.utils.apis.download_dblp.DBLP_DIR", tmp_path)
        monkeypatch.setattr("src.utils.apis.download_dblp.DBLP_FILE", tmp_path / "dblp.xml.gz")

    def test_skips_download_when_up_to_date(self, tmp_path):
        f = tmp_path / "dblp.xml.gz"
        f.write_bytes(b"x" * (MIN_SIZE_MB << 20))  # fake valid size
        with patch("src.utils.apis.download_dblp._is_up_to_date", return_value=True):
            assert download_dblp(auto=True) is True

    def test_returns_false_on_connectivity_failure(self):
        import requests

        with patch("src.utils.apis.download_dblp.requests.head", side_effect=requests.ConnectionError):
            assert download_dblp(auto=True) is False

    def test_downloads_when_missing(self, tmp_path):
        big = b"x" * (MIN_SIZE_MB << 20)
        mock_head = MagicMock()
        mock_resp = MagicMock()
        mock_resp.headers = {"content-length": str(len(big))}
        mock_resp.iter_content = MagicMock(return_value=[big])
        mock_resp.raise_for_status = MagicMock()
        with (
            patch("src.utils.apis.download_dblp.requests.head", return_value=mock_head),
            patch("src.utils.apis.download_dblp.requests.get", return_value=mock_resp),
        ):
            assert download_dblp(auto=True) is True
        assert (tmp_path / "dblp.xml.gz").exists()

    def test_rejects_truncated_file(self, tmp_path):
        small = b"x" * 100
        mock_head = MagicMock()
        mock_resp = MagicMock()
        mock_resp.headers = {"content-length": str(len(small))}
        mock_resp.iter_content = MagicMock(return_value=[small])
        mock_resp.raise_for_status = MagicMock()
        with (
            patch("src.utils.apis.download_dblp.requests.head", return_value=mock_head),
            patch("src.utils.apis.download_dblp.requests.get", return_value=mock_resp),
        ):
            assert download_dblp(auto=True) is False
