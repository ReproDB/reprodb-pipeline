"""Tests for src.utils.http — shared HTTP session factory."""

from src.utils.apis.http import USER_AGENT, create_session


class TestCreateSession:
    def test_default_session_has_user_agent(self):
        session = create_session()
        assert session.headers["User-Agent"] == USER_AGENT

    def test_default_timeout_attribute(self):
        session = create_session()
        assert session.default_timeout == 30

    def test_custom_timeout(self):
        session = create_session(timeout=60)
        assert session.default_timeout == 60

    def test_extra_headers_merged(self):
        session = create_session(extra_headers={"Authorization": "Bearer tok"})
        assert session.headers["Authorization"] == "Bearer tok"
        assert session.headers["User-Agent"] == USER_AGENT

    def test_retry_adapter_mounted(self):
        session = create_session(retries=5)
        adapter = session.get_adapter("https://example.com")
        assert adapter.max_retries.total == 5

    def test_backoff_factor(self):
        session = create_session(backoff=2.0)
        adapter = session.get_adapter("https://example.com")
        assert adapter.max_retries.backoff_factor == 2.0

    def test_status_forcelist(self):
        session = create_session()
        adapter = session.get_adapter("https://example.com")
        assert 429 in adapter.max_retries.status_forcelist
        assert 503 in adapter.max_retries.status_forcelist
