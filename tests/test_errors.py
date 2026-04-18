"""Unit tests for :mod:`harbor_registry_mcp.errors`.

Verifies that every HTTP status we special-case produces an actionable
message that names :envvar:`HARBOR_URL` / :envvar:`HARBOR_USERNAME` /
:envvar:`HARBOR_PASSWORD` where appropriate and hints at a concrete next
step. Network failures are simulated via :mod:`responses`.
"""

from __future__ import annotations

import pytest
import requests
import responses

from harbor_registry_mcp.errors import ConfigError, handle


def _http_error(status: int, url: str = "https://harbor.example.com/api/v2.0/projects") -> requests.HTTPError:
    """Trigger a real ``requests.HTTPError`` carrying a response with ``status``."""
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, url, json={}, status=status)
        try:
            r = requests.get(url, timeout=5)
            r.raise_for_status()
        except requests.HTTPError as e:
            return e
    raise AssertionError(f"expected HTTPError for status {status}")  # pragma: no cover


class TestConfigError:
    def test_message_mentions_env_vars(self) -> None:
        msg = handle(ConfigError("HARBOR_URL is not set"), "listing projects")
        assert "configuration problem" in msg
        assert "listing projects" in msg
        assert "HARBOR_URL" in msg


class TestHttpStatusMapping:
    def test_401_mentions_robot_token(self) -> None:
        msg = handle(_http_error(401), "listing projects")
        assert "401" in msg
        assert "HARBOR_USERNAME" in msg
        assert "HARBOR_PASSWORD" in msg

    def test_403_mentions_scope(self) -> None:
        msg = handle(_http_error(403), "deleting artifact x")
        assert "403" in msg
        assert "robot account" in msg or "scope" in msg
        assert "deleting artifact x" in msg

    def test_404_suggests_discovery(self) -> None:
        msg = handle(_http_error(404), "listing repos")
        assert "404" in msg
        assert "harbor_list_projects" in msg or "harbor_list_repos" in msg

    def test_409_suggests_retry(self) -> None:
        msg = handle(_http_error(409), "deleting tag")
        assert "409" in msg
        assert "Retry" in msg or "retry" in msg

    def test_429_suggests_backoff(self) -> None:
        msg = handle(_http_error(429), "bulk delete")
        assert "429" in msg
        assert "Wait" in msg or "rate" in msg

    @pytest.mark.parametrize("code", [500, 502, 503, 504])
    def test_5xx_flags_transient(self, code: int) -> None:
        msg = handle(_http_error(code), "scan")
        assert str(code) in msg
        assert "transient" in msg or "health" in msg

    def test_unknown_4xx_includes_body_snippet(self) -> None:
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://harbor.example.com/api/v2.0/x",
                body="boom" * 100,
                status=418,
            )
            try:
                r = requests.get("https://harbor.example.com/api/v2.0/x", timeout=5)
                r.raise_for_status()
            except requests.HTTPError as e:
                msg = handle(e, "teapot call")
                assert "418" in msg
                assert "boom" in msg


class TestNetworkErrors:
    def test_connection_error_mentions_url_and_proxy(self) -> None:
        msg = handle(requests.ConnectionError("DNS fail"), "listing")
        assert "connect" in msg.lower()
        assert "HARBOR_URL" in msg
        assert "proxy" in msg

    def test_timeout_mentions_page_size(self) -> None:
        msg = handle(requests.Timeout("slow"), "listing artifacts")
        assert "timed out" in msg
        assert "page_size" in msg

    def test_unexpected_exception_fallthrough(self) -> None:
        msg = handle(RuntimeError("kaboom"), "something")
        assert "RuntimeError" in msg
        assert "kaboom" in msg
        assert "something" in msg
