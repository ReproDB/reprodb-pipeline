"""Shared HTTP session factory with retry and rate-limit support.

Usage::

    from src.utils.apis.http import create_session

    session = create_session()
    resp = session.get("https://api.example.com/data", timeout=session.default_timeout)
"""

from __future__ import annotations

import logging

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

#: User-Agent sent with every request.
USER_AGENT = "ReproDB-Pipeline/1.0 (https://github.com/reprodb/reprodb-pipeline)"


def create_session(
    *,
    retries: int = 3,
    backoff: float = 1.0,
    timeout: int = 30,
    extra_headers: dict[str, str] | None = None,
) -> requests.Session:
    """Create a :class:`requests.Session` with automatic retries.

    The returned session retries on 429/5xx with exponential back-off and
    stores a ``default_timeout`` attribute for convenience.
    """
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers["User-Agent"] = USER_AGENT
    if extra_headers:
        session.headers.update(extra_headers)
    session.default_timeout = timeout  # type: ignore[attr-defined]
    return session
